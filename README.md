# FGC Calendar Sync

Syncs raid events from [Forga's Guild Calendar](https://github.com/ForgaNet/ForgasGuildCalendar) (WoW addon) to Google Calendar and Discord. Runs as a Windows system tray app or headless CLI (Linux/cron).

## Features

- **Discord bot**: creates per-event channels with roster images, pings confirmed members
- **Google Calendar sync** (optional): creates calendar events for raids you're signed up for
- **Auto-sync**: watches SavedVariables for changes + polls every 5 minutes
- **Multi-client safe**: multiple instances can share the same Discord channel

## Installation

### Windows — Standalone executable (recommended)

1. Download `FGC-Sync.exe` from the [latest release](https://github.com/klopfer-hello/ForgasGuildCalender-Sync/releases/latest)
2. Run `FGC-Sync.exe` — a setup wizard will guide you through configuration
3. (Optional) Right-click the tray icon → **"Start with Windows"** for auto-start

### Windows — pip install

Requires Python 3.12+.

```bash
pip install "fgc-sync[gui] @ git+https://github.com/klopfer-hello/ForgasGuildCalender-Sync.git"
```

Then run:

```bash
fgc-sync
```

On first launch, a setup wizard guides you through selecting your WoW directory, logging into Google (optional), and configuring Discord.

### Linux — pip install (headless CLI)

Requires Python 3.12+. No GUI or PySide6 needed.

```bash
pip install "fgc-sync @ git+https://github.com/klopfer-hello/ForgasGuildCalender-Sync.git"
```

Create your config manually at `~/.config/ForgasGuildCalendar-Sync/config.json`:

```json
{
  "wow_path": "/path/to/wow",
  "account_folder": "YOUR_ACCOUNT_ID",
  "guild_key": "Realm-Guild Name",
  "discord_bot_token": "your-bot-token",
  "discord_guild_id": "your-server-id",
  "discord_category_id": "your-category-id",
  "timezone": "Europe/Berlin"
}
```

Run once:

```bash
fgc-sync-cli
```

Or with Discord only (no Google Calendar):

```bash
fgc-sync-cli --discord-only
```

#### Cron setup

Sync every 5 minutes:

```bash
crontab -e
```

Add:

```
*/5 * * * * /path/to/fgc-sync-cli --discord-only
```

## Discord Bot Setup

1. Go to [Discord Developer Portal](https://discord.com/developers/applications), create an application
2. **Bot** tab: create bot, copy token, enable **Server Members Intent**
3. **OAuth2** → URL Generator: scope `bot`, permissions: **Send Messages**, **Manage Channels**, **Read Message History**
4. Open the generated URL to invite the bot to your server
5. Create a **category** in Discord (e.g. "Raids")
6. In the category's permission settings, give the bot role **Manage Channels** for that category only — this prevents the bot from affecting other channels
7. Right-click the category → **Copy Category ID** (requires Developer Mode: User Settings → Advanced)
8. Enter Bot Token, Server ID, and Category ID in the app settings (tray icon → Settings) or `config.json`

## Google Calendar Setup (optional)

1. Create a [Google Cloud project](https://console.cloud.google.com/) with the **Google Calendar API** enabled
2. Create an OAuth **Desktop** client ID and download `client_secrets.json`
3. Place `client_secrets.json` in:
   - Windows: `%APPDATA%\ForgasGuildCalendar-Sync\`
   - Linux: `~/.config/ForgasGuildCalendar-Sync/`
4. The app will prompt you to log in on first sync

## How it works

1. Reads `FGC_DB` from WoW's SavedVariables file
2. Extracts future events with confirmed raid rosters (group assignments)
3. **Discord**: creates a channel per event with a rendered roster image, pings confirmed members
4. **Google Calendar** (optional): creates/updates/deletes calendar events for raids you're signed up for
5. Watches the SavedVariables file for changes (triggers on logout, `/reload`, character switch)

## Disclaimer

This is an independent, community-made tool. It is **not affiliated with, endorsed by, or related to** the developers or maintainers of the [Forga's Guild Calendar](https://github.com/ForgaNet/ForgasGuildCalendar) addon. All trademarks and product names belong to their respective owners.
