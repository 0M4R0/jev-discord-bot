"""Core moderation engine: Jev evaluation + progressive escalation."""

from __future__ import annotations

import datetime
import logging

import discord
from discord import ui

from database import Database, GuildSettings
from typesafe import AsyncTypeSafe, Choice, Noul

logger = logging.getLogger("moderator")


def build_state(
    message: discord.Message,
    recent_false_flags: list[str] | None = None,
) -> str:
    """Build structured state text for Jev."""
    now = datetime.datetime.now(datetime.timezone.utc)
    account_age_days = (now - message.author.created_at).days
    has_link = (
        "http://" in message.content.lower() or "https://" in message.content.lower()
    )
    channel_name = getattr(message.channel, "name", "unknown")

    lines = [
        "=== DISCORD MESSAGE CONTEXT ===",
        f"Author ID: {message.author.id}",
        f"Account Age (Days): {account_age_days}",
        f"Has External Link: {has_link}",
        f"Channel: #{channel_name}",
        "",
        "=== MESSAGE CONTENT ===",
        message.content[:2000],
    ]

    if recent_false_flags:
        lines.append("")
        lines.append("=== COMMUNITY VERIFIED SAFE PRECEDENTS ===")
        lines.append(
            "These messages were previously flagged but confirmed legitimate by admins. "
            "Treat similar content as safe."
        )
        for i, flag in enumerate(recent_false_flags[:5], 1):
            clean = flag.replace("\n", " ").strip()[:150]
            lines.append(f"{i}. {clean}")

    return "\n".join(lines)


class ConfirmView(ui.View):
    """Two-step ephemeral confirmation for admin actions."""

    def __init__(self, action: str, timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.action = action
        self.confirmed = False
        self.value: bool | None = None

    @ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        self.confirmed = True
        self.value = True
        self.stop()
        await interaction.response.edit_message(
            content=f"✅ {self.action} confirmed.", view=None
        )

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button) -> None:
        self.value = False
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None)


