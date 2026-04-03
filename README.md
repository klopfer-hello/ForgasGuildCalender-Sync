# FGC Calendar Sync

Syncs raid events from [Forga's Guild Calendar](https://github.com/ForgaNet/ForgasGuildCalendar) (WoW addon) to Google Calendar. Runs as a Windows system tray app — auto-syncs when WoW writes SavedVariables and polls every 5 minutes.

## Setup

### Prerequisites

- Python 3.12+
- A Google Cloud project with the **Google Calendar API** enabled and an OAuth **Desktop** client ID (`client_secrets.json`)

### Install

```bash
pip install git+https://github.com/klopfer-hello/ForgasGuildCalender-Sync.git
```

Place `client_secrets.json` in `%APPDATA%\ForgasGuildCalendar-Sync\`.

### Run

```
fgc-sync
```

On first launch, a setup wizard guides you through selecting your WoW directory, logging into Google, and picking a calendar.

### Auto-start with Windows

Right-click the tray icon and enable **"Start with Windows"**.

## How it works

1. Reads `FGC_DB` from WoW's SavedVariables file
2. Filters to events where your character is signed up or confirmed
3. Creates/updates/deletes Google Calendar events to match
4. Watches the SavedVariables file for changes (triggers on logout, `/reload`, character switch)

## Disclaimer

This is an independent, community-made tool. It is **not affiliated with, endorsed by, or related to** the developers or maintainers of the [Forga's Guild Calendar](https://github.com/ForgaNet/ForgasGuildCalendar) addon. All trademarks and product names belong to their respective owners.
