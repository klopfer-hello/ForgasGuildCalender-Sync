"""Tests for effective event duration (services.event_duration).

Covers the addon's normalization rules, the three-step resolution chain
(stored value -> addon per-raid default -> configured fallback), and the
downstream surfaces that must stop assuming a flat duration.
"""

import math

import pytest

from fgc_sync.models.enums import EventType
from fgc_sync.models.events import CalendarEvent
from fgc_sync.services.discord_poster import compute_event_hash
from fgc_sync.services.event_duration import (
    MAX_DURATION_MINUTES,
    addon_default_duration_minutes,
    apply_effective_durations,
    fallback_minutes_from_config,
    normalize_duration_minutes,
    resolve_duration_minutes,
)
from fgc_sync.services.ics import compute_ics_hash, render_ics
from fgc_sync.services.weekly_overview import compute_weekly_hash


def _event(**overrides) -> CalendarEvent:
    defaults = dict(
        event_id="fgc-1",
        title="Raid",
        event_type=EventType.RAID,
        raid="karazhan",
        date="2026-09-14",
        server_hour=20,
        server_minute=0,
        comment="",
        creator="Forga",
        revision=1,
        participants=[],
    )
    defaults.update(overrides)
    return CalendarEvent(**defaults)


class TestNormalizeDurationMinutes:
    """Mirrors the addon's FGC:NormalizeEventDurationMinutes."""

    @pytest.mark.parametrize("value", [0, 1, 90, 120, 405])
    def test_accepts_whole_minutes_in_range(self, value):
        assert normalize_duration_minutes(value) == value

    def test_zero_is_preserved_not_dropped(self):
        # 0 means "explicitly undefined", which is different from a missing
        # key: both fall through, but only one is a value the addon stored.
        assert normalize_duration_minutes(0) == 0
        assert normalize_duration_minutes(None) is None

    def test_upper_bound_is_inclusive(self):
        assert normalize_duration_minutes(MAX_DURATION_MINUTES) == MAX_DURATION_MINUTES
        assert normalize_duration_minutes(MAX_DURATION_MINUTES + 1) is None

    @pytest.mark.parametrize(
        "value", [-1, 406, 12.5, float("nan"), math.inf, -math.inf]
    )
    def test_rejects_out_of_contract_numbers(self, value):
        assert normalize_duration_minutes(value) is None

    def test_accepts_numeric_strings_like_tonumber(self):
        assert normalize_duration_minutes("90") == 90
        assert normalize_duration_minutes("90.0") == 90

    @pytest.mark.parametrize("value", [None, True, False, "abc", "", [], {}, object()])
    def test_rejects_non_numeric(self, value):
        assert normalize_duration_minutes(value) is None

    def test_float_that_is_whole_is_accepted(self):
        assert normalize_duration_minutes(180.0) == 180


class TestAddonDefaults:
    @pytest.mark.parametrize(
        ("raid", "expected"),
        [
            ("karazhan", 120),
            ("gruul", 30),
            ("magtheridon", 30),
            ("gruul_mag", 60),
            ("ssc_tk", 210),
            ("bt", 180),
            ("hyjal_bt", 240),
        ],
    )
    def test_known_raids_use_the_addon_table(self, raid, expected):
        assert addon_default_duration_minutes(EventType.RAID, raid) == expected

    @pytest.mark.parametrize(
        ("legacy", "canonical"),
        [
            ("tempest_keep", "tk"),
            ("black_temple", "bt"),
            ("serpentshrine", "ssc"),
            ("sunwell", "swp"),
            ("zulaman", "za"),
        ],
    )
    def test_legacy_long_form_raid_keys_resolve(self, legacy, canonical):
        assert addon_default_duration_minutes(
            EventType.RAID, legacy
        ) == addon_default_duration_minutes(EventType.RAID, canonical)

    @pytest.mark.parametrize("raid", ["aq40", "naxx", "zg", "", "totally-unknown"])
    def test_raids_without_an_addon_default_return_none(self, raid):
        assert addon_default_duration_minutes(EventType.RAID, raid) is None

    @pytest.mark.parametrize("event_type", ["meeting", "dungeon", "pvp", ""])
    def test_non_raid_events_have_no_addon_default(self, event_type):
        # The addon answers 180 for these; we return None so the *configured*
        # duration wins instead of a hard-coded 3h.
        assert addon_default_duration_minutes(event_type, "karazhan") is None


