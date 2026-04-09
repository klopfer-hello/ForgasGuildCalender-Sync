# ForgasGuildCalendar-Sync – Project Context for Claude

## Overview

System tray companion tool for the **Forga's Guild Calendar** WoW addon. Reads raid/event data from the addon's SavedVariables file and syncs it to Google Calendar and/or Discord. Runs as a background process with file watching and periodic polling.

## Environment

- Python >= 3.12 (tested on 3.14)
- **Windows GUI mode**: PySide6 system tray + dialogs, entry point `fgc-sync`
- **Linux/headless CLI mode**: no Qt required, entry point `fgc-sync-cli`, designed for cron
- Install: `pip install -e .` (CLI only) or `pip install -e ".[gui]"` (with PySide6)

## Source Data

- WoW addon: **Forga's Guild Calendar** (ForgasGuildCalendar)
- SavedVariables file: `WTF/Account/<id>/SavedVariables/ForgasGuildCalendar.lua`
- Global variable: `FGC_DB`
- Event path: `FGC_DB.profiles[profile].guildScoped[guildKey].events["YYYY-MM-DD"][]`
- Time field: always use `serverTimeMinutes` (minutes from midnight), not `serverHour`/`serverMinute` (often missing)
- Server timezone: EU Thunderstrike = `Europe/Berlin`
- Character detection: auto-detected from `FGC_DB.profileKeys` (format: `"Name - Realm"`)

## Architecture (MVC)

```
src/fgc_sync/
├── models/          # M — Pure data, no dependencies
│   ├── enums.py     #   Attendance, EventType, SyncAction
│   ├── events.py    #   CalendarEvent, Participant
│   ├── sync.py      #   SyncResult, SyncPlanEntry, SyncPlan
│   └── update.py    #   UpdateInfo, InstallMode
│
├── services/        # Business logic (no UI, no Qt)
│   ├── config.py    #   JSON config in %APPDATA%
│   ├── lua_parser.py        #   Parse FGC_DB SavedVariables
│   ├── discord_poster.py     #   Discord REST API (forum threads + images + pings)
│   ├── roster_image.py      #   Pillow-based roster card renderer
│   ├── google_calendar.py   #   OAuth2 + Calendar CRUD
│   ├── sync_engine.py       #   Diff & sync (create/update/delete)
│   ├── file_watcher.py      #   Watchdog observer with debounce
│   └── updater.py           #   GitHub release check + self-update
│
├── controllers/     # C — Mediates views and services
│   ├── app_controller.py    #   Orchestrates lifecycle, wires signals
│   └── sync_controller.py   #   QThread sync, preview computation
│
├── views/           # V — Pure Qt UI, emits signals
│   ├── styles.py            #   System-aware light/dark stylesheet
│   ├── tray_icon.py         #   System tray icon + menu
│   ├── setup_wizard.py      #   First-run: WoW path → Discord → Google → calendar
│   ├── settings_dialog.py   #   Post-setup config changes
│   └── preview_dialog.py    #   Sync preview table
│
├── _version.py      # Version, license metadata, GitHub repo constant
├── app.py           # QApplication bootstrap (Windows GUI)
├── cli.py           # Headless CLI entry point (Linux/cron)
└── __main__.py      # python -m fgc_sync (auto-detects mode)
```

### Dependency Rules

- Models depend on **nothing**
- Services depend on **models** and external libraries only
- Controllers depend on **services** and **models**
- Views depend on **models** (for display) and **Qt** only
- Views never call services directly — controllers mediate

## Key Behaviors

### Sync Logic (`sync_engine.py`)

1. Parse SavedVariables, extract future events
2. Filter to events where one of the user's characters is **Signed** (1) or **Confirmed** (2)
3. For each event:
   - **Not in mapping** → search Google Calendar for existing event with same title+date (dedup), adopt if found, else create
   - **In mapping, revision changed** → verify event exists in Google, update or re-create
   - **In mapping, revision same** → verify event exists in Google, re-create if deleted externally, else skip
4. Events in mapping but not in WoW (or in `deletedEvents`) → delete from Google Calendar
5. Persist updated mapping to config

### Discord Sync Logic (`sync_engine.py` → `execute_discord_sync`)

Runs after Google Calendar sync on every sync cycle. Uses `discord_message_mapping` in config.
Only posts events within the next 7 days that have a confirmed roster (group assignments).

1. **Stale-data guard**: read local SavedVariables file mtime; scan recent messages of all forum threads for the highest `_t<unix_ts>` embedded in any roster image filename. If local mtime < remote max → skip the entire Discord sync (another client has newer data; overwriting would cause flapping images and duplicate pings).
2. Collect all future guild events (not filtered by personal participation)
3. For each event:
   - **Not in mapping** → call `find_existing_thread` (matches by deterministic thread name first, then attachment scan for legacy threads). If found → adopt with empty `pinged` list (so `ping_members` can resolve and ping on the next pass). Otherwise → create forum thread with roster image as starter message.
   - **In mapping, content hash changed** → ensure thread is unarchived, then update image in place (PATCH), or post a new image if the original was deleted
   - **In mapping, hash unchanged** → ensure thread is unarchived, fall through to ping retry only
   - **Ping retry**: compute `to_ping = current_confirmed - pinged`. Ping anyone in the diff (label "Confirmed" for new threads, "Newly confirmed" otherwise). `ping_members` returns the subset of names that actually resolved to a Discord member; only those are added to `pinged`. Names that fail to resolve (e.g. user not yet in the Discord server) are retried on the next sync.
