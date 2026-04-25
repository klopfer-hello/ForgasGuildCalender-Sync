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

### Layers

Source lives in `src/fgc_sync/`, layered (run `ls src/fgc_sync/` for the actual file list):

- **`models/`** — pure data classes and enums (`CalendarEvent`, `Participant`, `SyncResult`, `SyncPlan`, `UpdateInfo`, ...)
- **`i18n/`** — translation loader (`__init__.py`) + JSON files (one per language, auto-discovered)
- **`services/`** — business logic with no Qt: SavedVariables parsing, Discord REST, Google Calendar, image rendering, sync diffing, file watching, self-update
- **`controllers/`** — wires services to views (Qt signals, threading)
- **`views/`** — Qt widgets (tray, wizard, settings, preview); pure UI, emits signals only
- **`cli.py`** — headless entry point (no Qt import)
- **`app.py`** — QApplication bootstrap
- **`__main__.py`** — `python -m fgc_sync` dispatcher

### Dependency Rules

- **Models** and **i18n** depend on nothing in the project (stdlib only)
- **Services** depend on models, i18n, and external libraries
- **Controllers** depend on services, models, i18n
- **Views** depend on models, i18n, and Qt — never on services directly (controllers mediate)

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

## Internationalization (i18n)

User-facing text (CLI prompts, GUI labels, Discord thread names, image labels, tray notifications, log messages shown to the user) is loaded from JSON files at runtime. Logger output that targets developers/diagnostics stays English.

### File layout

- `src/fgc_sync/i18n/<code>.json` — one file per language (e.g. `en-UK.json`, `de-DE.json`)
- Codes are filenames (without `.json`) — `available_languages()` discovers them at runtime
- **Reference language** = `en-UK`. Every other file is validated against it on load; missing keys log a warning and fall through to the reference value
- `_meta.display_name` in each file is what the language picker shows
- Files are bundled via `pyproject.toml`:
  ```toml
  [tool.setuptools.package-data]
  "fgc_sync.i18n" = ["*.json"]
  ```

### Adding a new language

1. Copy `en-UK.json` to `<new-code>.json` (e.g. `fr-FR.json`)
2. Update `_meta.display_name` and translate every value
3. Restart the app — the language appears in the picker automatically. No code changes needed.
4. Run `pytest tests/test_i18n.py` to confirm all reference keys are present

### API

See docstrings in `i18n/__init__.py`. The functions you'll typically reach for: `t()`, `tl()`, `t_for()`, `t_all()`, `set_language()`, `available_languages()`, `display_name()`. `Config.__init__` and `Config.set("language", ...)` call `set_language` automatically — you rarely need to call it yourself.

### Key naming conventions

Dot-separated, organized by area:

- `common.*` — shared button/dialog labels (`ok`, `cancel`, `browse`, `error_title`, ...)
- `language.*` — language picker prompt/label
- `cli.*` — CLI prompts, argparse help, dry-run output, sync log messages
- `setup_wizard.*` — Qt setup wizard pages (`language`, `wow`, `discord`, `google`, `calendar`)
- `settings.*` — Settings dialog
- `preview.*` — Sync preview dialog (incl. `action_create` / `action_update` / `action_delete` for `SyncAction.value`)
- `tray.*` — System tray menu and notifications
- `app_controller.*` — Update prompts, About dialog
- `discord.*` — Discord output (weekday array, `thread_with_word`, ping labels)
- `weekly.*` — Weekly overview thread name, image text, summary text
- `roster.*` — Roster card image (full weekday array, stats line, section headers, role labels)

Format placeholders use named arguments (`{week:02d}`, `{path}`, `{count}`) so positional changes don't break translations.

### Cross-language dedup

Discord output is part of dedup logic — switching language must not orphan existing threads or trigger re-pings:

- **Per-event thread names**: `DiscordPoster._candidate_thread_names(event)` returns the name in every supported language. `find_existing_thread` matches against the full set
- **Weekly thread name**: `candidate_weekly_thread_names()` (in `weekly_overview.py`) does the same; `execute_weekly_sync` iterates over candidates when adopting an existing thread
- **Ping label scan**: `get_already_pinged_names` checks every supported language's `discord.ping_confirmed` and `discord.ping_newly_confirmed` prefix, so `Confirmed:` / `Bestätigt:` / `Newly confirmed:` / `Neu bestätigt:` are all recognized as prior bot pings

### Items intentionally NOT translated

- `RAID_SHORT_NAMES` (Kara, SSC, TK, ...) — WoW raid abbreviations, gaming jargon
- `CLASS_COLORS`, class names — WoW lore, not localized
- Internal log messages (`log.info`, `log.error`) and exceptions — developer/diagnostic
- Config keys and JSON field names
- Date/number formats (still German `dd.mm.yyyy` for both languages — matches WoW addon convention)

