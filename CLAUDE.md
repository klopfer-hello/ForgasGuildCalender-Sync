# ForgasGuildCalendar-Sync — Project Context

## Overview

System tray companion for the **Forga's Guild Calendar** WoW addon. Reads raid/event data from SavedVariables and syncs to Google Calendar and/or Discord. Runs as a Windows tray app or headless Linux CLI.

## Environment

- Python >= 3.12 (tested on 3.14)
- **Windows GUI**: PySide6 system tray + dialogs, entry point `fgc-sync`
- **Linux/headless CLI**: no Qt required, entry point `fgc-sync-cli`, designed for cron
- Install: `pip install -e .` (CLI) or `pip install -e ".[gui]"` (with PySide6)

## Source Data

- WoW addon: **Forga's Guild Calendar** (ForgasGuildCalendar)
- SavedVariables: `WTF/Account/<id>/SavedVariables/ForgasGuildCalendar.lua`
- Global variable: `FGC_DB`
- Event path: `FGC_DB.profiles[profile].guildScoped[guildKey].events["YYYY-MM-DD"][]`
- Time: always use `serverTimeMinutes` (minutes from midnight), not `serverHour`/`serverMinute`
- Timezone: EU Thunderstrike = `Europe/Berlin`
- Characters: auto-detected from `FGC_DB.profileKeys` (format `"Name - Realm"`)

---

## Architecture

### MVC Layer Structure

```
src/fgc_sync/
├── models/          # M — Pure data, no dependencies
│   ├── enums.py     #   Attendance, EventType, SyncAction
│   ├── events.py    #   CalendarEvent, Participant
│   ├── sync.py      #   SyncResult, SyncPlanEntry, SyncPlan
│   └── update.py    #   UpdateInfo, InstallMode
│
├── services/        # Business logic (no UI, no Qt)
│   ├── config.py    #   JSON config + setup codes + transactions
│   ├── lua_parser.py        #   Parse FGC_DB SavedVariables
│   ├── discord_poster.py    #   Discord REST API (forum threads + images + pings)
│   ├── roster_image.py      #   Pillow-based roster card renderer
│   ├── google_calendar.py   #   OAuth2 + Calendar CRUD
│   ├── sync_engine.py       #   Diff & sync (create/update/delete) + dry-run plans
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
│   ├── setup_wizard.py      #   First-run wizard (WoW → Discord → Google)
│   ├── settings_dialog.py   #   Post-setup config changes
│   └── preview_dialog.py    #   Sync preview table
│
├── _version.py      # Version, license, GitHub repo constant
├── app.py           # QApplication bootstrap (Windows GUI)
├── cli.py           # Headless CLI entry point (Linux/cron)
└── __main__.py      # python -m fgc_sync (auto-detects mode)
```

### Dependency Rules

- **Models** depend on nothing
- **Services** depend on models and external libraries only
- **Controllers** depend on services and models
- **Views** depend on models (for display) and Qt only
- Views never call services directly — controllers mediate

### File Structure

```
ForgasGuildCalendar-Sync/
├── pyproject.toml              # Package config, dependencies, ruff config
├── .pre-commit-config.yaml     # Pre-commit hooks (ruff, format, conventional commits)
├── .github/workflows/
│   ├── release.yml             # PyInstaller build + GitHub release on tag push
│   ├── lint.yml                # Pre-commit + pytest CI on push/PR
│   └── cleanup-releases.yml    # Delete old releases (keep latest)
├── codecov.yml                 # Coverage reporting config
├── resources/
│   ├── app.ico                 # Application icon (purple G, 16-256px)
│   └── class_icons/            # WoW class icons for roster images
├── src/fgc_sync/               # Package source (see above)
└── tests/                      # pytest unit tests
```

---

## Google Calendar Sync

### Sync Logic (`sync_engine.py` → `execute_sync`)

1. Parse SavedVariables, extract future events
2. Filter to events where the user's character is **Signed** (1) or **Confirmed** (2)
3. For each event:
   - **Not in mapping** → search Google Calendar for duplicate (same title+date), adopt if found, else create
   - **In mapping, revision changed** → verify exists in Google, update or re-create
   - **In mapping, revision same** → verify exists, re-create if deleted externally, else skip
4. Events in mapping but not in WoW (or in `deletedEvents`) → delete from Google Calendar
5. Persist `event_mapping` to config

### Google Calendar Event Format

- **Summary**: `[Type] Title (CharacterName)` e.g. `[Raid] Gruul mit Forga (Klopfbernd)`
- **Start**: date + serverTimeMinutes in configured timezone
- **Duration**: configurable, default 3 hours
- **Description**: event comment + participant counts + roster breakdown
- **Location**: raid name (titlecased)