class ModLogView(ui.View):
    """Admin buttons on mod-log embeds: Pardon / Ban."""

    def __init__(
        self,
        moderator: MessageModerator,
        guild_id: int,
        user_id: int,
        message_content: str,
        offense_id: int,
    ):
        super().__init__(timeout=None)  # persistent
        self.moderator = moderator
        self.guild_id = guild_id
        self.user_id = user_id
        self.message_content = message_content
        self.offense_id = offense_id

    # Check if the user using this command is able to moderate members
    def check_admin(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        return (
            interaction.guild is not None
            and isinstance(member, discord.Member)
            and member.guild_permissions.moderate_members
        )

    @ui.button(
        label="🟢 Pardon (False Flag)",
        style=discord.ButtonStyle.success,
        custom_id="pardon",
    )
    async def pardon(self, interaction: discord.Interaction, button: ui.Button) -> None:
        # Check if the user is an admin
        if not self.check_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return

        # Parse offense_id from custom_id
        data = interaction.data
        if data is None or "custom_id" not in data:
            await interaction.response.send_message(
                "Invalid button data.", ephemeral=True
            )
            return

        try:
            offense_id = int(data["custom_id"].split(":")[-1])
        except (ValueError, IndexError, TypeError):
            await interaction.response.send_message(
                "Invalid button data.", ephemeral=True
            )
            return

        # Confirmation
        view = ConfirmView("Pardon")
        await interaction.response.send_message(
            "Confirm pardon? This marks the message as a safe precedent for Jev.",
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if not view.confirmed:
            return

        # Get offense
        offense = await self.moderator.db.get_offense(offense_id)
        if not offense or offense.status != "ACTIVE":
            await interaction.followup.send("No active offense found.", ephemeral=True)
            return

        # Process pardon
        await self.moderator.db.pardon_offense(offense_id)
        await self.moderator.db.add_false_flag(
            offense.guild_id, offense.message_content
        )

        # Lift timeout if active
        guild = interaction.guild
        if not guild:
            return

        try:
            member = guild.get_member(offense.user_id)
            if member and member.timed_out_until:
                try:
                    await member.timeout(None, reason="Pardoned false flag")
                except discord.HTTPException:
                    pass

            # Handle followup response
            await interaction.followup.send(
                f"Pardoned. Safe precedent added for Jev. Offense #{offense.id}.",
                ephemeral=True,
            )

            # Update original embed
            try:
                message = interaction.message
                if message and message.embeds:
                    embed = message.embeds[0]
                    embed.color = discord.Color.green()
                    embed.add_field(
                        name="Resolution", value="🟢 PARDONED", inline=False
                    )
                    await message.edit(embed=embed, view=None)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        except discord.HTTPException:
            pass

    @ui.button(label="🔴 Ban User", style=discord.ButtonStyle.danger, custom_id="ban")
    async def ban(self, interaction: discord.Interaction, button: ui.Button) -> None:
        # Check if the user is an admin
        if not self.check_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return

        # Parse offense_id from custom_id
        data = interaction.data
        if data is None or "custom_id" not in data:
            await interaction.response.send_message(
                "Invalid button data.", ephemeral=True
            )
            return

        try:
            offense_id = int(data["custom_id"].split(":")[-1])
        except (ValueError, IndexError, TypeError):
            await interaction.response.send_message(
                "Invalid button data.", ephemeral=True
            )
            return

        # Confirmation
        view = ConfirmView("Ban")
        await interaction.response.send_message(
            "Confirm permanent ban?",
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if not view.confirmed:
            return

        # Get offense
        offense = await self.moderator.db.get_offense(offense_id)
        if not offense or offense.status != "ACTIVE":
            await interaction.followup.send("No active offense found.", ephemeral=True)
            return

        # Process ban
        guild = interaction.guild
        if not guild:
            return

        try:
            member = guild.get_member(offense.user_id)
            if member:
                await member.ban(reason="Escalated from moderation bot")
            else:
                await guild.ban(
                    discord.Object(id=offense.user_id),
                    reason="Escalated from moderation bot",
                )
            await self.moderator.db.mark_banned(offense.guild_id, offense.user_id)
            await interaction.followup.send("User banned.", ephemeral=True)

            # Update original embed if possible
            try:
                message = interaction.message
                if message and message.embeds:
                    embed = message.embeds[0]
                    embed.color = discord.Color.dark_red()
                    embed.add_field(name="Resolution", value="🔴 BANNED", inline=False)
                    await message.edit(embed=embed, view=None)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        except discord.Forbidden:
            await interaction.followup.send(
                "Missing Ban Members permission.", ephemeral=True
            )
        except discord.HTTPException as e:
            await interaction.followup.send(f"Ban failed: {e}", ephemeral=True)


class MessageModerator:
    def __init__(self, client: AsyncTypeSafe, db: Database) -> None:
        self.client = client
        self.db = db

    async def evaluate_message(
        self, message: discord.Message, settings: GuildSettings
    ) -> dict | None:
        """Run Jev and return threat decision or None if clean."""
        if message.author.bot or not message.guild or not message.content.strip():
            return None

        recent_flags = await self.db.get_recent_false_flags(message.guild.id)
        state = build_state(message, recent_flags)

        questions = {
            "threat_level": Choice(
                instructions=(
                    "Classify this Discord message for moderation. "
                    "Consider phishing, scam links, social engineering, spam, malware invites, "
                    "or clear malicious intent. Account age and links are signals, not proof. "
                    "Respect community-verified safe precedents."
                ),
                criteria={
                    "tier1": "Clear high-confidence threat (phishing, scam, malware, social engineering)",
                    "tier2": "Suspicious / medium confidence (possible spam or borderline social engineering)",
                    "legitimate": "Normal, harmless, or matches verified safe precedents",
                },
            ),
            "is_phishing": Noul(
                instructions="Does this message attempt phishing, credential theft, or financial scam?"
            ),
        }

        try:
            result = await self.client.evaluate(state=state, questions=questions)
        except Exception as e:
            logger.error("TypeSafe evaluation failed: %s", e)
            return None

        threat = result.answers.get("threat_level")
        phishing = result.answers.get("is_phishing")

        if threat is None or threat.choice is None:
            return None

        choice = threat.choice
        conf = threat.confidence or 0.0
        phishing_score = (
            phishing.noul if phishing and phishing.noul is not None else 0.0
        )

        logger.info(
            "Jev decision for message %s: choice=%s confidence=%.3f "
            "phishing=%.3f thresholds=(tier1 %.2f, tier2 %.2f)",
            message.id,
            choice,
            conf,
            phishing_score,
            settings.tier1_threshold,
            settings.tier2_threshold,
        )

        # Apply configurable thresholds
        if choice == "tier1" and conf >= settings.tier1_threshold:
            return {
                "tier": 1,
                "choice": choice,
                "confidence": conf,
                "phishing": phishing_score,
                "raw": result,
            }
        if choice == "tier2" and conf >= settings.tier2_threshold:
            return {
                "tier": 2,
                "choice": choice,
                "confidence": conf,
                "phishing": phishing_score,
                "raw": result,
            }
        # Also escalate pure high phishing noul even if choice drifted
        if phishing_score >= 0.86:
            return {
                "tier": 1 if phishing_score >= 0.95 else 2,
                "choice": "tier1" if phishing_score >= 0.95 else "tier2",
                "confidence": max(conf, phishing_score),
                "phishing": phishing_score,
                "raw": result,
            }
        return None

    async def handle_offense(
        self,
        message: discord.Message,
        decision: dict,
        settings: GuildSettings,
    ) -> None:
        """Delete, escalate, DM, log."""
        guild = message.guild
        if not guild:
            return

        is_guild_owner = message.author.id == guild.owner_id

        # Guild owner is completely exempt from moderation
        if is_guild_owner:
            logger.info(
                "Ignoring moderation offense from guild owner %s",
                message.author.id,
            )
            return

        logger.info(
            "Handling offense for message %s from user %s: tier=%s confidence=%.3f",
            message.id,
            message.author.id,
            decision.get("tier"),
            decision.get("confidence", 0.0),
        )

        # Delete message
        try:
            await message.delete()
            # Log the deletion
            logger.info(
                "Deleted message %s: Jev threat=%s confidence=%.2f",
                message.id,
                decision["choice"],
                decision["confidence"],
            )
        except discord.Forbidden:
            logger.warning(
                "Cannot delete message %s: missing Manage Messages permission",
                message.id,
            )
        except discord.HTTPException as e:
            logger.warning("Failed to delete message %s: %s", message.id, e)

        count = await self.db.count_active_offenses(guild.id, message.author.id)
        next_count = count + 1

        action = "WARN_1_DM"
        timeout_mins = 0
        if next_count == 1:
            action = "WARN_1_DM"
        elif next_count == 2:
            action = "WARN_2_DM"
        elif next_count == 3:
            action = "TIMEOUT_FIRST"
            timeout_mins = settings.first_timeout_mins
        else:
            action = "TIMEOUT_SUBSEQUENT"
            timeout_mins = settings.subsequent_timeout_mins

        offense_id = await self.db.add_offense(
            guild_id=guild.id,
            user_id=message.author.id,
            message_content=message.content[:500],
            channel_id=message.channel.id,
            action=action,
            confidence=decision["confidence"],
        )

        # Apply timeout if needed
        if timeout_mins > 0:
            author = guild.get_member(message.author.id)

            # Check if the user is a member of this guild
            if author is None:
                logger.warning(
                    "Cannot timeout %s: user is not a member of this guild",
                    message.author.id,
                )
                return

            bot_member = guild.me
            if bot_member is None:
                logger.warning(
                    "Cannot timeout %s: bot member is unavailable",
                    author.id,
                )
            elif author.top_role >= bot_member.top_role:
                logger.warning(
                    "Cannot timeout %s: user role %s is equal to or higher than bot role %s",
                    author.id,
                    author.top_role.name,
                    bot_member.top_role.name,
                )
            else:
                try:
                    delta = datetime.timedelta(minutes=timeout_mins)
                    await author.timeout(
                        delta,
                        reason=f"Moderation escalation (offense #{next_count})",
                    )
                except discord.Forbidden:
                    logger.warning(
                        "Cannot timeout %s: missing Moderate Members permission",
                        author.id,
                    )
                except discord.HTTPException as e:
                    logger.warning("Timeout failed: %s", e)

        # DM user
        dm_text = self._build_dm(next_count, timeout_mins, decision)
        dm_ok = False
        try:
            await message.author.send(dm_text)
            dm_ok = True
        except discord.Forbidden:
            logger.warning(
                "Could not DM user %s: DMs are disabled or blocked",
                message.author.id,
            )
        except discord.HTTPException as e:
            logger.warning(
                "Could not DM user %s: %s",
                message.author.id,
                e,
            )

        # Mod-log embed
        if settings.mod_log_channel_id:
            channel = guild.get_channel(settings.mod_log_channel_id)
            if channel and isinstance(channel, discord.TextChannel):
                embed = discord.Embed(
                    title="🛡️ Moderation Action",
                    color=discord.Color.orange()
                    if next_count < 3
                    else discord.Color.red(),
                    timestamp=datetime.datetime.now(datetime.timezone.utc),
                )
                embed.add_field(
                    name="User",
                    value=f"{message.author.mention} (`{message.author.id}`)",
                    inline=True,
                )
                embed.add_field(name="Offense #", value=str(next_count), inline=True)
                embed.add_field(name="Action", value=action, inline=True)
                embed.add_field(
                    name="Confidence",
                    value=f"{decision['confidence']:.2f}",
                    inline=True,
                )
                embed.add_field(
                    name="Phishing score",
                    value=f"{decision.get('phishing', 0):.2f}",
                    inline=True,
                )
                channel_value = (
                    message.channel.mention
                    if isinstance(message.channel, discord.abc.GuildChannel)
                    else "DM"
                )
                embed.add_field(name="Channel", value=channel_value, inline=True)
                embed.add_field(
                    name="Content",
                    value=f"```{message.content[:800]}```"
                    if message.content
                    else "*empty*",
                    inline=False,
                )
                embed.add_field(
                    name="DM delivered",
                    value="Yes" if dm_ok else "No (DMs closed)",
                    inline=True,
                )
                embed.set_footer(text=f"Offense ID {offense_id}")

                view = ModLogView(
                    self,
                    guild.id,
                    message.author.id,
                    message.content[:500],
                    offense_id,
                )
                view.pardon.custom_id = f"modlog:pardon:{offense_id}"
                view.ban.custom_id = f"modlog:ban:{offense_id}"
                try:
                    await channel.send(embed=embed, view=view)
                except discord.HTTPException as e:
                    logger.warning("Mod-log send failed: %s", e)

    def _build_dm(self, offense_num: int, timeout_mins: int, decision: dict) -> str:
        base = (
            f"**Automated moderation notice**\n\n"
            f"Your message was removed (offense #{offense_num}).\n"
            f"Detected threat confidence: {decision['confidence']:.0%}."
        )
        if offense_num == 1:
            return base + "\n\nThis is a warning. Further violations will escalate."
        if offense_num == 2:
            return (
                base + "\n\n**Final warning.** Next offense will result in a timeout."
            )
        if timeout_mins:
            return base + f"\n\nYou have been timed out for **{timeout_mins} minutes**."
        return base
