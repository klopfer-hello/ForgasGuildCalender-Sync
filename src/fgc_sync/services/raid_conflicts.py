"""Derive per-participant "unavailable" state from raid-lockout conflicts.

Mirrors the addon's ``GetPlayerConfirmedRaidIdConflictsForEvent`` (the rule
behind the greyed-out entries in its "Available Players" list): a player who
is *Signed* on event A is unavailable when they are *Confirmed* on another,
non-cancelled raid event B with a different raid lead whose raid lockout
overlaps A's.

Lockout model (``RAID_LOCKOUT_RULES`` in the addon's ``UI-Editor.lua``):
every raid resets weekly except ZA/ZG/AQ20 (3-day cycle) and Onyxia (5-day
cycle). Weekly lockouts are anchored on the EU reset day (Wednesday). Cycle
lockouts are anchored on a fixed epoch date — the addon anchors them from
live server reset timers which aren't stored in SavedVariables, so cycle
boundaries are an approximation; what matters for cross-client hash
convergence is that the result is deterministic for identical inputs.

Double raids decompose into their component lockouts (``componentRaidKeys``
in the addon), so an ``ssc_tk`` event conflicts with plain ``ssc`` and ``tk``
events in the same reset week.
"""

from __future__ import annotations

from datetime import date, timedelta

from fgc_sync.models.enums import Attendance
from fgc_sync.models.events import CalendarEvent

# The addon marks a cancelled event with this prefix on the event comment
# (EVENT_CANCELLED_COMMENT_PREFIX in Core-UiRuntime.lua).
EVENT_CANCELLED_COMMENT_PREFIX = "!}"

# Double raids share the lockouts of their component raids
# (componentRaidKeys in the addon's UI-Editor.lua EVENT_OPTIONS).
COMPONENT_RAID_KEYS: dict[str, tuple[str, ...]] = {
    "ssc_tk": ("ssc", "tk"),
    "gruul_mag": ("gruul", "magtheridon"),
}

# Legacy long-form raid values older events used, mapped to the addon's
# canonical raid keys so both spellings land on the same lockout.
_CANONICAL_RAID_KEYS: dict[str, str] = {
    "serpentshrine": "ssc",
    "tempest_keep": "tk",
    "black_temple": "bt",
    "sunwell": "swp",
    "zulaman": "za",
}

# Non-weekly reset cycles (days). Everything else resets weekly.
CYCLE_DAYS: dict[str, int] = {"za": 3, "zg": 3, "aq20": 3, "ony": 5}

# EU weekly raid reset is Wednesday (date.weekday() == 2).
WEEKLY_RESET_WEEKDAY = 2

# Fixed anchor for the short reset cycles (see module docstring).
_CYCLE_ANCHOR = date(2026, 1, 7)


def _component_raid_keys(raid: str) -> tuple[str, ...]:
    raid_key = _CANONICAL_RAID_KEYS.get(raid, raid)
    return COMPONENT_RAID_KEYS.get(raid_key, (raid_key,))


def _lockout_key(raid_key: str, event_date: date) -> str:
    cycle = CYCLE_DAYS.get(raid_key)
    if cycle:
        index = (event_date - _CYCLE_ANCHOR).days // cycle
        return f"{raid_key}|c{cycle}|{index}"
    week_start = event_date - timedelta(
        days=(event_date.weekday() - WEEKLY_RESET_WEEKDAY) % 7
    )
    return f"{raid_key}|w|{week_start.isoformat()}"


def is_event_cancelled(event: CalendarEvent) -> bool:
    """Return True if the addon marked *event* as cancelled."""
    return (event.comment or "").startswith(EVENT_CANCELLED_COMMENT_PREFIX)


def _event_lockout_keys(event: CalendarEvent) -> list[str]:
    if event.event_type != "raid" or not event.raid:
        return []
    try:
        event_date = date.fromisoformat(event.date)
    except ValueError:
        return []
    return [_lockout_key(rk, event_date) for rk in _component_raid_keys(event.raid)]


def mark_unavailable_participants(events: list[CalendarEvent]) -> None:
    """Set ``Participant.unavailable`` on signed players picked by another raid.

    Mutates *events* in place. A participant signed on event A is unavailable
    when the same character is confirmed on another non-cancelled raid event
    with a different creator that shares at least one lockout key with A.
    """
    # lockout key -> confirmed character (casefolded) -> creators of the
    # events that confirmed them.
    confirmed_index: dict[str, dict[str, set[str]]] = {}
    for event in events:
        if is_event_cancelled(event):
            continue
        lockout_keys = _event_lockout_keys(event)
        if not lockout_keys or not event.creator:
            continue
        for participant in event.participants:
            if participant.attendance != Attendance.CONFIRMED:
                continue
            name_key = participant.name.casefold()
            for lockout in lockout_keys:
                confirmed_index.setdefault(lockout, {}).setdefault(name_key, set()).add(
                    event.creator
                )

    for event in events:
        if is_event_cancelled(event):
            continue
        lockout_keys = _event_lockout_keys(event)
        if not lockout_keys or not event.creator:
            continue
        for participant in event.participants:
            if participant.attendance != Attendance.SIGNED:
                continue
            name_key = participant.name.casefold()
            participant.unavailable = any(
                creator != event.creator
                for lockout in lockout_keys
                for creator in confirmed_index.get(lockout, {}).get(name_key, ())
            )