---

## Discord Sync

### Sync Logic (`sync_engine.py` → `execute_discord_sync`)

Runs after Google Calendar sync. Posts events within the next 7 days (`DISCORD_LOOKAHEAD_DAYS`) that have a confirmed roster (group assignments).

1. **Stale-data guard** (`_is_local_data_stale`): compares local SavedVariables mtime against the highest `_t<unix_ts>` in remote image filenames. If local is older → skip entire sync.
2. Collect future guild events (not filtered by personal participation)
3. For each event:
   - **Not in mapping** → `find_existing_thread` (name match first, then attachment scan via `_find_image_in_thread`). If found → adopt with empty `pinged` list. Otherwise → create forum thread with roster image.
   - **In mapping, hash changed** → unarchive thread, PATCH image in place (or find original image if mapping lost track, then PATCH)
   - **In mapping, hash unchanged** → unarchive thread, fall through to ping retry
   - **Ping logic**: scan thread history for prior bot pings (`get_already_pinged_names`), compute `to_ping = confirmed - already_pinged`, send ping message. Only resolved names are added to `pinged`.
4. **Cleanup**: delete threads for removed events and events older than 24 hours (`EXPIRED_EVENT_HOURS`)
5. Persist `discord_message_mapping` to config

### Forum Threads

- **Thread name**: `Do 03.04. 20:00 — Gruul mit Forga` (German weekday, date, time, short raid name, creator)
- **Short raid names**: kara, gruul, maggi, ssc, tk, hyjal, bt, swp, za (`RAID_SHORT_NAMES`)
- **Starter message**: roster image posted as part of thread creation
- **Pings**: "Confirmed:" on creation, "Newly confirmed:" on updates
- **Cleanup**: threads deleted 24h after event start; 404 on already-deleted threads is silently ignored
- Threads are created in chronological order

### Roster Images (`roster_image.py`)

- Rendered via Pillow at 2x resolution
- **Header**: day of week, date, time, event title, location, participant counts
- **Groups**: confirmed participants in raid groups (1–8) with role and class icons
- **Sections**: Signed, Bench (no Declined)
- **Footer**: role counts (Tanks/Healers/DDs) and class counts with icons

### Member Matching

WoW character name matched as **case-insensitive substring** of Discord server nickname, display name, or username. Requires **Server Members Intent** on the bot.

### Multi-Client Safety

- Image filename: `roster_<event_id>_h<hash>_t<sv_mtime>.png`
- Thread dedup: deterministic thread names prevent duplicate creation
- Image dedup: `_find_image_in_thread` scans up to 100 messages before posting a new image
- Ping dedup: `get_already_pinged_names` scans thread history for prior bot pings
- Stale-data guard: clients with older SavedVariables skip writing

### Mapping Schema (`discord_message_mapping[event_id]`)

- `channel_id`: Discord thread ID
- `message_ids`: `{image_id, hash, sv_mtime}`
- `pinged`: list of character names successfully pinged (legacy: `confirmed`)

---

## CLI

- Entry point: `fgc-sync-cli` (or `python -m fgc_sync --headless`)
- Runs a single sync cycle and exits — designed for cron
- No Qt/PySide6 dependency
- Flags: `--dry-run`, `--discord-only`, `--force`, `--export-code`, `--setup`, `--config-dir`, `--version`, `--about`, `--check-update`, `--update`
- Interactive setup on first run using `questionary`
- Handles git bash `/d/...` paths on Windows (`_normalize_path`)
- Config at `~/.config/ForgasGuildCalendar-Sync/config.json` (XDG)

### Dry-Run Mode

`--dry-run` uses `compute_sync_plan` (Google) and `compute_discord_sync_plan` (Discord) to show planned actions without modifying any remote state or local config. Includes the stale-data guard check.

### Setup Codes

`encode_setup_code` / `decode_setup_code` in `config.py` encode Discord config into a compact obfuscated string (JSON → zlib → base64url, prefixed `fgc1-`). Generated via `--export-code`, consumed during CLI or GUI setup.

---

## Auto-Sync Triggers (GUI)

- **File watcher**: watchdog monitors SavedVariables directory, 2s debounce
- **Poll timer**: every 5 minutes as fallback
- **Manual**: "Sync Now" from tray menu

## Auto-Update (`updater.py`)

- Queries GitHub Releases API for latest version
- **GUI**: checks at startup + every 6 hours, shows popup
- **CLI**: logs a message after sync; `--update` to install
- **Exe mode**: downloads new exe, writes a `.cmd` swap script, exits
- **Pip mode**: Windows spawns detached batch script; Linux runs `pip install --upgrade` directly
- Cleans up `.bak`/`.update` files on startup
- Skips check if version is `"dev"`

