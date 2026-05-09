"""Parse WoW SavedVariables for ForgasGuildCalendar (FGC_DB).

Façade that exposes a stable API on top of three storage layouts:

* **v1** — named-keys events, ``group``/``slot`` inline on each participant
* **v2** — FGC2 packed positional arrays plus a separate ``rosterByPlayer``
  table at ``event[13]``
* **v3** — named-keys events (like v1) but with the roster externalized into
  ``event["roster"]["byPlayer"]`` (named keys, not positional). Companion
  signal: the addon prefixes ``profiles[].guildScoped`` keys with ``V3|``.

Dispatch is **per-guild, content-based**: the first parseable event in the
guild's events table is sniffed to decide the layout. The
``_fgcEventStorageVersion`` flag is intentionally *not* used — during the FGC2
rollout the addon has been observed setting that flag to ``2`` while still
writing events in named-keys form, so the on-disk shape is the only reliable
signal. The ``V3|`` guild-key prefix is consulted only when the events table
is empty (no shape to sniff). Top-level shape (``profileKeys``, ``profiles``,
``sync.deletedEvents``) is identical across versions, so only
:func:`extract_events` branches.
"""

from __future__ import annotations

import re
from pathlib import Path

from slpp import slpp as lua

from fgc_sync.models.events import CalendarEvent
from fgc_sync.services import lua_parser_v1, lua_parser_v2, lua_parser_v3

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
    """Return ``"v1"``, ``"v2"``, or ``"v3"``.

    Sniffs the first parseable event in the guild's events table:

    * Lua list / Python list → packed positional (v2)
    * dict containing integer key ``1`` and no ``"eventId"`` → packed (v2)
    * dict containing ``"eventId"`` with a populated ``roster.byPlayer`` →
      externalized named-keys roster (v3)
    * dict containing ``"eventId"`` without a ``roster.byPlayer`` → inline
      named-keys (v1)

    When the events table is empty, the addon's ``V3|`` guild-key prefix is
    used as a fallback hint; otherwise defaults to v1.
    """
    events_by_date = (
        db.get("profiles", {})
        .get(profile, {})
        .get("guildScoped", {})
        .get(guild_key, {})
        .get("events", {})
    )
    if not isinstance(events_by_date, dict):
        return _layout_from_guild_key(guild_key)

    for events in events_by_date.values():
        if not isinstance(events, (list, dict)):
            continue
        event_list = events.values() if isinstance(events, dict) else events
        for evt in event_list:
            if isinstance(evt, list):
                return "v2"
            if isinstance(evt, dict):
                if "eventId" in evt:
                    roster = evt.get("roster")
                    if isinstance(roster, dict) and isinstance(
                        roster.get("byPlayer"), dict
                    ):
                        return "v3"
                    return "v1"
                if 1 in evt:
                    return "v2"

    return _layout_from_guild_key(guild_key)


def _layout_from_guild_key(guild_key: str) -> str:
    """Fallback layout hint from the addon's guild-key namespace prefix."""
    if isinstance(guild_key, str) and guild_key.startswith("V3|"):
        return "v3"
    return "v1"


def extract_events(
    db: dict, guild_key: str, profile: str = "Default"
) -> list[CalendarEvent]:
    """Extract calendar events for a guild from parsed FGC_DB."""
    layout = _detect_layout(db, guild_key, profile)
    if layout == "v2":
        return lua_parser_v2.extract_events(db, guild_key, profile)
    if layout == "v3":
        return lua_parser_v3.extract_events(db, guild_key, profile)
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
