# Squadlock Bot

[![CI](https://github.com/YuriBrX/squadlock-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/YuriBrX/squadlock-bot/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.6%2B-5865F2?logo=discord&logoColor=white)](https://discordpy.readthedocs.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Discord bot for creating and tracking persistent Squadlock cards for special
vehicles. Each card records a time-to-live (TTL), supports renewal and closure,
and can alert `@everyone`, selected roles, individual subscribers, or nobody as
expiration approaches.

## Features

- `/squadlock post` creates a Components V2 card for a vehicle.
- Configurable TTL, with a 48-hour default.
- Alert modes for `@everyone`, up to five selected roles, or no general mention.
- **Warn me** subscriptions for individual users.
- Persistent **Renew**, **Warn me**, **Squadlock TTL**, and **Close** buttons.
- Alerts shortly before expiration and a final expiration notification.
- JSON-backed state that survives normal bot restarts.
- Message context-menu actions for closing a card and changing its TTL.
- Recovery utility that rebuilds state from recent Discord messages.
- Optional guild or global slash-command synchronization.
- Discloud configuration included.

## Requirements

- Python 3.10 or newer
- A Discord application and bot token
- A server where you can install the bot
- `discord.py` 2.6 or newer for Components V2

The bot generally needs these channel permissions:

- View Channel
- Send Messages
- Read Message History
- Embed Links
- Use Application Commands
- Mention Everyone, only when using the `@everyone` alert mode
- Manage Messages, if moderators will close and delete cards

## Quick start

### 1. Create and invite a Discord bot

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Open **Bot**, create the bot user, and copy its token.
3. In **OAuth2 > URL Generator**, select `bot` and `applications.commands`.
4. Select the permissions listed above and invite the bot to your test server.

Keep the token private. Never place it in source code, a GitHub variable, the
README, or `discloud.config`.

### 2. Install locally

```powershell
git clone https://github.com/YuriBrX/squadlock-bot.git
cd squadlock-bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

On macOS or Linux, activate with `source .venv/bin/activate` and copy the example
with `cp .env.example .env`.

### 3. Configure

Edit `.env`:

```dotenv
DISCORD_TOKEN=replace_with_your_bot_token
GUILD_ID=
SYNC_COMMANDS=true
```

| Variable | Required | Description |
| --- | --- | --- |
| `DISCORD_TOKEN` | Yes | Secret token from the Discord Developer Portal |
| `GUILD_ID` | No | Target server ID for guild-specific synchronization |
| `SYNC_COMMANDS` | No | Whether to sync application commands on startup; defaults to `true` |

For a bot serving multiple servers, leave `GUILD_ID` empty to perform global
command synchronization. Global commands can take time to appear.

### 4. Run

```powershell
python bot.py
```

Startup logs show the authenticated bot account. Application commands are synced
when `SYNC_COMMANDS` is enabled.

## Commands and controls

### `/squadlock post`

Creates a tracking card in the current text channel or thread.

| Option | Description |
| --- | --- |
| `vehicle_name` | Full display name of the tracked vehicle |
| `mention_mode` | Required alert mode: `@everyone`, selected roles, or none |
| `window_hours` | TTL in hours; defaults to 48 and cannot be below 1 |
| `thumbnail_url` | Optional public image URL shown on the card |

When **Roles** is selected, an ephemeral role picker accepts between one and five
roles before creating the card.

### `/squadlock close`

Stops tracking a card and attempts to delete its message. Supply the optional
Discord message link when the channel contains multiple tracked cards.

### `/squadlock setwindow`

Changes a card's TTL and restarts its countdown from the current time. It also
accepts an optional card message link.

### Card buttons

| Button | Behavior |
| --- | --- |
| Renew | Restarts the countdown using the existing TTL |
| Warn me | Toggles personal expiration notifications for the current user |
| Squadlock TTL | Opens a modal and restarts the countdown with a new TTL |
| Close | Removes tracking and deletes the card; requires Manage Messages |

Discord's **Apps** menu also exposes **Squadlock Close** and **Squadlock Set TTL**
message actions.

## Alerts

The background watcher checks cards every 15 seconds. The current warning offsets
are configured in `Cogs/squadlock_cog.py` and trigger at approximately 481, 121,
and 16 minutes before expiration. Each threshold is sent once per renewal cycle.
An additional message is posted when a card expires.

Mentions are constructed with explicit Discord allowed-mention controls:

- `everyone` allows only the `@everyone` mention;
- `role` allows only the saved role IDs;
- `none` sends no general mention; and
- subscribed users may still receive individual mentions in every mode.

## Persistence and privacy

Operational state is stored locally in `runtime/squadlocks.json`. A record can
contain guild, channel, message, role, and subscriber user IDs, along with vehicle
names and timestamps. For that reason, the entire `runtime/` directory is ignored
by Git and must never be committed or attached to public issues.

The bot does not use an external database. Back up the runtime JSON privately if
you need durable recovery across hosting migrations. Concurrent writes are not
designed for multiple bot processes sharing the same directory.

## Recovering tracked cards

If `runtime/squadlocks.json` is lost but card messages still exist, stop the main
bot and run:

```powershell
python recover_squadlocks.py
```

To scan only one server:

```powershell
python recover_squadlocks.py --guild-id 123456789012345678
```

The utility scans the 50 most recent messages in each accessible text channel and
active thread, recognizes Squadlock component IDs, reconstructs their timestamps
and settings, writes a new runtime JSON file, and exits.

Important limitations:

- Only recent messages within the 50-message window are considered.
- The bot needs View Channel and Read Message History.
- Closed, deleted, archived, or inaccessible cards may not be recoverable.
- Stop the main bot first so both processes do not write the state file.

## Discloud deployment

The included `discloud.config` starts `bot.py` with automatic restart and 100 MB
RAM. Upload the project through the Discloud CLI or dashboard and configure
`DISCORD_TOKEN`, `GUILD_ID`, and `SYNC_COMMANDS` using Discloud's environment or
secret settings. The included `discloud.ignore` prevents the local `.env` and
development files from being uploaded.

Persist the `runtime/` directory on the hosting platform if you expect tracked
cards to survive a redeploy. Otherwise, use the recovery utility after migration.

## Customization

The main behavior constants live near the top of `Cogs/squadlock_cog.py`:

| Constant | Purpose |
| --- | --- |
| `DEFAULT_WINDOW_HOURS` | Default card TTL |
| `ALERT_OFFSETS_MIN` | Warning thresholds before expiration |
| `MENTION_STRATEGY` | Default mention behavior for stored/legacy records |
| `RENEW_LOG_CHANNEL_ID` | Optional channel for renewal audit messages |
| `BRAND_NAME` | Card subtitle |
| `BADGE_LEFT` / `BADGE_STATE` | Status labels shown on cards |
| `EMBED_COLOR` | Components V2 container accent color |

Restart the bot after changing constants. Existing persistent buttons continue to
work because the bot registers fixed component IDs during startup.

## Troubleshooting

### Commands do not appear

- Confirm the invite included the `applications.commands` scope.
- Ensure `SYNC_COMMANDS=true` and restart the bot.
- Check `GUILD_ID`; leave it empty for global synchronization.
- Inspect startup logs for Discord API errors.

### A card cannot be created or updated

Grant View Channel, Send Messages, and Embed Links in the target channel. Threads
must be visible and writable by the bot.

### Alerts do not mention anyone

For `@everyone`, grant Mention Everyone. For role alerts, ensure the selected role
is mentionable or that the bot has permission to mention it. Confirm that the bot
can still send messages in the card's channel.

### A card is not registered after restart

Verify that the hosting platform preserved `runtime/squadlocks.json`. If not, stop
the main bot and use `recover_squadlocks.py` while the original card messages are
still visible.

### `DISCORD_TOKEN is not set`

Create `.env` beside `bot.py` for local development or configure the variable in
your hosting platform. Rotate the token immediately if it was exposed.

## Project structure

```text
.
├── .github/                  # CI, Dependabot, and contribution templates
├── Cogs/
│   └── squadlock_cog.py      # Commands, cards, persistence, and alerts
├── .env.example              # Safe configuration template
├── bot.py                    # Bot startup and command synchronization
├── config.py                 # Environment-variable parsing
├── paths.py                  # Runtime directory definition
├── recover_squadlocks.py     # State recovery utility
├── discloud.config           # Discloud process configuration
├── discloud.ignore           # Deployment exclusions
├── requirements.txt          # Python dependencies
└── LICENSE                   # MIT license
```

## Development

Use a dedicated Discord test server and run the same source validation as CI:

```powershell
python -m compileall -q bot.py config.py paths.py recover_squadlocks.py Cogs
```

Keep changes focused, update documentation when behavior changes, and sanitize
logs before opening an issue. Never commit `.env` or `runtime/`.

## License

Released under the [MIT License](LICENSE).
