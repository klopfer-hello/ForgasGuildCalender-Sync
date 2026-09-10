"""Effective event duration, mirroring the addon's own duration rules.

Since the ``V5`` storage bump the addon writes an optional ``durationMinutes``
on each event (``Core-EventRuntime.lua``). Two details of its semantics matter
and are easy to get wrong:

* **The value is normalized to an integer in ``[0, 405]``.** Anything else —
  missing, fractional, negative, NaN, out of range — is *no value* at all
  (``NormalizeEventDurationMinutes`` returns ``nil``).
* **A stored ``0`` does not mean "zero minutes".** The addon treats it as
  *explicitly undefined* ("Explicitly undefined duration is a valid
  suggestion") and falls back to a per-raid default table,
  ``GetDefaultEventDurationMinutes``, whose own last resort is 180 minutes.

Only 15 of 88 events carried the field when it first appeared, because the
addon writes it lazily — an untouched event simply has no key. Absent and
``0`` therefore mean the same thing: *fall through*.

We mirror that chain but replace the addon's final 180 with the user's
configured ``default_duration_hours``, so raids the addon has no default for
(``aq40``, ``naxx``, ``zg``, …) and non-raid events (meetings) follow the
tool's own setting:

1. a stored, non-zero ``durationMinutes`` — the raid lead's explicit choice
2. the addon's per-raid default, for ``type == "raid"`` with a known raid key
3. ``default_duration_hours`` from config

Steps 1 and 2 depend only on SavedVariables, so every client derives the same
number for them — which is what keeps the content hashes convergent across
clients. Only step 3 reads local config; see :func:`apply_effective_durations`.
"""

from __future__ import annotations

from fgc_sync.models.enums import EventType
from fgc_sync.models.events import CalendarEvent
from fgc_sync.services.raid_conflicts import canonical_raid_key

#: Upper bound the addon accepts for ``durationMinutes`` (6h45m). Values above
#: it are rejected outright rather than clamped, matching the addon.
MAX_DURATION_MINUTES = 405

#: The addon's own blanket fallback (the ``or 180`` in
#: ``GetDefaultEventDurationMinutes``). We normally prefer the user's
#: ``default_duration_hours`` over it, so this is only reached when that setting
#: is unreadable or an event somehow arrives unresolved.
ADDON_FALLBACK_DURATION_MINUTES = 180

#: Per-raid defaults from the addon's ``defaultsByRaid`` table
#: (``Core-EventRuntime.lua``). Keyed by the addon's canonical raid keys; the
#: double raids carry their own combined default rather than summing their
#: components. Raids absent here (``aq40``, ``naxx``, ``zg``, …) have no addon
#: default and fall through to the configured duration.
ADDON_DEFAULT_DURATION_MINUTES: dict[str, int] = {
    "karazhan": 120,
    "gruul": 30,
    "magtheridon": 30,
    "gruul_mag": 60,
    "ssc": 120,
    "tk": 120,
    "ssc_tk": 210,
    "hyjal": 120,
    "bt": 180,
    "hyjal_bt": 240,
    "za": 180,
    "swp": 180,
}


def normalize_duration_minutes(value: object) -> int | None:
    """Return *value* as the addon would store it, or ``None`` if it wouldn't.

    Mirrors ``FGC:NormalizeEventDurationMinutes``: numbers and numeric strings
    are accepted, must be whole, and must fall in ``[0, MAX_DURATION_MINUTES]``.
    ``0`` survives as ``0`` — it is a meaningful "explicitly undefined" marker,
    not a missing value (see module docstring).
    """
    # bool is an int subclass in Python but never a duration in Lua.
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        minutes = float(value)
    except (TypeError, ValueError):
        return None
    if minutes != minutes:  # NaN — also excludes it from the range checks
        return None
    if minutes < 0 or minutes > MAX_DURATION_MINUTES:
        return None
    if minutes != int(minutes):
        return None
    return int(minutes)


def addon_default_duration_minutes(event_type: str, raid: str) -> int | None:
    """The addon's own default for this event, or ``None`` if it has none.

    ``None`` covers both cases the addon answers with its blanket 180: a
    non-raid event (meeting, dungeon, pvp) and a raid key missing from
    :data:`ADDON_DEFAULT_DURATION_MINUTES`. Returning ``None`` instead lets the
    caller substitute the configured duration.
    """
    if str(event_type or "") != EventType.RAID:
        return None
    return ADDON_DEFAULT_DURATION_MINUTES.get(canonical_raid_key(raid or ""))


def resolve_duration_minutes(event: CalendarEvent, fallback_minutes: int) -> int:
    """Resolve *event* to a concrete duration via the three-step chain."""
    stored = normalize_duration_minutes(event.duration_minutes)
    if stored:  # non-zero: the raid lead set this explicitly
        return stored
    default = addon_default_duration_minutes(event.event_type, event.raid)
    if default is not None:
        return default
    return fallback_minutes


def fallback_minutes_from_config(default_duration_hours: float) -> int:
    """Convert the configured ``default_duration_hours`` to whole minutes."""
    try:
        return max(1, round(float(default_duration_hours) * 60))
    except (TypeError, ValueError):
        return ADDON_FALLBACK_DURATION_MINUTES


def effective_duration_hours(event: CalendarEvent) -> float:
    """Duration of *event* in fractional hours, for calendar end times.

    Reads the value :func:`apply_effective_durations` already resolved. The
    guard is belt-and-braces: the chain never yields 0, but a zero-length
    Google event or ``.ics`` would be visible breakage if it ever did, so an
    unresolved event falls back rather than collapsing to a point in time.
    """
    minutes = event.duration_minutes or ADDON_FALLBACK_DURATION_MINUTES
    return minutes / 60


def apply_effective_durations(
    events: list[CalendarEvent], fallback_minutes: int
) -> None:
    """Replace each event's raw ``duration_minutes`` with its effective value.

    Called by the sync-engine collectors right after parsing — the same place
    and pattern as :func:`~fgc_sync.services.raid_conflicts.
    mark_unavailable_participants` — so every consumer downstream (Google end
    times, ``.ics`` ``DTEND``, roster header, weekly grid) can read
    ``event.duration_minutes`` as a plain int without knowing about the
    fallback chain.

    Derived state: never written back to SavedVariables, recomputed from
    scratch every cycle. Two clients agree on the result for every event the
    addon has an opinion about; they can only diverge on events that reach
    step 3, i.e. when their ``default_duration_hours`` settings differ.
    """
    for event in events:
        event.duration_minutes = resolve_duration_minutes(event, fallback_minutes)
