"""Parse WoW SavedVariables for ForgasGuildCalendar (FGC_DB).

Façade that exposes a stable API on top of two storage layouts:

* **v1** — named-keys (``event.eventId``, ``event.title``, …)
* **v2** — packed positional arrays plus a separate ``rosterByPlayer`` table

Dispatch is **per-guild, content-based**: the first parseable event in the
guild's events table is sniffed to decide the layout. The
``_fgcEventStorageVersion`` flag is intentionally *not* used — during the FGC2
rollout the addon has been observed setting that flag to ``2`` while still
writing events in named-keys form, so the on-disk shape is the only reliable
signal. Top-level shape (``profileKeys``, ``profiles``, ``sync.deletedEvents``)
is identical across versions, so only :func:`extract_events` branches.
"""

from __future__ import annotations

import re
from pathlib import Path

from slpp import slpp as lua

from fgc_sync.models.events import CalendarEvent
from fgc_sync.services import lua_parser_v1, lua_parser_v2

# Match production (FGC_DB) and the parallel-running FGC2 test build (FGC2_DB).
_FGC_DB_PATTERN = re.compile(r"^FGC2?_DB\s*=\s*", re.MULTILINE)


def parse_saved_variables(file_path: Path) -> dict:
    """Parse FGC_DB from a SavedVariables Lua file."""
    text = file_path.read_text(encoding="utf-8")
    match = _FGC_DB_PATTERN.search(text)
    if not match:
        raise ValueError("Could not find FGC_DB in SavedVariables file")
    return lua.decode(text[match.end() :])


def _detect_layout(db: dict, guild_key: str, profile: str) -> str:
    """Return ``"v1"`` (named-keys) or ``"v2"`` (packed positional).

    Sniffs the first parseable event in the guild's events table:

    * Lua list / Python list → packed positional (v2)
    * dict containing ``"eventId"`` → named-keys (v1)
    * dict containing integer key ``1`` and no ``"eventId"`` → packed (v2)

    Defaults to v1 when nothing parseable is found — v1 is also the safe
    fallback when the events table is empty.
    """
    events_by_date = (
        db.get("profiles", {})
        .get(profile, {})
        .get("guildScoped", {})
        .get(guild_key, {})
        .get("events", {})
    )
    if not isinstance(events_by_date, dict):
        return "v1"

    for events in events_by_date.values():
        if not isinstance(events, (list, dict)):
            continue
        event_list = events.values() if isinstance(events, dict) else events
        for evt in event_list:
            if isinstance(evt, list):
                return "v2"
            if isinstance(evt, dict):
                if "eventId" in evt:
                    return "v1"
                if 1 in evt:
                    return "v2"
    return "v1"


def extract_events(
    db: dict, guild_key: str, profile: str = "Default"
) -> list[CalendarEvent]:
    """Extract calendar events for a guild from parsed FGC_DB."""
    if _detect_layout(db, guild_key, profile) == "v2":
        return lua_parser_v2.extract_events(db, guild_key, profile)
    return lua_parser_v1.extract_events(db, guild_key, profile)


def get_deleted_event_ids(
    db: dict, guild_key: str, profile: str = "Default"
) -> set[str]:
    """Return set of eventIds that have been deleted."""
    deleted = (
        db.get("profiles", {})
        .get(profile, {})
        .get("guildScoped", {})
        .get(guild_key, {})
        .get("sync", {})
        .get("deletedEvents", {})
    )
    return set(deleted.keys()) if isinstance(deleted, dict) else set()


def list_guild_keys(db: dict, profile: str = "Default") -> list[str]:
    """Return available guild keys from the parsed DB."""
    guild_scoped = db.get("profiles", {}).get(profile, {}).get("guildScoped", {})
    return list(guild_scoped.keys())


def list_character_names(db: dict) -> list[str]:
    """Return character names from profileKeys (without realm suffix)."""
    names = []
    for full_name in db.get("profileKeys", {}):
        name = full_name.split(" - ")[0].strip()
        if name and name not in names:
            names.append(name)
    return names
