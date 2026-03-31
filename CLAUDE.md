# ForgasGuildCalendar-Sync – Project Context for Claude

## Overview

System tray companion tool for the **Forga's Guild Calendar** WoW addon. Reads raid/event data from the addon's SavedVariables file and syncs it to Google Calendar. Runs as a background process with file watching and periodic polling.

## Environment

- Python >= 3.12 (tested on 3.14)
- PySide6 for UI (system tray + dialogs)
- Windows-only (system tray, Windows startup integration, registry-based theme detection)
- Installed via `pip install -e .` — entry point: `fgc-sync` (gui-scripts, uses `pythonw.exe`)

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
│   └── sync.py      #   SyncResult, SyncPlanEntry, SyncPlan
│
├── services/        # Business logic (no UI, no Qt)
│   ├── config.py    #   JSON config in %APPDATA%
│   ├── lua_parser.py        #   Parse FGC_DB SavedVariables
│   ├── google_calendar.py   #   OAuth2 + Calendar CRUD
│   ├── sync_engine.py       #   Diff & sync (create/update/delete)
│   └── file_watcher.py      #   Watchdog observer with debounce
│
├── controllers/     # C — Mediates views and services
│   ├── app_controller.py    #   Orchestrates lifecycle, wires signals
│   └── sync_controller.py   #   QThread sync, preview computation
│
├── views/           # V — Pure Qt UI, emits signals
│   ├── styles.py            #   System-aware light/dark stylesheet
│   ├── tray_icon.py         #   System tray icon + menu
│   ├── setup_wizard.py      #   First-run: WoW path → Google login → calendar
│   ├── settings_dialog.py   #   Post-setup config changes
│   └── preview_dialog.py    #   Sync preview table
│
├── app.py           # QApplication bootstrap
└── __main__.py      # python -m fgc_sync
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

### Auto-sync Triggers

- **File watcher**: watchdog monitors SavedVariables directory, 2s debounce
- **Poll timer**: every 5 minutes as fallback
- **Manual**: "Sync Now" from tray menu

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

### Credential Files

- `%APPDATA%/ForgasGuildCalendar-Sync/token.json` — OAuth2 token (auto-refreshed)
- `client_secrets.json` — Google OAuth client ID (looked up next to project root first, then %APPDATA%)

## File Structure

```
ForgasGuildCalendar-Sync/
├── pyproject.toml           # Package config, dependencies, entry point
├── CLAUDE.md                # This file
├── .gitignore
├── client_secrets.json      # Google OAuth (gitignored)
├── src/fgc_sync/            # Package source (see Architecture above)
├── scripts/
│   └── create_shortcut.ps1  # Windows startup shortcut helper
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
4. Commit: `chore: release vX.Y.Z`
5. Tag: `git tag vX.Y.Z`

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

### UI

- Stylesheet must follow system dark/light mode (`is_system_dark_mode()`)
- All dialogs use the shared stylesheet from `views/styles.py`
- Views emit signals for user actions — controllers handle the logic
