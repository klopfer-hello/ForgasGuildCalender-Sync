"""V3 (named-keys event with externalized named-keys roster) reader for FGC_DB.

Each event is a dict with explicit field names — same as v1 — but ``group`` and
``slot`` no longer live inline on each participant. They're stored in a separate
``event.roster.byPlayer[name]`` table, also using named keys::

    event["roster"]["byPlayer"]["Grayfill"] = {
        "group": 1,
        "slot": 1,
        "revision": 5,
        "updatedBy": "Grayfill",
        "updatedAt": ...,
    }

This is the addon's third on-disk shape: encoding stayed named-keys (unlike v2
which switched to packed positional), but the roster table got hoisted out for
the same dedup/sync reasons FGC2 introduced. The companion ``V3|`` prefix on
``profiles[].guildScoped`` keys is the addon's external signal that this layout
is in effect; the façade falls back to the prefix only when the events table is
empty (shape detection wins otherwise).
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

            roster_raw = evt.get("roster", {})
            roster_by_player = (
                roster_raw.get("byPlayer", {}) if isinstance(roster_raw, dict) else {}
            )
            if not isinstance(roster_by_player, dict):
                roster_by_player = {}

            participants = _parse_participants(
                evt.get("participants", {}), roster_by_player
            )
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


def _parse_participants(raw: dict, roster_by_player: dict) -> list[Participant]:
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


def _parse_time(evt: dict) -> tuple[int, int]:
    """Extract hour and minute, preferring serverTimeMinutes."""
    time_minutes = evt.get("serverTimeMinutes")
    if time_minutes is not None:
        return int(time_minutes) // 60, int(time_minutes) % 60
    return evt.get("serverHour", 0), evt.get("serverMinute", 0)
