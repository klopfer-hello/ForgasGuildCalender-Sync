"""V2 (positional/packed) reader for FGC_DB events.

Each event is an array record (Lua 1-based)::

    event[1]  = eventId
    event[2]  = type
    event[3]  = raid
    event[4]  = title
    event[5]  = comment
    event[6]  = creator
    event[7]  = serverTimeMinutes
    event[8]  = revision
    event[9]  = updatedAt
    event[10] = updatedBy
    event[11] = participantsByPlayer  (dict: name -> packed participant)
    event[12] = reservesByPlayer
    event[13] = rosterByPlayer        (dict: name -> packed roster entry)

Each participant array::

    participant[1] = attendance
    participant[2] = classCode
    participant[3] = roleCode
    participant[4] = specIndex
    participant[5] = itemLevel
    participant[6] = comment

Each roster entry array::

    roster[1] = group
    roster[2] = slot
"""

from __future__ import annotations

from fgc_sync.models.enums import Attendance
from fgc_sync.models.events import CalendarEvent, Participant

# Lua-style 1-based indices (the _get helper translates as needed).
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

_P_ATTENDANCE = 1
_P_CLASS = 2
_P_ROLE = 3
_P_ITEM_LEVEL = 5
_P_COMMENT = 6

_R_GROUP = 1
_R_SLOT = 2


def _get(arr, lua_idx: int, default=None):
    """Index into a packed Lua table that slpp may return as list or dict.

    ``lua_idx`` is the 1-based Lua index. Lists are 0-based in Python.
    """
    if isinstance(arr, list):
        py_idx = lua_idx - 1
        if 0 <= py_idx < len(arr):
            return arr[py_idx]
        return default
    if isinstance(arr, dict):
        return arr.get(lua_idx, default)
    return default


def extract_events(
    db: dict, guild_key: str, profile: str = "Default"
) -> list[CalendarEvent]:
    events_by_date = (
        db.get("profiles", {})
        .get(profile, {})
        .get("guildScoped", {})
        .get(guild_key, {})
        .get("events", {})
    )

    result = []
    for date_key, events in events_by_date.items():
        if not isinstance(events, (list, dict)):
            continue

        event_list = events.values() if isinstance(events, dict) else events
        for evt in event_list:
            if not isinstance(evt, (list, dict)):
                continue

            event_id = _get(evt, _E_EVENT_ID)
            if not event_id:
                continue

            roster_raw = _get(evt, _E_ROSTER, {})
            roster = roster_raw if isinstance(roster_raw, dict) else {}

            participants_raw = _get(evt, _E_PARTICIPANTS, {})
            participants = _parse_participants(participants_raw, roster)

            hour, minute = _parse_time(_get(evt, _E_TIME_MIN))

            title = _get(evt, _E_TITLE) or "Untitled"

            result.append(
                CalendarEvent(
                    event_id=str(event_id),
                    title=title,
                    event_type=_get(evt, _E_TYPE, "") or "",
                    raid=_get(evt, _E_RAID, "") or "",
                    date=str(date_key),
                    server_hour=hour,
                    server_minute=minute,
                    comment=_get(evt, _E_COMMENT, "") or "",
                    creator=_get(evt, _E_CREATOR, "") or "",
                    revision=int(_get(evt, _E_REVISION, 0) or 0),
                    participants=participants,
                )
            )

    return result


def _parse_participants(raw, roster: dict) -> list[Participant]:
    if not isinstance(raw, dict):
        return []
    participants = []
    for name, pdata in raw.items():
        if not isinstance(pdata, (list, dict)):
            continue

        att_value = _get(pdata, _P_ATTENDANCE, 0)
        try:
            attendance = Attendance(int(att_value))
        except (ValueError, TypeError):
            attendance = Attendance.DECLINED

        rentry = roster.get(name)
        group = int(_get(rentry, _R_GROUP, 0) or 0) if rentry is not None else 0
        slot = int(_get(rentry, _R_SLOT, 0) or 0) if rentry is not None else 0

        participants.append(
            Participant(
                name=name,
                attendance=attendance,
                class_code=_get(pdata, _P_CLASS, "") or "",
                role_code=_get(pdata, _P_ROLE, "") or "",
                comment=_get(pdata, _P_COMMENT, "") or "",
                group=group,
                slot=slot,
                item_level=float(_get(pdata, _P_ITEM_LEVEL, 0) or 0),
            )
        )
    return participants


def _parse_time(time_minutes) -> tuple[int, int]:
    if time_minutes is None:
        return 0, 0
    try:
        m = int(time_minutes)
    except (ValueError, TypeError):
        return 0, 0
    return m // 60, m % 60
