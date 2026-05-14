"""Parse WoW SavedVariables for ForgasGuildCalendar (FGC_DB).

Façade that exposes a stable API on top of four storage layouts:

* **v1** — named-keys events, ``group``/``slot`` inline on each participant
* **v2** — FGC2 packed positional arrays plus a separate ``rosterByPlayer``
  table at ``event[13]``
* **v3** — named-keys events (like v1) but with the roster externalized into
  ``event["roster"]["byPlayer"]`` (named keys, not positional). Companion
  signal: the addon prefixes ``profiles[].guildScoped`` keys with ``V3|``
* **v4** — current addon namespace (``V4|<base>``). Canonical shape is
  packed-positional (the addon's ``PackEventRecord`` rewrites records into
  1-based Lua arrays with metatable-based named accessors), but in practice
  V4 buckets can hold both packed *and* named-keys events because packing
  only happens when a record passes through the mutation path.
  ``lua_parser_v4`` sniffs each event individually

The dispatch strategy is two-tier:

1. **Namespace resolution** — :func:`_resolve_guild_key` strips any
   ``V3|`` / ``V4|`` prefix from the configured ``guild_key`` and probes
   ``V4|<base>`` first, falling back to ``V3|<base>`` and finally the bare
   key. So a stale ``V3|…`` config keeps working after the addon's V3→V4
   bump, and rollback (empty/missing V4) routes reads back to V3 without
   code changes. V3 is retained indefinitely as the rollback safety copy
2. **Layout dispatch** — once the namespace is resolved, ``V4|`` always
   routes to :mod:`lua_parser_v4` (which does its own per-event shape sniff
   internally). For everything else, the first parseable event is content-
   sniffed (list / dict-with-int-1 → v2, dict-with-eventId + roster.byPlayer
   → v3, dict-with-eventId only → v1). The ``_fgcEventStorageVersion`` flag
   is intentionally *not* used — during the FGC2 rollout it was observed set
   to ``2`` while events were still named-keys, so on-disk shape is the only
   reliable signal. The ``V3|`` guild-key prefix is consulted only when the
   resolved events table is empty (no shape to sniff)

Top-level shape (``profileKeys``, ``profiles``, ``sync.deletedEvents``) is
identical across versions, so only :func:`extract_events` branches on layout.
"""

from __future__ import annotations

import re
from pathlib import Path

from slpp import slpp as lua

from fgc_sync.models.events import CalendarEvent
from fgc_sync.services import (
    lua_parser_v1,
    lua_parser_v2,
    lua_parser_v3,
    lua_parser_v4,
)

# Match production (FGC_DB) and the parallel-running FGC2 test build (FGC2_DB).
_FGC_DB_PATTERN = re.compile(r"^FGC2?_DB\s*=\s*", re.MULTILINE)

# Namespace prefixes the addon has used on ``profiles[].guildScoped`` keys,
# ordered from newest (preferred) to oldest. V4 is the current namespace; V3 is
# retained as a rollback safety copy. Bare keys (no prefix) cover legacy data
# from before namespacing existed.
_NAMESPACE_PREFIXES: tuple[str, ...] = ("V4|", "V3|")


def parse_saved_variables(file_path: Path) -> dict:
    """Parse FGC_DB from a SavedVariables Lua file."""
    text = file_path.read_text(encoding="utf-8")
    match = _FGC_DB_PATTERN.search(text)
    if not match:
        raise ValueError("Could not find FGC_DB in SavedVariables file")
    return lua.decode(text[match.end() :])


def _strip_namespace_prefix(guild_key: str) -> str:
    """Return ``guild_key`` with any ``V3|`` / ``V4|`` prefix removed."""
    if not isinstance(guild_key, str):
        return ""
    for prefix in _NAMESPACE_PREFIXES:
        if guild_key.startswith(prefix):
            return guild_key[len(prefix) :]
    return guild_key


def _resolve_guild_key(db: dict, configured: str, profile: str) -> str:
    """Resolve ``configured`` to whichever namespace actually holds events.

    Strips any prefix and probes ``V4|<base>`` then ``V3|<base>``, returning
    the first candidate whose ``events`` table is non-empty. Falls back to the
    configured key as-is when neither prefix is populated — that covers fresh
    installs, bare-key legacy data, and the case where the addon has
    bootstrapped V4 but not written any events yet.
    """
    guild_scoped = db.get("profiles", {}).get(profile, {}).get("guildScoped", {})
    if not isinstance(guild_scoped, dict):
        return configured

    base = _strip_namespace_prefix(configured)
    for prefix in _NAMESPACE_PREFIXES:
        candidate = f"{prefix}{base}"
        scoped = guild_scoped.get(candidate)
        if not isinstance(scoped, dict):
            continue
        events = scoped.get("events")
        if isinstance(events, dict) and events:
            return candidate

    return configured