### Wiring

- `Config.__init__` calls `i18n.set_language(self.get("language"))` after loading config — so importing `Config` once sets up i18n for the whole process
- `Config.set("language", code)` triggers `i18n.set_language` so the rest of the running session sees the new value
- CLI `main()` constructs `Config()` *before* `argparse` so `--help` is rendered in the user's language
- The Qt `SetupWizard` has a `LanguagePage` as page 0; `validatePage` calls `i18n.set_language` and `wizard.retranslate_pages()` so subsequent pages re-render before the user sees them

---

## Google Calendar Sync

### Sync Logic (`sync_engine.py` → `execute_sync`)

Read the function for the actual flow. Invariants that have to hold:

- Only events where the user's character is **Signed** or **Confirmed** are synced
- **Adopt before create**: every event missing from the local mapping is searched in Google by title+date before a new event is created (recovers from lost mapping)
- **Verify before trust**: even when revision matches, the Google event is checked for existence — externally deleted events are re-created
- **Mass-deletion guard**: if WoW yields zero events but the mapping is non-empty, treat it as a parser failure and skip cleanup entirely
- Events absent from WoW *or* listed in `deletedEvents` are deleted from Google

### Google Calendar Event Format

- **Summary**: `[Type] Title (CharacterName)` e.g. `[Raid] Gruul mit Forga (Klopfbernd)`
- **Start**: date + serverTimeMinutes in configured timezone
- **Duration**: configurable, default 3 hours
- **Description**: event comment + participant counts + roster breakdown
- **Location**: raid name (titlecased)

---

## Discord Sync

### Sync Logic (`sync_engine.py` → `execute_discord_sync`)

Runs after Google Calendar sync. Posts events within `DISCORD_LOOKAHEAD_DAYS` (7) that have a confirmed roster (group assignments). Invariants:

- **Stale-data guard first** (`_is_local_data_stale`): if any remote roster-image filename has a higher `_t<unix_ts>` than the local SavedVariables mtime, abort the entire sync — another client has newer data
- **Adopt before create**: `find_existing_thread` matches by deterministic name (in *any* supported language) first, then falls back to scanning thread attachments
- **Adopted threads start with `pinged=[]`** — `get_already_pinged_names` reconstructs the actual pinged set from thread history
- **Ping the difference, not the union**: `to_ping = confirmed - (local_pinged ∪ history_pinged)`; only names that `ping_members` actually resolved are added back to `pinged`
- Cleanup deletes threads for removed events and events older than `EXPIRED_EVENT_HOURS` (24h); 404 on already-deleted threads is silently ignored

### Forum Threads

- **Thread name**: `<weekday> dd.mm. HH:MM — <Raid> <with-word> <creator>` (e.g. `Do 03.04. 20:00 — Gruul mit Forga` in `de-DE`, `Thu 03.04. 20:00 — Gruul with Forga` in `en-UK`). Built by `DiscordPoster._format_thread_name(event, language)`; the public `_thread_name` returns the active-language form. `_candidate_thread_names` returns every supported-language variant for cross-language dedup
- **Short raid names**: kara, gruul, maggi, ssc, tk, hyjal, bt, swp, za (`RAID_SHORT_NAMES`) — *not* translated
- **Starter message**: roster image posted as part of thread creation
- **Pings**: `discord.ping_confirmed` label on creation, `discord.ping_newly_confirmed` on updates (translated). `get_already_pinged_names` accepts every supported language's prefixes so language switches don't cause re-pings
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

## Weekly Raid Overview

### Sync Logic (`sync_engine.py` → `execute_weekly_sync`)

Maintains a **single permanent** forum thread (`get_weekly_thread_name()` — `Wöchentliche Raid Übersicht` / `Weekly Raid Overview`) with the current ISO week's schedule as a school-timetable image. Invariants:

- **Stale-data guard** identical to per-event sync (`_is_local_data_stale`)
- **No roster filter, no 7-day lookahead**: every guild event in the current ISO week (Mon–Sun) is included regardless of participation status (unlike per-event threads)
- **Thread name is constant**: only the starter message + image are PATCHed in place; the thread is never recreated per week
- **Cross-language adoption**: when no thread is in mapping, iterate `candidate_weekly_thread_names()` so an old-language thread is adopted instead of duplicated
- Skip when `{hash, week_key}` are both unchanged

### Image (`weekly_overview.py` → `render_weekly_overview`)

