"""V4 (post-namespace-bump) reader for FGC_DB events.

V4 is the addon's current guild-scope namespace (``V4|<realm>-<guild>``).
The canonical V4 on-disk shape is **packed-positional**: the addon's
``PackEventRecord`` rewrites each event into a 1-based Lua array with
metatable-based named accessors, so reads like ``record.eventId`` resolve via
``rawget(record, 1)`` while serialization writes ``[1] = ..., [2] = ...``.
The field index tables mirror ``Core-PackedStorage.lua``::

    EVENT_FIELD_INDEX = {
        eventId = 1, type = 2, raid = 3, title = 4, comment = 5,
        creator = 6, serverTimeMinutes = 7, revision = 8, updatedAt = 9,
        updatedBy = 10, participants = 11, reserves = 12, roster = 13,
    }
    PARTICIPANT_FIELD_INDEX = {
        attendance = 1, classCode = 2, roleCode = 3, specIndex = 4,
        itemLevel = 5, comment = 6, firstSignupAt = 7, revision = 8,
        updatedAt = 9, updatedBy = 10,
    }
    ROSTER_FIELD_INDEX = {
        group = 1, slot = 2, revision = 3, updatedAt = 4, updatedBy = 5,
    }

In practice a V4 events bucket can hold a *mix* of shapes:

* **Packed** events that have already passed through ``PackEventRecord``
* **Named-keys** events that haven't been touched since the migration from
  V3 — these look identical to the v3 layout (named-keys dict with the roster
  externalized into ``event["roster"]["byPlayer"]``)

Per-event shape sniffing inside :func:`extract_events` routes each record to
the right decoder, so a mixed bucket parses correctly. The packed shape
extends v2 — the field indices match on every slot v2 read, so a v4-packed
event would also decode under the v2 parser. The dedicated v4 parser exists
so v4-specific fields (e.g. ``firstSignupAt`` on participants, the new
``reserves`` table) have a single owning module if/when the sync needs them.
"""

from __future__ import annotations

from fgc_sync.models.enums import Attendance
from fgc_sync.models.events import CalendarEvent, Participant

# Event positional slots (1-based, mirrors EVENT_FIELD_INDEX in the addon).
_E_EVENT_ID = 1
_E_TYPE = 2
_E_RAID = 3
_E_TITLE = 4
_E_COMMENT = 5
_E_CREATOR = 6
_E_TIME_MIN = 7
_E_REVISION = 8
_E_PARTICIPANTS = 11
_E_ROSTER = 13

# Participant positional slots. Slot 7 = firstSignupAt is new in v4; calendar
# sync ignores it but the index documents the v4 contract.
_P_ATTENDANCE = 1
_P_CLASS = 2
_P_ROLE = 3
_P_ITEM_LEVEL = 5
_P_COMMENT = 6

# Roster positional slots. Slots 3-5 (revision/updatedAt/updatedBy) are
# version metadata the addon uses for sync conflict resolution.
_R_GROUP = 1
_R_SLOT = 2


def extract_events(
    db: dict, guild_key: str, profile: str = "Default"
) -> list[CalendarEvent]:
    """Extract v4-namespace events, handling both packed and named-keys shapes."""
    events_by_date = (
        db.get("profiles", {})
        .get(profile, {})
        .get("guildScoped", {})
        .get(guild_key, {})
        .get("events", {})
    )

    result: list[CalendarEvent] = []
    for date_key, events in events_by_date.items():
        if not isinstance(events, (list, dict)):
            continue

        event_list = events.values() if isinstance(events, dict) else events
        for evt in event_list:
            parsed = _parse_event(evt, str(date_key))
            if parsed is not None:
                result.append(parsed)

    return result


def _parse_event(evt, date_key: str) -> CalendarEvent | None:
    if isinstance(evt, dict) and "eventId" in evt:
        return _parse_named_event(evt, date_key)
    if _looks_packed(evt):
        return _parse_packed_event(evt, date_key)
    return None


def _looks_packed(evt) -> bool:
    """Return True if ``evt`` is a packed-positional record."""
    if isinstance(evt, list):
        return True
    return isinstance(evt, dict) and 1 in evt and "eventId" not in evt


# --- Packed-positional decoding (canonical v4) ---