def _detect_layout(db: dict, guild_key: str, profile: str) -> str:
    """Return ``"v1"``, ``"v2"``, ``"v3"``, or ``"v4"``.

    A ``V4|`` guild key short-circuits to ``"v4"`` — that namespace is the
    addon's current home and may hold a mix of packed and named-keys events
    within the same bucket, so per-event sniffing happens inside the v4
    parser rather than at this façade. For other namespaces, the first
    parseable event is content-sniffed:

    * Lua list / Python list → packed positional (v2)
    * dict containing integer key ``1`` and no ``"eventId"`` → packed (v2)
    * dict containing ``"eventId"`` with a populated ``roster.byPlayer`` →
      externalized named-keys roster (v3)
    * dict containing ``"eventId"`` without a ``roster.byPlayer`` → inline
      named-keys (v1)

    When the events table is empty, the guild-key namespace prefix is used
    as a fallback hint; otherwise defaults to v1.
    """
    if isinstance(guild_key, str) and guild_key.startswith("V4|"):
        return "v4"

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
    """Fallback layout hint from the addon's guild-key namespace prefix.

    ``V4|`` maps to ``"v4"`` and ``V3|`` to ``"v3"`` — only consulted when
    the events table is empty (no shape to sniff). Bare keys default to v1.
    """
    if isinstance(guild_key, str):
        if guild_key.startswith("V4|"):
            return "v4"
        if guild_key.startswith("V3|"):
            return "v3"
    return "v1"


def extract_events(
    db: dict, guild_key: str, profile: str = "Default"
) -> list[CalendarEvent]:
    """Extract calendar events for a guild from parsed FGC_DB.

    Resolves the configured ``guild_key`` to whichever namespace actually
    holds events (V4 preferred, V3 fallback) so a stale config keeps working
    after the addon's V3→V4 bump and rollbacks route automatically.
    """
    resolved = _resolve_guild_key(db, guild_key, profile)
    layout = _detect_layout(db, resolved, profile)
    if layout == "v4":
        return lua_parser_v4.extract_events(db, resolved, profile)
    if layout == "v2":
        return lua_parser_v2.extract_events(db, resolved, profile)
    if layout == "v3":
        return lua_parser_v3.extract_events(db, resolved, profile)
    return lua_parser_v1.extract_events(db, resolved, profile)


def get_deleted_event_ids(
    db: dict, guild_key: str, profile: str = "Default"
) -> set[str]:
    """Return set of eventIds that have been deleted.

    Uses the same V4/V3 namespace resolution as :func:`extract_events` so the
    deletion set always comes from the same namespace as the live events.
    """
    resolved = _resolve_guild_key(db, guild_key, profile)
    deleted = (
        db.get("profiles", {})
        .get(profile, {})
        .get("guildScoped", {})
        .get(resolved, {})
        .get("sync", {})
        .get("deletedEvents", {})
    )
    return set(deleted.keys()) if isinstance(deleted, dict) else set()


def list_guild_keys(db: dict, profile: str = "Default") -> list[str]:
    """Return available guild keys from the parsed DB, one entry per base.

    During the V3→V4 namespace bump both ``V3|<base>`` and ``V4|<base>`` exist
    side by side (V3 is kept as a rollback safety copy). Deduplicate by base
    name so the setup picker doesn't surface duplicates — V4 wins over V3
    wins over the bare key.
    """
    guild_scoped = db.get("profiles", {}).get(profile, {}).get("guildScoped", {})
    if not isinstance(guild_scoped, dict):
        return []

    def priority(key: str) -> int:
        for i, prefix in enumerate(_NAMESPACE_PREFIXES):
            if key.startswith(prefix):
                return i
        return len(_NAMESPACE_PREFIXES)

    preferred: dict[str, str] = {}
    for key in guild_scoped:
        if not isinstance(key, str):
            continue
        base = _strip_namespace_prefix(key)
        if base not in preferred or priority(key) < priority(preferred[base]):
            preferred[base] = key

    return list(preferred.values())


def list_character_names(db: dict) -> list[str]:
    """Return character names from profileKeys (without realm suffix)."""
    names = []
    for full_name in db.get("profileKeys", {}):
        name = full_name.split(" - ")[0].strip()
        if name and name not in names:
            names.append(name)
    return names