---

## Configuration

### Config File

Stored at `%APPDATA%/ForgasGuildCalendar-Sync/config.json` (Windows) or `~/.config/ForgasGuildCalendar-Sync/config.json` (Linux):

| Key | Purpose |
|-----|---------|
| `wow_path` | WoW installation directory |
| `account_folder` | WTF account folder name |
| `guild_key` | Guild scope key (e.g. `Thunderstrike-Sauercrowd Community`) |
| `calendar_id` | Google Calendar ID |
| `timezone` | IANA timezone (default: `Europe/Berlin`) |
| `default_duration_hours` | Event duration (default: 3) |
| `log_level` | Logging verbosity (default: `ERROR`) |
| `event_mapping` | `{fgc_eventId: {google_id, revision, title}}` |
| `discord_bot_token` | Discord bot token (optional) |
| `discord_guild_id` | Discord server ID (optional) |
| `discord_forum_id` | Forum channel ID (optional) |
| `discord_message_mapping` | `{fgc_eventId: {channel_id, message_ids, pinged[]}}` |

### Config Transactions

`Config.begin_transaction()` / `commit_transaction()` / `rollback_transaction()` buffer writes during setup so cancelling doesn't leave partial config on disk.

### Credential Files

- `token.json` — Google OAuth2 token (auto-refreshed)
- `client_secrets.json` — Google OAuth client ID (looked up next to project root first, then AppData)

---

## Development

### Versioning

Semantic Versioning (`MAJOR.MINOR.PATCH`). Version lives in `pyproject.toml`. Releases are git tags (`git tag vX.Y.Z`).

| Bump | When |
|------|------|
| `PATCH` | Bug fixes |
| `MINOR` | New features, backwards compatible |
| `MAJOR` | Breaking changes (e.g. config format) |

### Release Checklist

1. `git log vX.Y.Z..HEAD --oneline` — review commits
2. Determine version bump
3. Update `pyproject.toml` version
4. Update `CLAUDE.md` if architecture or conventions changed
5. Commit: `chore: release vX.Y.Z`
6. Tag: `git tag vX.Y.Z` (triggers CI build + GitHub release)

### Commit Convention

Enforced by pre-commit hook (`conventional-pre-commit`):

```
<type>(<scope>): <description>

<body>

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

| Type | When |
|------|------|
| `feat` | New feature |
| `fix` | Bug fix |
| `refactor` | Restructuring without behavior change |
| `docs` | Documentation only |
| `chore` | Build, config, dependencies |
| `style` | Formatting (no logic change) |
| `test` | Adding or updating tests |

Rules: English, lowercase description, no trailing period. Each logically separate change gets its own commit. Never use `--no-verify` or force-push without explicit request.

### Code Quality

Pre-commit hooks: ruff lint + format, trailing whitespace, end-of-file, YAML/TOML validation, merge conflict detection, conventional commits.

CI pipeline (`lint.yml`): runs pre-commit + pytest with coverage upload to Codecov.

### Testing

- Tests in `tests/` using pytest
- Black-box style: test public function inputs/outputs, not internals
- Coverage excludes Qt-dependent modules (views, controllers, app.py)
- Run locally: `pytest tests/ -v`

---

## Code Review Rules

### Architecture

- Models must not import from services, controllers, or views
- Services must not import from controllers or views
- Controllers must not import from views (except to instantiate dialogs)
- Views must not call service functions directly

### Quality

- No magic numbers — use enums and named constants (e.g. `EXPIRED_EVENT_HOURS`, `_MESSAGE_SCAN_LIMIT`)
- Use `config.saved_variables_path` for path construction
- `SAVED_VARIABLES_FILENAME` lives in `services/config.py`

### Google Calendar

- Always verify events exist before assuming (externally deleted)
- Always search for duplicates before creating (lost mapping)
- Use `serverTimeMinutes` for time, never `serverHour`/`serverMinute` alone
- Filter to events where user's character is Signed or Confirmed

### Discord

- `pinged` list must only contain names that `ping_members` actually resolved
- Thread adoption must start with empty `pinged` list
- `message_ids` must not contain `channel_id` — keep thread ID and message metadata separate
- Before posting a new image, always try `find_image_message` to locate the original
- Deleting an already-deleted thread (404) must be handled silently

### UI

- Stylesheet follows system dark/light mode (`is_system_dark_mode()`)
- All dialogs use shared stylesheet from `views/styles.py`
- Views emit signals — controllers handle logic