def _parse_packed_event(evt, date_key: str) -> CalendarEvent | None:
    event_id = _lua_get(evt, _E_EVENT_ID)
    if not event_id:
        return None

    roster_raw = _lua_get(evt, _E_ROSTER, {})
    roster_by_player = roster_raw if isinstance(roster_raw, dict) else {}

    participants_raw = _lua_get(evt, _E_PARTICIPANTS, {})
    participants = _parse_packed_participants(participants_raw, roster_by_player)

    hour, minute = _split_minutes(_lua_get(evt, _E_TIME_MIN))

    return CalendarEvent(
        event_id=str(event_id),
        title=_lua_get(evt, _E_TITLE) or "Untitled",
        event_type=_lua_get(evt, _E_TYPE, "") or "",
        raid=_lua_get(evt, _E_RAID, "") or "",
        date=date_key,
        server_hour=hour,
        server_minute=minute,
        comment=_lua_get(evt, _E_COMMENT, "") or "",
        creator=_lua_get(evt, _E_CREATOR, "") or "",
        revision=int(_lua_get(evt, _E_REVISION, 0) or 0),
        participants=participants,
    )


def _parse_packed_participants(raw, roster_by_player) -> list[Participant]:
    if not isinstance(raw, dict):
        return []
    participants = []
    for name, pdata in raw.items():
        if not isinstance(pdata, (list, dict)):
            continue

        try:
            attendance = Attendance(int(_lua_get(pdata, _P_ATTENDANCE, 0) or 0))
        except (ValueError, TypeError):
            attendance = Attendance.DECLINED

        rentry = (
            roster_by_player.get(name) if isinstance(roster_by_player, dict) else None
        )
        group = int(_lua_get(rentry, _R_GROUP, 0) or 0) if rentry is not None else 0
        slot = int(_lua_get(rentry, _R_SLOT, 0) or 0) if rentry is not None else 0

        participants.append(
            Participant(
                name=name,
                attendance=attendance,
                class_code=_lua_get(pdata, _P_CLASS, "") or "",
                role_code=_lua_get(pdata, _P_ROLE, "") or "",
                comment=_lua_get(pdata, _P_COMMENT, "") or "",
                group=group,
                slot=slot,
                item_level=float(_lua_get(pdata, _P_ITEM_LEVEL, 0) or 0),
            )
        )
    return participants


def _lua_get(arr, lua_idx: int, default=None):
    """Index a 1-based Lua table that slpp may return as Python list or dict."""
    if isinstance(arr, list):
        py_idx = lua_idx - 1
        if 0 <= py_idx < len(arr):
            return arr[py_idx]
        return default
    if isinstance(arr, dict):
        return arr.get(lua_idx, default)
    return default


def _split_minutes(time_minutes) -> tuple[int, int]:
    if time_minutes is None:
        return 0, 0
    try:
        m = int(time_minutes)
    except (ValueError, TypeError):
        return 0, 0
    return m // 60, m % 60


# --- Named-keys decoding (V4 records not yet packed) ---


def _parse_named_event(evt: dict, date_key: str) -> CalendarEvent:
    roster_raw = evt.get("roster", {})
    roster_by_player = (
        roster_raw.get("byPlayer", {}) if isinstance(roster_raw, dict) else {}
    )
    if not isinstance(roster_by_player, dict):
        roster_by_player = {}

    participants = _parse_named_participants(
        evt.get("participants", {}), roster_by_player
    )
    hour, minute = _named_time(evt)

    return CalendarEvent(
        event_id=evt["eventId"],
        title=evt.get("title", "Untitled"),
        event_type=evt.get("type", ""),
        raid=evt.get("raid", ""),
        date=date_key,
        server_hour=hour,
        server_minute=minute,
        comment=evt.get("comment", ""),
        creator=evt.get("creator", ""),
        revision=evt.get("revision", 0),
        participants=participants,
    )


def _parse_named_participants(raw: dict, roster_by_player: dict) -> list[Participant]:
    if not isinstance(raw, dict):
        return []
    participants = []
    for name, pdata in raw.items():
        if not isinstance(pdata, dict):
            continue
        try:
            attendance = Attendance(pdata.get("attendance", 0))
        except ValueError:
            attendance = Attendance.DECLINED

        rentry = roster_by_player.get(name)
        if isinstance(rentry, dict):
            group = int(rentry.get("group", 0) or 0)
            slot = int(rentry.get("slot", 0) or 0)
        else:
            group = 0
            slot = 0

        participants.append(
            Participant(
                name=name,
                attendance=attendance,
                class_code=pdata.get("classCode", ""),
                role_code=pdata.get("roleCode", ""),
                comment=pdata.get("comment", ""),
                group=group,
                slot=slot,
                item_level=float(pdata.get("itemLevel", 0)),
            )
        )
    return participants


def _named_time(evt: dict) -> tuple[int, int]:
    time_minutes = evt.get("serverTimeMinutes")
    if time_minutes is not None:
        return int(time_minutes) // 60, int(time_minutes) % 60
    return evt.get("serverHour", 0), evt.get("serverMinute", 0)
