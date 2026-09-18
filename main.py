"""Discord moderation bot powered by TypeSafe Jev (System One)."""

from __future__ import annotations

import csv
import io
import json
import logging
import sys
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from database import db_instance
from moderator import MessageModerator
from typesafe import AsyncTypeSafe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("main")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=config.COMMAND_PREFIX, intents=intents)
typesafe_client = AsyncTypeSafe(api_key=config.TYPESAFE_API_KEY)
moderator = MessageModerator(client=typesafe_client, db=db_instance)


@bot.event
async def on_ready() -> None:
    await db_instance.connect()
    logger.info("Logged in as %s (%s)", bot.user, bot.user.id if bot.user else "?")
    try:
        synced = await bot.tree.sync()
        logger.info("Synced %d slash commands", len(synced))
    except Exception as e:
        logger.error("Command sync failed: %s", e)


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot or not message.guild:
        return

    settings = await db_instance.get_guild_settings(message.guild.id)
    decision = await moderator.evaluate_message(message, settings)
    if decision:
        await moderator.handle_offense(message, decision, settings)
        return  # do not process as command

    await bot.process_commands(message)


# ---------- Slash commands ----------

@bot.tree.command(name="set-mod-log", description="Set the channel for moderation alerts.")
@app_commands.describe(channel="The #mod-log channel")
@app_commands.checks.has_permissions(administrator=True)
async def set_mod_log(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if not interaction.guild:
        return
    perms = channel.permissions_for(interaction.guild.me)
    if not (perms.view_channel and perms.send_messages and perms.embed_links):
        await interaction.response.send_message(
            "I need View Channel, Send Messages, and Embed Links in that channel.",
            ephemeral=True,
        )
        return
    settings = await db_instance.get_guild_settings(interaction.guild.id)
    settings.mod_log_channel_id = channel.id
    await db_instance.save_guild_settings(settings)
    await interaction.response.send_message(f"Mod-log set to {channel.mention}", ephemeral=True)


@bot.tree.command(name="unset-mod-log", description="Remove the mod-log channel.")
@app_commands.checks.has_permissions(administrator=True)
async def unset_mod_log(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    settings = await db_instance.get_guild_settings(interaction.guild.id)
    settings.mod_log_channel_id = None
    await db_instance.save_guild_settings(settings)
    await interaction.response.send_message("Mod-log unset. Actions still go to Server Audit Log.", ephemeral=True)


@bot.tree.command(name="set-timeouts", description="Set timeout durations for escalations.")
@app_commands.describe(
    first_offense_mins="Timeout minutes on 3rd offense (default 10)",
    subsequent_offense_mins="Timeout minutes on 4th+ offenses (default 60)",
)
@app_commands.checks.has_permissions(administrator=True)
async def set_timeouts(
    interaction: discord.Interaction,
    first_offense_mins: app_commands.Range[int, 1, 10080],
    subsequent_offense_mins: app_commands.Range[int, 1, 10080],
) -> None:
    if not interaction.guild:
        return
    settings = await db_instance.get_guild_settings(interaction.guild.id)
    settings.first_timeout_mins = first_offense_mins
    settings.subsequent_timeout_mins = subsequent_offense_mins
    await db_instance.save_guild_settings(settings)
    await interaction.response.send_message(
        f"Timeouts updated: 3rd={first_offense_mins}m, 4th+={subsequent_offense_mins}m",
        ephemeral=True,
    )


@bot.tree.command(name="set-thresholds", description="Adjust Jev confidence thresholds.")
@app_commands.describe(
    tier1="High-confidence threat threshold (default 0.95)",
    tier2="Medium-confidence threat threshold (default 0.70)",
)
@app_commands.checks.has_permissions(administrator=True)
async def set_thresholds(
    interaction: discord.Interaction,
    tier1: app_commands.Range[float, 0.5, 1.0],
    tier2: app_commands.Range[float, 0.3, 1.0],
) -> None:
    if not interaction.guild:
        return
    if tier2 > tier1:
        await interaction.response.send_message("tier2 must be ≤ tier1", ephemeral=True)
        return
    settings = await db_instance.get_guild_settings(interaction.guild.id)
    settings.tier1_threshold = tier1
    settings.tier2_threshold = tier2
    await db_instance.save_guild_settings(settings)
    await interaction.response.send_message(
        f"Thresholds set: tier1={tier1:.2f}, tier2={tier2:.2f}",
        ephemeral=True,
    )


@bot.tree.command(name="user-offenses", description="Show a member's infraction history.")
@app_commands.describe(user="The member to inspect")
@app_commands.checks.has_permissions(moderate_members=True)
async def user_offenses(interaction: discord.Interaction, user: discord.Member) -> None:
    if not interaction.guild:
        return
    offenses = await db_instance.get_user_offenses(interaction.guild.id, user.id)
    if not offenses:
        await interaction.response.send_message(f"No recorded offenses for {user.mention}.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"Offenses for {user.display_name}",
        color=discord.Color.blue(),
    )
    for o in offenses[:15]:
        status_emoji = {"ACTIVE": "🔴", "PARDONED": "🟢", "BANNED": "⚫"}.get(o.status, "⚪")
        embed.add_field(
            name=f"{status_emoji} #{o.id} · {o.action} · {o.status}",
            value=f"`{o.created_at[:19]}` · conf={o.confidence:.2f}\n```{o.message_content[:120]}```",
            inline=False,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="pardon", description="Pardon a member's latest active offense.")
@app_commands.describe(user="Member to pardon")
@app_commands.checks.has_permissions(administrator=True)
async def pardon_user(interaction: discord.Interaction, user: discord.Member) -> None:
    if not interaction.guild:
        return
    offense = await db_instance.pardon_latest(interaction.guild.id, user.id)
    if not offense:
        await interaction.response.send_message("No active offense to pardon.", ephemeral=True)
        return
    await db_instance.add_false_flag(interaction.guild.id, offense.message_content)
    if user.timed_out_until:
        try:
            await user.timeout(None, reason="Manual pardon")
        except discord.HTTPException:
            pass
    await interaction.response.send_message(
        f"Pardoned offense #{offense.id}. Added as safe precedent for Jev.",
        ephemeral=True,
    )


@bot.tree.command(name="export-feedback", description="Export false flags & threats as JSON or CSV.")
@app_commands.describe(file_format="json or csv")
@app_commands.checks.has_permissions(administrator=True)
async def export_feedback(
    interaction: discord.Interaction,
    file_format: Literal["json", "csv"] = "json",
) -> None:
    if not interaction.guild:
        return
    rows = await db_instance.export_feedback(interaction.guild.id)
    if not rows:
        await interaction.response.send_message("No data to export.", ephemeral=True)
        return

    if file_format == "json":
        data = json.dumps(rows, indent=2)
        fp = io.BytesIO(data.encode("utf-8"))
        file = discord.File(fp, filename="feedback.json")
    else:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
        fp = io.BytesIO(buf.getvalue().encode("utf-8"))
        file = discord.File(fp, filename="feedback.csv")

    await interaction.response.send_message("Export ready.", file=file, ephemeral=True)


@bot.tree.command(name="mod-config", description="Show current moderation settings.")
@app_commands.checks.has_permissions(administrator=True)
async def mod_config(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    settings = await db_instance.get_guild_settings(interaction.guild.id)
    flags = await db_instance.get_recent_false_flags(interaction.guild.id, limit=20)
    log_ch = f"<#{settings.mod_log_channel_id}>" if settings.mod_log_channel_id else "Not set"

    embed = discord.Embed(title="Moderation Config", color=discord.Color.blurple())
    embed.add_field(name="Mod-log channel", value=log_ch, inline=False)
    embed.add_field(name="Tier1 threshold", value=f"{settings.tier1_threshold:.2f}", inline=True)
    embed.add_field(name="Tier2 threshold", value=f"{settings.tier2_threshold:.2f}", inline=True)
    embed.add_field(name="3rd offense timeout", value=f"{settings.first_timeout_mins}m", inline=True)
    embed.add_field(name="4th+ timeout", value=f"{settings.subsequent_timeout_mins}m", inline=True)
    embed.add_field(
        name="Jev in-context memory",
        value=f"{len(flags)} active safe precedent(s)",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("Missing permissions.", ephemeral=True)
    else:
        logger.exception("Command error: %s", error)
        if not interaction.response.is_done():
            await interaction.response.send_message("Something went wrong.", ephemeral=True)


def main() -> None:
    if not config.DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN missing in .env")
        sys.exit(1)
    if not config.TYPESAFE_API_KEY:
        logger.error("TYPESAFE_API_KEY missing in .env")
        sys.exit(1)
    bot.run(config.DISCORD_TOKEN)


if __name__ == "__main__":
    main()
