# FGC Calendar Sync

Companion tool for the [Forga's Guild Calendar](https://github.com/ForgaNet/ForgasGuildCalendar) WoW addon. Reads raid/event data from the addon's SavedVariables and offers two independent sync features — use either or both:

| Feature | What it does |
|---------|-------------|
| **Discord Bot** | Creates per-event channels with rendered roster images, pings confirmed members, auto-deletes channels 24h after the raid |
| **Google Calendar** | Syncs raids you're signed up for to your personal Google Calendar |

Both features are optional — configure only what you need.

Runs as a **Windows system tray app** (auto-sync on file changes + 5-minute polling) or as a **headless CLI** for Linux/cron.

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

On first launch, a setup wizard guides you through selecting your WoW directory and guild. Configure Discord and/or Google Calendar in Settings afterwards.

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
  "timezone": "Europe/Berlin"
}
```

Add Discord and/or Google Calendar settings as needed (see setup sections below).

Run a single sync cycle:

```bash
fgc-sync-cli
```

Or Discord only (skip Google Calendar):

```bash
fgc-sync-cli --discord-only
```

#### Cron setup

Sync every 5 minutes:

```
*/5 * * * * /path/to/fgc-sync-cli
```

## Feature Setup

### Discord Bot

Creates a channel per raid under a Discord category, posts a rendered roster image, and pings confirmed members. Channels are automatically deleted 24 hours after the raid.

**Setup:**

1. Go to [Discord Developer Portal](https://discord.com/developers/applications), create an application
2. **Bot** tab: create bot, copy token, enable **Server Members Intent**
3. **OAuth2** → URL Generator: scope `bot`, permissions: **Send Messages**, **Manage Channels**, **Read Message History**
4. Open the generated URL to invite the bot to your server
5. Create a **category** in Discord (e.g. "Raids")
6. In the category's permission settings, give the bot role **Manage Channels** for that category only — this prevents the bot from affecting other channels
7. Right-click the category → **Copy Category ID** (requires Developer Mode: User Settings → Advanced)
8. Enter Bot Token, Server ID, and Category ID in the app settings (tray icon → Settings) or `config.json`:

```json
{
  "discord_bot_token": "your-bot-token",
  "discord_guild_id": "your-server-id",
  "discord_category_id": "your-category-id"
}
```

### Google Calendar

Syncs raids where your character is signed up or confirmed to a personal Google Calendar. Events are created, updated, and deleted automatically.

**Setup:**

1. Create a [Google Cloud project](https://console.cloud.google.com/) with the **Google Calendar API** enabled
2. Create an OAuth **Desktop** client ID and download `client_secrets.json`
3. Place `client_secrets.json` in:
   - Windows: `%APPDATA%\ForgasGuildCalendar-Sync\`
   - Linux: `~/.config/ForgasGuildCalendar-Sync/`
4. Add `calendar_id` to your config or select a calendar in the setup wizard
5. The app will prompt you to log in with Google on first sync

## How it works

1. Reads `FGC_DB` from WoW's SavedVariables file
2. Extracts future events from the guild calendar
3. **Discord** (if configured): for events with a confirmed roster (group assignments) within 7 days — creates a channel, posts a roster image, pings confirmed members
4. **Google Calendar** (if configured): for events where your character is signed up — creates/updates/deletes calendar entries
5. Watches the SavedVariables file for changes (triggers on logout, `/reload`, character switch)

## License

This project is licensed under the [MIT License](LICENSE).

This software uses [PySide6](https://doc.qt.io/qtforpython-6/) (Qt for Python), which is licensed under the [LGPL-3.0](https://www.gnu.org/licenses/lgpl-3.0.html). The standalone executables bundle PySide6 — as required by the LGPL, you can rebuild the executables from source to use a different version of PySide6:

```bash
pip install -e ".[gui]"
pip install pyinstaller
pyinstaller --onefile --windowed --name "FGC-Sync" --add-data "resources;resources" --hidden-import "PIL" src/fgc_sync/app.py
```

Other dependencies and their licenses: Pillow (HPND), google-api-python-client (Apache-2.0), requests (Apache-2.0), watchdog (Apache-2.0), slpp (MIT).

## Disclaimer

This is an independent, community-made tool. It is **not affiliated with, endorsed by, or related to** the developers or maintainers of the [Forga's Guild Calendar](https://github.com/ForgaNet/ForgasGuildCalendar) addon. All trademarks and product names belong to their respective owners.
