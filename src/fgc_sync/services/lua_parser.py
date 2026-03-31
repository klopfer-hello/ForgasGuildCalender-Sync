"""Parse WoW SavedVariables for ForgasGuildCalendar (FGC_DB)."""

from __future__ import annotations

import re
from pathlib import Path

from slpp import slpp as lua

from fgc_sync.models.enums import Attendance
from fgc_sync.models.events import CalendarEvent, Participant

_FGC_DB_PATTERN = re.compile(r"^FGC_DB\s*=\s*", re.MULTILINE)


def parse_saved_variables(file_path: Path) -> dict:
    """Parse FGC_DB from a SavedVariables Lua file."""
    text = file_path.read_text(encoding="utf-8")
    match = _FGC_DB_PATTERN.search(text)
    if not match:
        raise ValueError("Could not find FGC_DB in SavedVariables file")
    return lua.decode(text[match.end():])


def extract_events(
    db: dict, guild_key: str, profile: str = "Default"
) -> list[CalendarEvent]:
    """Extract calendar events for a guild from parsed FGC_DB."""
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
    guild_scoped = (
        db.get("profiles", {}).get(profile, {}).get("guildScoped", {})
    )
    return list(guild_scoped.keys())


def list_character_names(db: dict) -> list[str]:
    """Return character names from profileKeys (without realm suffix)."""
    names = []
    for full_name in db.get("profileKeys", {}):
        name = full_name.split(" - ")[0].strip()
        if name and name not in names:
            names.append(name)
    return names


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
            )
        )
    return participants


def _parse_time(evt: dict) -> tuple[int, int]:
    """Extract hour and minute, preferring serverTimeMinutes."""
    time_minutes = evt.get("serverTimeMinutes")
    if time_minutes is not None:
        return int(time_minutes) // 60, int(time_minutes) % 60
    return evt.get("serverHour", 0), evt.get("serverMinute", 0)
