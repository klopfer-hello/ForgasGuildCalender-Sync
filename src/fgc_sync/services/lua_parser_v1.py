"""V1 (named-keys) reader for FGC_DB events.

Each event is a dict with explicit field names (``eventId``, ``title``, ...) and
participants live in ``event.participants[name]`` with named keys including
``group`` and ``slot`` inline.
"""

from __future__ import annotations

from fgc_sync.models.enums import Attendance
from fgc_sync.models.events import CalendarEvent, Participant


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
            if not isinstance(evt, dict) or "eventId" not in evt:
                continue

            participants = _parse_participants(evt.get("participants", {}))
            hour, minute = _parse_time(evt)

            result.append(
                CalendarEvent(
                    event_id=evt["eventId"],
                    title=evt.get("title", "Untitled"),
                    event_type=evt.get("type", ""),
                    raid=evt.get("raid", ""),
                    date=str(date_key),
                    server_hour=hour,
                    server_minute=minute,
                    comment=evt.get("comment", ""),
                    creator=evt.get("creator", ""),
                    revision=evt.get("revision", 0),
                    participants=participants,
                )
            )

    return result


def _parse_participants(raw: dict) -> list[Participant]:
    if not isinstance(raw, dict):
        return []
    participants = []
    for name, pdata in raw.items():
        if not isinstance(pdata, dict):
            continue
        att_value = pdata.get("attendance", 0)
        try:
            attendance = Attendance(att_value)
        except ValueError:
            attendance = Attendance.DECLINED
        participants.append(
            Participant(
                name=name,
                attendance=attendance,
                class_code=pdata.get("classCode", ""),
                role_code=pdata.get("roleCode", ""),
                comment=pdata.get("comment", ""),
                group=int(pdata.get("group", 0)),
                slot=int(pdata.get("slot", 0)),
                item_level=float(pdata.get("itemLevel", 0)),
            )
        )
    return participants


def _parse_time(evt: dict) -> tuple[int, int]:
    """Extract hour and minute, preferring serverTimeMinutes."""
    time_minutes = evt.get("serverTimeMinutes")
    if time_minutes is not None:
        return int(time_minutes) // 60, int(time_minutes) % 60
    return evt.get("serverHour", 0), evt.get("serverMinute", 0)