- **Header**: `Raid Übersicht — KW <nn> / <year>`, full date range (`dd.mm.yyyy – dd.mm.yyyy`), raid count
- **Grid**: 7 day columns (Mo–So) × hourly rows. Hour range is **dynamic** — `_determine_hour_range` picks `min(earliest_event, 17)` down to `max(latest_event_end, start+4)+1` (trailing labeled row for end clarity), capped at 03:00 next day
- **Event cell**: short name, time range (`20:00–22:30`), `RL: <leader>`, `Bestätigt: X` (= confirmed), `Angemeldet: X+Y` (= confirmed + signed)
- **Parallel raids**: greedy lane assignment per day column — overlapping raids sit side-by-side in equal-width lanes. Fonts shrink and labels abbreviate (`Best.`/`Angem.`) for 3+ lanes
- Duration constant: `WEEKLY_EVENT_DURATION_HOURS` (fractional supported)

### Starter Message Text

`format_weekly_summary(monday, num_events)` returns:

```
**Raid Übersicht — KW <nn> / <year>**
dd.mm.yyyy – dd.mm.yyyy
N Raid(s) geplant
```

(`en-UK` produces `Raid Overview — CW <nn> / <year>` and `N raid(s) scheduled`.)

Sent as the starter message `content` on create, re-sent on every PATCH so it tracks the current week.

### Mapping Schema (`discord_weekly_mapping`)

Single dict (not keyed by event id):

- `channel_id`: Discord thread ID (stable across weeks)
- `message_id`: starter message ID (stable — the image is PATCHed in place)
- `hash`: last rendered content hash
- `week_key`: ISO week string like `2026-W16`
- `sv_mtime`: SavedVariables mtime embedded in the image filename

### Filename

`weekly_<week_key>_h<hash>_t<sv_mtime>.png` — same `_h..._t...` convention as per-event roster images.

---

## CLI

- Entry point: `fgc-sync-cli` (or `python -m fgc_sync --headless`)
- Runs a single sync cycle and exits — designed for cron
- No Qt/PySide6 dependency
- Flags: `--dry-run`, `--discord-only`, `--weekly-only`, `--force`, `--export-code`, `--setup`, `--config-dir`, `--version`, `--about`, `--check-update`, `--update`
- Interactive setup on first run using `questionary` — first prompt is the language picker, then WoW path, account, guild, Discord, Google
- `--help` is rendered in the user's configured language (`Config()` is loaded before `argparse` is constructed)
- Handles git bash `/d/...` paths on Windows (`_normalize_path`)
- Config at `~/.config/ForgasGuildCalendar-Sync/config.json` (XDG)

### Dry-Run Mode

`--dry-run` uses `compute_sync_plan` (Google), `compute_discord_sync_plan` (Discord per-event), and `compute_weekly_sync_plan` (Discord weekly overview) to show planned actions without modifying any remote state or local config. Also writes a local `weekly_preview.png` to the config dir so the weekly overview can be eyeballed before deployment. Includes the stale-data guard check.

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
| `language` | UI / output language code (default: `en-UK`; e.g. `de-DE`). Setting this calls `i18n.set_language` |
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
| `discord_weekly_mapping` | `{channel_id, message_id, hash, week_key, sv_mtime}` — single entry for the permanent weekly-overview thread |

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

### Internationalization

- Every user-facing string goes through `t()` / `tl()` from `fgc_sync.i18n` — no hardcoded English or German in views, CLI, Discord output, or images
- Logger messages stay English (developer-facing). User-facing notifications and tray status do go through `t()`
- New keys go in `en-UK.json` first (the reference) and then in every other language file. The `test_i18n` validation will fail otherwise
- Format placeholders are named (`{path}`, `{count}`), never positional, so translators can reorder freely
- Don't import the same key in two places with two different fallbacks — define it once and reuse
- For any output that participates in dedup logic (Discord thread names, ping label scans), use the `t_all()` helper or build a candidate list across `available_languages()` so a language switch doesn't churn remote state

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

### Weekly Overview

- Thread name comes from `get_weekly_thread_name()` (active-language) — don't change it per week; only the starter message content and image change
- When adopting an existing thread, iterate `candidate_weekly_thread_names()` so a language switch picks up an old-language thread instead of creating a new one
- Both `execute_weekly_sync` and `compute_weekly_sync_plan` must respect the stale-data guard (`_is_local_data_stale`)
- `compute_weekly_hash` must cover every field the image displays, so content changes always trigger a PATCH. Translated labels are *not* part of the hash — image content depends on language but adopting the existing thread + PATCHing avoids churn
- `render_weekly_overview` must handle fractional `WEEKLY_EVENT_DURATION_HOURS` (e.g. 2.5) — coerce to int where values flow to canvas dimensions
- Lane assignment uses event start/end in minutes; parallel raids in the same day column must never overlap visually

### UI

- Stylesheet follows system dark/light mode (`is_system_dark_mode()`)
- All dialogs use shared stylesheet from `views/styles.py`
- Views emit signals — controllers handle logic
