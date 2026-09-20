# Jev Moderation Bot

IT works, just in case

Real-time Discord moderation powered by **TypeSafe AI System One (Jev)**.

Detects phishing, spam, and social engineering. Progressive 4-stage escalation. False-flag memory that feeds back into Jev as in-context learning.

## Features

- **Jev decision engine** — structured Choice + Noul evaluation with calibrated confidence
- **4-stage ladder**: Warn → Final warn → 10m timeout → 1h timeout (configurable)
- **False-flag memory** — admin pardons become safe precedents injected into every future evaluation
- **Dual logging** — Discord audit log + rich #mod-log embeds with Pardon / Ban buttons
- **Slash commands** for config, history, export

## Setup

### 1. Keys

- Discord bot token: https://discord.com/developers/applications  
  Enable **Message Content Intent** and **Server Members Intent**.
- TypeSafe API key: https://console.typesafe.ai/settings/keys

### 2. Install

```bash
git clone <your-repo-url>
cd jev-mod-bot
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with your tokens
```

### 3. Invite the bot

OAuth2 URL Generator → scopes: `bot`, `applications.commands`  
Permissions: Manage Messages, Moderate Members, Ban Members, View Channels, Send Messages, Embed Links, Read Message History.

### 4. Run

```bash
python main.py
```

## Commands (Administrator unless noted)

| Command            | Description                                |
| ------------------ | ------------------------------------------ |
| `/set-mod-log`     | Set the alert channel                      |
| `/unset-mod-log`   | Clear it                                   |
| `/set-timeouts`    | Change 3rd / 4th+ timeout lengths          |
| `/set-thresholds`  | Change Jev confidence cutoffs              |
| `/user-offenses`   | View a member's history (Moderate Members) |
| `/timeout-user`    | Timeout a member (Moderate Members)        |
| `/pardon`          | Pardon + add safe precedent                |
| `/export-feedback` | Download JSON/CSV of flags for analysis    |
| `/mod-config`      | Current settings + memory size             |

## How Jev is used

Every message is turned into a structured state (account age, link presence, channel, content, + recent pardoned messages).

Jev answers two questions in parallel:

1. **Choice** — `tier1` / `tier2` / `legitimate`
2. **Noul** — is this phishing / scam?

Thresholds decide whether to act. Pardoned messages are re-injected so the model adapts to your community without retraining.

## License

MIT