class TestResolveDurationMinutes:
    def test_stored_value_wins_over_addon_default(self):
        evt = _event(raid="karazhan", duration_minutes=150)
        assert resolve_duration_minutes(evt, 180) == 150

    def test_zero_falls_through_to_addon_default(self):
        evt = _event(raid="karazhan", duration_minutes=0)
        assert resolve_duration_minutes(evt, 999) == 120

    def test_missing_falls_through_to_addon_default(self):
        evt = _event(raid="bt", duration_minutes=None)
        assert resolve_duration_minutes(evt, 999) == 180

    def test_invalid_stored_value_falls_through(self):
        evt = _event(raid="gruul", duration_minutes=5000)
        assert resolve_duration_minutes(evt, 999) == 30

    def test_unknown_raid_falls_through_to_config(self):
        evt = _event(raid="naxx", duration_minutes=0)
        assert resolve_duration_minutes(evt, 240) == 240

    def test_non_raid_event_falls_through_to_config(self):
        evt = _event(event_type="meeting", raid="", duration_minutes=None)
        assert resolve_duration_minutes(evt, 45) == 45

    def test_explicit_value_wins_even_for_unknown_raid(self):
        evt = _event(raid="naxx", duration_minutes=300)
        assert resolve_duration_minutes(evt, 180) == 300


class TestFallbackMinutesFromConfig:
    @pytest.mark.parametrize(
        ("hours", "expected"), [(3, 180), (2.5, 150), (1, 60), (0.5, 30)]
    )
    def test_converts_hours_to_minutes(self, hours, expected):
        assert fallback_minutes_from_config(hours) == expected

    @pytest.mark.parametrize("bad", [None, "abc", object()])
    def test_bad_config_falls_back_to_180(self, bad):
        assert fallback_minutes_from_config(bad) == 180

    def test_never_returns_zero(self):
        assert fallback_minutes_from_config(0) >= 1


class TestApplyEffectiveDurations:
    def test_stamps_every_event_with_a_concrete_int(self):
        events = [
            _event(event_id="a", raid="karazhan", duration_minutes=150),
            _event(event_id="b", raid="karazhan", duration_minutes=0),
            _event(event_id="c", raid="naxx", duration_minutes=None),
            _event(event_id="d", event_type="meeting", raid=""),
        ]
        apply_effective_durations(events, 180)
        assert [e.duration_minutes for e in events] == [150, 120, 180, 180]
        assert all(isinstance(e.duration_minutes, int) for e in events)

    def test_is_idempotent(self):
        # A second pass sees the already-resolved value as an explicit one and
        # must not shift it — cycles re-parse and re-stamp every time.
        events = [_event(raid="karazhan", duration_minutes=0)]
        apply_effective_durations(events, 180)
        apply_effective_durations(events, 180)
        assert events[0].duration_minutes == 120

    def test_empty_list_is_safe(self):
        apply_effective_durations([], 180)


class TestEventTimeHelpers:
    def test_end_time_and_range(self):
        evt = _event(server_hour=20, server_minute=0, duration_minutes=150)
        assert evt.end_time_str == "22:30"
        assert evt.time_range_str == "20:00–22:30"

    def test_wraps_past_midnight(self):
        evt = _event(server_hour=23, server_minute=15, duration_minutes=180)
        assert evt.end_time_str == "02:15"

    def test_unresolved_duration_shows_start_only(self):
        evt = _event(duration_minutes=None)
        assert evt.end_time_str == ""
        assert evt.time_range_str == "20:00"


class TestDurationEntersContentHashes:
    """A duration edit must invalidate every cached remote artefact."""

    def test_event_hash_changes(self):
        a = _event(duration_minutes=120)
        b = _event(duration_minutes=180)
        assert compute_event_hash(a) != compute_event_hash(b)

    def test_weekly_hash_changes(self):
        a = [_event(duration_minutes=120)]
        b = [_event(duration_minutes=180)]
        assert compute_weekly_hash(a) != compute_weekly_hash(b)

    def test_ics_hash_changes(self):
        evt = _event()
        assert compute_ics_hash(evt, "Europe/Berlin", 2.0) != compute_ics_hash(
            evt, "Europe/Berlin", 3.0
        )

    def test_ics_dtend_reflects_the_duration(self):
        evt = _event(server_hour=20, server_minute=0, duration_minutes=150)
        body = render_ics(evt, "Europe/Berlin", evt.duration_minutes / 60).decode()
        assert "DTSTART:20260914T180000Z" in body  # 20:00 CEST -> 18:00 UTC
        assert "DTEND:20260914T203000Z" in body  # +2h30


class TestEffectiveDurationHours:
    def test_converts_resolved_minutes(self):
        from fgc_sync.services.event_duration import effective_duration_hours

        assert effective_duration_hours(_event(duration_minutes=150)) == 2.5
        assert effective_duration_hours(_event(duration_minutes=30)) == 0.5

    def test_unresolved_event_never_yields_a_zero_length_entry(self):
        from fgc_sync.services.event_duration import effective_duration_hours

        assert effective_duration_hours(_event(duration_minutes=None)) > 0
        assert effective_duration_hours(_event(duration_minutes=0)) > 0