4. Events no longer in WoW (or in `deletedEvents`) → delete thread
5. Events that happened 24+ hours ago → delete thread
6. Persist `discord_message_mapping` to config

**Multi-client safety**: image filename encodes `roster_<event_id>_h<hash>_t<sv_mtime>.png`. Thread-name-based dedup avoids racing on creation; sv_mtime guard prevents older clients from overwriting newer ones.

**Mapping schema** (`discord_message_mapping[event_id]`):
- `channel_id`: Discord thread id (threads are channels in Discord's API)
- `message_ids`: `{image_id, hash, sv_mtime}`
- `pinged`: list of character names that have been successfully pinged so far. Legacy entries may use `confirmed` instead — read code falls back automatically.

### Discord Roster Images (`roster_image.py`)

- Rendered via Pillow at 2x resolution
- **Header**: day of week, date, time, event title, location, participant counts
- **Groups**: confirmed participants in their assigned raid groups (1–8), with role icons (shield/cross/sword) and class icons
- **Sections**: Signed, Bench (no Declined)
- **Footer**: role counts (Tanks/Healers/DDs) and class counts with icons

### Discord Per-Event Forum Threads

Each event with a confirmed roster gets its own thread in a Discord forum channel:
- **Thread name**: `Do 03.04. 20:00 — Gruul mit Forga` (German weekday, date, time, short raid name, creator)
- **Short raid names**: kara, gruul, maggi, ssc, tk, hyjal, bt, swp, za (see `RAID_SHORT_NAMES` in `discord_poster.py`)
- **Starter message**: roster image posted as part of thread creation (single API call)
- **Visibility**: public to all server members
- **Auto-archive handling**: threads are automatically unarchived before posting updates or pings
- **Pings**: one-off notification messages — "Confirmed:" on creation, "Newly confirmed:" on updates
- **Cleanup**: threads are deleted 24 hours after the event start time
- Threads are created in chronological order (sorted by date and time)

### Discord Member Matching

Members are matched by checking if the WoW character name is a **case-insensitive substring** of the Discord member's server nickname, global display name, or username. The bot requires the **Server Members Intent** enabled in the Discord Developer Portal.

### Discord Bot Setup

1. Create a Discord application at discord.com/developers
2. Bot tab: create bot, copy token, enable **Server Members Intent**
3. OAuth2 → URL Generator: scope `bot`, permissions: **Send Messages**, **Manage Threads**, **Read Message History**
4. Invite bot to your server
5. Create a **forum channel** in Discord (e.g. "Raids")
6. In the forum channel's permission settings, give the bot role **Manage Threads** and **Send Messages in Threads** — this scopes the bot's thread creation/deletion to that forum
7. Right-click the forum channel → Copy Channel ID
8. In the app's Settings, fill in Bot Token, Server ID, and Forum Channel ID

### Auto-sync Triggers

- **File watcher**: watchdog monitors SavedVariables directory, 2s debounce
- **Poll timer**: every 5 minutes as fallback
- **Manual**: "Sync Now" from tray menu

The tray menu also has **"Open Log File"** which opens `%APPDATA%/ForgasGuildCalendar-Sync/sync.log` with the OS default handler.

### Linux / Headless CLI

- Entry point: `fgc-sync-cli` (or `python -m fgc_sync --headless`)
- Runs a single sync cycle and exits — designed for cron
- No Qt/PySide6 dependency required
- Flags: `--discord-only`, `--version`, `--about`, `--check-update`, `--update`, `--config-dir`
- Interactive setup on first run using `questionary` (arrow-key select, tab path completion)
- Handles git bash `/d/...` paths on Windows automatically
- Checks for updates after every sync run (log message only)
- Config at `~/.config/ForgasGuildCalendar-Sync/config.json` (XDG)
- Cron example: `*/5 * * * * /path/to/fgc-sync-cli`

### Auto-Update (`updater.py`)

- Queries GitHub releases API for latest version
- Compares with current version from package metadata (`_version.py`)
- **GUI**: checks at startup + every 6 hours, shows popup with Update Now / Later
- **CLI**: logs a message after sync if newer version exists; `--update` to install
- **Exe mode**: downloads new exe, writes a `.cmd` swap script, exits, script replaces exe (no auto-restart to avoid DLL conflicts)
- **Pip mode**: on Windows spawns a detached batch script; on Linux runs `pip install --upgrade` directly
- Cleans up leftover `.bak`/`.update` files on startup
- Skips check if version is `"dev"` (editable install without metadata)

### Google Calendar Events

- **Summary**: `[Type] Title (CharacterName)` e.g. `[Raid] Gruul mit Forga (Klopfbernd)`
- **Start**: date + serverTimeMinutes in Europe/Berlin
- **Duration**: configurable, default 3 hours
- **Description**: event comment + participant counts + roster
- **Location**: raid name (titlecased)

## Configuration

Stored at `%APPDATA%/ForgasGuildCalendar-Sync/config.json`:

| Key | Purpose |
|-----|---------|
| `wow_path` | WoW installation directory |
| `account_folder` | WTF account folder name |
| `guild_key` | Guild scope key (e.g. `Thunderstrike-Sauercrowd Community`) |
| `calendar_id` | Google Calendar ID |
| `timezone` | IANA timezone (default: `Europe/Berlin`) |
| `default_duration_hours` | Event duration (default: 3) |
| `event_mapping` | `{fgc_eventId: {google_id, revision, title}}` |
| `discord_bot_token` | Discord bot token (optional) |
| `discord_guild_id` | Discord server ID (optional) |
| `discord_forum_id` | Forum channel ID for raid threads (optional) |
| `discord_message_mapping` | `{fgc_eventId: {channel_id, message_ids: {image_id, hash, sv_mtime}, pinged[]}}` |

### Credential Files

- `%APPDATA%/ForgasGuildCalendar-Sync/token.json` — OAuth2 token (auto-refreshed)
- `client_secrets.json` — Google OAuth client ID (looked up next to project root first, then %APPDATA%)

## File Structure

```
ForgasGuildCalendar-Sync/
├── pyproject.toml           # Package config, dependencies, entry point
├── LICENSE                  # MIT license
├── CLAUDE.md                # This file
├── README.md                # User-facing documentation
├── .gitignore
├── .github/workflows/
│   └── release.yml          # PyInstaller build + GitHub release on tag push
├── client_secrets.json      # Google OAuth (gitignored)
├── resources/
│   ├── app.ico              # Application icon (purple G, 16-256px)
│   └── class_icons/         # WoW class icons for roster images
├── src/fgc_sync/            # Package source (see Architecture above)
└── tests/
    └── __init__.py
```

## Versioning

- Schema: **Semantic Versioning** (`MAJOR.MINOR.PATCH`)
- Version lives in `pyproject.toml` (`version = "X.Y.Z"`)
- Releases are Git tags: `git tag vX.Y.Z`

| Bump | When |
|------|------|
| `PATCH` | Bug fixes without new features |
| `MINOR` | New features, backwards compatible |
| `MAJOR` | Breaking changes (e.g. config format) |

### Release Checklist

1. `git log vX.Y.Z..HEAD --oneline` — review commits since last release
2. Determine version bump (PATCH / MINOR / MAJOR)
3. Update `pyproject.toml` — `version = "X.Y.Z"`
4. Update `CLAUDE.md` if architecture, features, or conventions changed
5. Commit: `chore: release vX.Y.Z`
6. Tag: `git tag vX.Y.Z` (triggers CI build + GitHub release)

## Commit Requirements

Commits must follow the **Conventional Commits** standard:

```
<type>(<scope>): <description>

<body>  ← optional, explains the "why"

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

### Allowed Types

| Type | When |
|------|------|
| `feat` | New feature |
| `fix` | Bug fix |
| `refactor` | Restructuring without behavior change |
| `docs` | Documentation only |
| `chore` | Build, config, dependencies |

### Rules

- Description in **English**, lowercase, no period at the end
- Body in English, explains concretely what and why
- Each logically separate change gets its own commit
- Always append `Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>`
- Never use `--no-verify` or force-push without explicit request

## Code Review Rules

### Architecture

- Models must not import from services, controllers, or views
- Services must not import from controllers or views
- Controllers must not import from views (except to instantiate dialogs)
- Views must not call service functions directly

### Quality

- No magic numbers — use enums (`Attendance`, `EventType`, `SyncAction`) and named constants
- No duplicated Google Calendar path construction — use `config.saved_variables_path`
- The `SAVED_VARIABLES_FILENAME` constant lives in `services/config.py`

### Google Calendar Sync

- Always verify events exist before assuming they do (externally deleted events)
- Always search for duplicates before creating (lost mapping scenario)
- Use `serverTimeMinutes` for time, never `serverHour`/`serverMinute` alone
- Filter events to only those where the user's character is Signed or Confirmed

### Discord Sync

- The `pinged` list must only contain names that `ping_members` actually resolved — never pre-seed with unverified names
- Thread adoption (`find_existing_thread`) must start with an empty `pinged` list
- `message_ids` must not contain `channel_id` — keep thread ID and message metadata separate
- Bot requires **Manage Threads** permission on the forum channel to delete threads with replies

### UI

- Stylesheet must follow system dark/light mode (`is_system_dark_mode()`)
- All dialogs use the shared stylesheet from `views/styles.py`
- Views emit signals for user actions — controllers handle the logic
