# FGC Sync

[![Build](https://github.com/klopfer-hello/ForgasGuildCalender-Sync/actions/workflows/release.yml/badge.svg)](https://github.com/klopfer-hello/ForgasGuildCalender-Sync/actions/workflows/release.yml)
[![Lint & Test](https://github.com/klopfer-hello/ForgasGuildCalender-Sync/actions/workflows/lint.yml/badge.svg)](https://github.com/klopfer-hello/ForgasGuildCalender-Sync/actions/workflows/lint.yml)
[![codecov](https://codecov.io/gh/klopfer-hello/ForgasGuildCalender-Sync/branch/main/graph/badge.svg?token=KB4N8FWDGR)](https://codecov.io/gh/klopfer-hello/ForgasGuildCalender-Sync)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![Latest Release](https://img.shields.io/github/v/release/klopfer-hello/ForgasGuildCalender-Sync)](https://github.com/klopfer-hello/ForgasGuildCalender-Sync/releases/latest)

> ## Disclaimer
> This is an independent, community-made tool. It is **not affiliated with, endorsed by, or related to** the developers or maintainers of the [Forga's Guild Calendar](https://github.com/ForgaNet/ForgasGuildCalendar) addon. All trademarks and product names belong to their respective owners.

Companion tool for the [Forga's Guild Calendar](https://github.com/ForgaNet/ForgasGuildCalendar) WoW addon. Reads raid/event data from the addon's SavedVariables and offers two independent sync features — use either or both:

| Feature | What it does |
|---------|-------------|
| **Discord Bot** | Creates per-event forum threads with rendered roster images, pings confirmed members, auto-deletes threads 24h after the raid |
| **Google Calendar** | Syncs raids you're signed up for to your personal Google Calendar |

Both features are optional — configure only what you need.

Runs as a **Windows system tray app** (auto-sync on file changes + 5-minute polling) or as a **headless CLI** for Linux/cron.

**Languages supported:** English (`en-UK`), Deutsch (`de-DE`). The first prompt of the setup wizard (CLI and GUI) lets you pick — you can change it later in Settings.

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

Run `fgc-sync-cli` — an interactive setup will guide you through selecting your WoW directory, account, and guild. Discord and Google Calendar can optionally be configured during setup.

```bash
fgc-sync-cli
```

Subsequent runs perform a single sync cycle and exit. Use `--discord-only` to skip Google Calendar:

```bash
fgc-sync-cli --discord-only
```

Use `--dry-run` to preview what would happen without making any changes — shows planned actions for both Google Calendar and Discord:

```bash
fgc-sync-cli --dry-run
```

Example output:

```
Google Calendar: 1 to create, 0 to update, 0 to delete

Action  Title                                  Date        Time   Info
-----------------------------------------------------------------------
create  [Raid] Gruul mit Forga (Klopfbernd)    2026-04-10  19:45  7 confirmed, 14 signed

Discord: 0 to create, 1 to update, 0 to delete

Action  Title               Date        Time   Info
-----------------------------------------------------
update  Karazhan mit Forga  2026-04-10  19:45  7 confirmed, 14 signed, ping 2 new
```

Use `--force` to delete every Discord thread tracked in the local mapping and recreate them from scratch (useful for recovering from inconsistent state):

```bash
fgc-sync-cli --force
```

#### Cron setup

Sync every 5 minutes:

```
*/5 * * * * /path/to/fgc-sync-cli
```

## Feature Setup

### Discord Bot

Creates a forum thread per raid, posts a rendered roster image as the starter message, and pings confirmed members. Threads are automatically deleted 24 hours after the raid.

Thread names follow the format: `Do 10.04. 20:00 — Kara mit Forga` (de-DE) or `Thu 10.04. 20:00 — Kara with Forga` (en-UK). Threads created under one language remain discoverable after switching to the other — no orphaned duplicates.

**Setup:**

1. Go to [Discord Developer Portal](https://discord.com/developers/applications), create an application
2. **Bot** tab: create bot, copy token, enable **Server Members Intent**
3. **OAuth2** → URL Generator: scope `bot`, permissions: **Send Messages**, **Manage Threads**, **Read Message History**
4. Open the generated URL to invite the bot to your server
5. Create a **forum channel** in Discord (e.g. "Raids")
6. In the forum channel's permission settings, give the bot role **Manage Threads** and **Send Messages in Threads**
7. Right-click the forum channel → **Copy Channel ID** (requires Developer Mode: User Settings → Advanced)
8. Enter Bot Token, Server ID, and Forum Channel ID in the app settings (tray icon → Settings) or `config.json`:

```json
{
  "discord_bot_token": "your-bot-token",
  "discord_guild_id": "your-server-id",
  "discord_forum_id": "your-forum-channel-id"
}
```

#### Sharing Discord config with your guild

Once Discord is configured, you can generate a **setup code** that bundles the bot token, server ID, and forum channel ID into a single obfuscated string. Share this with guildmates so they can skip the manual Discord setup:

```bash
fgc-sync-cli --export-code
```

This prints a code like `fgc1-eNqrVkrJLE7OL0qJT8ov...` that recipients can paste during setup:

- **GUI**: on the Discord wizard page, paste the code into the "Setup code" field and click **Import**
- **CLI**: select "Paste a setup code" when prompted during setup

<!-- screenshot:setup-code-import — Discord wizard page with setup code field -->

#### Multi-client support

Multiple guild members can run FGC Sync against the same Discord forum channel. The tool handles this safely:

- **Thread dedup**: threads are matched by their deterministic name, so two clients won't create duplicates
- **Ping dedup**: before pinging, the bot scans the thread's message history for its own prior ping messages — members already pinged (by any client) are skipped
- **Stale-data guard**: each roster image filename encodes the SavedVariables file timestamp; a client with older data will skip posting to avoid overwriting newer updates

#### Weekly raid overview

Alongside the per-event threads, the bot maintains a single permanent forum thread (`Wöchentliche Raid Übersicht` in de-DE, `Weekly Raid Overview` in en-UK) that shows the current ISO week's schedule as a school-timetable-style image (7 day columns × hourly rows). Each raid cell lists short name, time range, raid leader, and counts (`Bestätigt`/`Confirmed` and `Angemeldet`/`Signed` = confirmed + signed). Parallel raids on the same day sit side-by-side.

The thread itself persists forever; every sync edits the starter message in place with a fresh image and a text summary like:

```
**Raid Übersicht — KW 16 / 2026**
13.04.2026 – 19.04.2026
3 Raid(s) geplant
```

Unlike per-event threads, the weekly overview **includes every planned raid in the week regardless of roster status** — not just those with a confirmed group assignment.

Test before deploying: `fgc-sync-cli --weekly-only --dry-run` writes a `weekly_preview.png` to the config directory without touching Discord. `fgc-sync-cli --weekly-only` runs just the weekly sync (skipping per-event threads and Google Calendar).

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
3. **Discord** (if configured): for events with a confirmed roster (group assignments) within 7 days — creates a forum thread with a roster image, pings confirmed members
4. **Google Calendar** (if configured): for events where your character is signed up — creates/updates/deletes calendar entries
5. Watches the SavedVariables file for changes (triggers on logout, `/reload`, character switch)

## CLI Reference

```
fgc-sync-cli [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `--dry-run` | Preview what sync would do without making changes (also writes `weekly_preview.png` to the config dir for local review) |
| `--discord-only` | Only sync to Discord, skip Google Calendar |
| `--weekly-only` | Only sync the weekly overview thread (skip per-event threads and Google Calendar) |
| `--force` | Delete all tracked Discord threads and recreate from scratch |
| `--export-code` | Print a setup code encoding the Discord config for sharing |
| `--setup` | Re-run the interactive setup wizard |
| `--config-dir DIR` | Use a custom config directory |
| `--check-update` | Check if a newer version is available |
| `--update` | Download and install the latest version |
| `--version` | Show version |
| `--about` | Show version and license information |

## Advanced Configuration

The config file is stored at:
- **Windows**: `%APPDATA%\ForgasGuildCalendar-Sync\config.json`
- **Linux**: `~/.config/ForgasGuildCalendar-Sync/config.json`

| Key | Default | Description |
|-----|---------|-------------|
| `language` | `en-UK` | UI / output language. Currently shipped: `en-UK`, `de-DE`. Drop a new `<code>.json` into `src/fgc_sync/i18n/` to add a language — see [CLAUDE.md](CLAUDE.md#internationalization-i18n) |
| `log_level` | `ERROR` | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `timezone` | `Europe/Berlin` | IANA timezone for event times |
| `default_duration_hours` | `3` | Default event duration in Google Calendar |

## Contributing

Contributions are welcome! Please follow these guidelines:

### Setup

```bash
git clone https://github.com/klopfer-hello/ForgasGuildCalender-Sync.git
cd ForgasGuildCalender-Sync
pip install -e ".[gui]"
pip install pre-commit pytest
pre-commit install
```

### Code quality

Pre-commit hooks run automatically on every commit:
- **ruff** — linting and auto-fix
- **ruff-format** — consistent formatting (Black-compatible)
- **conventional commits** — enforces commit message format
- **trailing whitespace**, **end-of-file**, **YAML/TOML validation**

### Commit messages

This project uses [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>
```

| Type | When |
|------|------|
| `feat` | New feature |
| `fix` | Bug fix |
| `refactor` | Restructuring without behavior change |
| `docs` | Documentation only |
| `chore` | Build, config, dependencies |

### Running tests

```bash
pytest tests/ -v
```

### Adding a translation

Translation files live in [`src/fgc_sync/i18n/`](src/fgc_sync/i18n/) as `<code>.json` (one file per language, e.g. `en-UK.json`, `de-DE.json`). The application discovers them at startup — drop a new file in and it appears in the language picker on next launch. No code changes required.

To contribute a new language:

1. Copy `en-UK.json` (the reference language — source of truth for which keys exist) to `<your-code>.json`. Use a [BCP-47 tag](https://en.wikipedia.org/wiki/IETF_language_tag) like `fr-FR`, `pl-PL`, or `pt-BR`
2. Update the `_meta` block at the top — `display_name` is what the language picker shows:
   ```json
   {
     "_meta": { "display_name": "Français", "native_name": "Français" },
     ...
   }
   ```
3. Translate every string value. Keep `{placeholder}` tokens intact and in the same order they appear in the source — they're filled with `str.format()` at runtime
4. Don't translate raid short names (`Kara`, `SSC`, etc.) — those are WoW jargon that stays consistent across languages
5. Run `pytest tests/test_i18n.py` to verify your file has every reference key
6. Commit with `feat(i18n): add <Language> translation`

Notes:
- Missing keys log a warning and fall through to the English value, so a partial translation still works
- Discord thread names use the `discord.weekday_abbrev` array and the `discord.thread_with_word` value (e.g. `mit` / `with`) — pick concise forms (≤ 3 chars for weekdays) to keep thread titles short
- Date format stays `dd.mm.yyyy` regardless of language (matches the WoW addon convention)

See [CLAUDE.md → Internationalization](CLAUDE.md#internationalization-i18n) for the full key reference and the cross-language dedup rules.

### Architecture

See [CLAUDE.md](CLAUDE.md) for the full architecture guide, dependency rules, and code review conventions.

## License

This project is licensed under the [MIT License](LICENSE).

This software uses [PySide6](https://doc.qt.io/qtforpython-6/) (Qt for Python), which is licensed under the [LGPL-3.0](https://www.gnu.org/licenses/lgpl-3.0.html). The standalone executables bundle PySide6 — as required by the LGPL, you can rebuild the executables from source to use a different version of PySide6:

```bash
pip install -e ".[gui]"
pip install pyinstaller
pyinstaller --onefile --windowed --name "FGC-Sync" --add-data "resources;resources" --hidden-import "PIL" src/fgc_sync/app.py
```

Other dependencies and their licenses: Pillow (HPND), google-api-python-client (Apache-2.0), requests (Apache-2.0), watchdog (Apache-2.0), slpp (MIT).
