"""Tests for fgc_sync.services.weekly_overview."""

from datetime import date

from fgc_sync.models.enums import Attendance, EventType
from fgc_sync.models.events import CalendarEvent, Participant
from fgc_sync.services.weekly_overview import (
    collect_week_events,
    compute_weekly_hash,
    current_week_bounds,
    format_weekly_summary,
    render_weekly_overview,
)


def _evt(
    event_id: str,
    day: str,
    hour: int,
    *,
    confirmed: int = 0,
    signed: int = 0,
    raid: str = "gruul",
    creator: str = "Forga",
) -> CalendarEvent:
    parts = [
        Participant("C" + str(i), Attendance.CONFIRMED, "WARRIOR", "tank")
        for i in range(confirmed)
    ] + [
        Participant("S" + str(i), Attendance.SIGNED, "MAGE", "dps")
        for i in range(signed)
    ]
    return CalendarEvent(
        event_id=event_id,
        title="Gruul mit Forga",
        event_type=EventType.RAID,
        raid=raid,
        date=day,
        server_hour=hour,
        server_minute=0,
        comment="",
        creator=creator,
        revision=1,
        participants=parts,
    )


class TestFormatWeeklySummary:
    def test_contains_week_number_and_dates(self):
        monday = date(2026, 4, 13)
        text = format_weekly_summary(monday, 3)
        assert "KW 16" in text
        assert "2026" in text
        assert "13.04.2026" in text
        assert "19.04.2026" in text
        assert "3 Raid(s)" in text


class TestCurrentWeekBounds:
    def test_monday_is_returned(self):
        wednesday = date(2026, 4, 15)
        monday, sunday, week_key = current_week_bounds(wednesday)
        assert monday == date(2026, 4, 13)
        assert sunday == date(2026, 4, 19)
        assert week_key == "2026-W16"


class TestCollectWeekEvents:
    def test_filters_to_current_week(self):
        events = {
            "a": _evt("a", "2026-04-13", 20),  # Mon, in week
            "b": _evt("b", "2026-04-20", 20),  # next Mon, out
            "c": _evt("c", "2026-04-12", 20),  # Sun before, out
            "d": _evt("d", "2026-04-19", 22),  # Sun, in week
        }
        got = collect_week_events(events, today=date(2026, 4, 15))
        assert [e.event_id for e in got] == ["a", "d"]

    def test_empty_when_no_events_in_week(self):
        events = {"x": _evt("x", "2020-01-01", 20)}
        assert collect_week_events(events, today=date(2026, 4, 15)) == []


class TestComputeWeeklyHash:
    def test_stable_for_same_content(self):
        events = [_evt("a", "2026-04-13", 20, confirmed=5, signed=2)]
        assert compute_weekly_hash(events) == compute_weekly_hash(list(events))

    def test_changes_on_count_change(self):
        a = [_evt("a", "2026-04-13", 20, confirmed=5, signed=2)]
        b = [_evt("a", "2026-04-13", 20, confirmed=6, signed=2)]
        assert compute_weekly_hash(a) != compute_weekly_hash(b)

    def test_changes_on_leader_change(self):
        a = [_evt("a", "2026-04-13", 20, creator="Alice")]
        b = [_evt("a", "2026-04-13", 20, creator="Bob")]
        assert compute_weekly_hash(a) != compute_weekly_hash(b)

    def test_order_independent(self):
        a = [
            _evt("a", "2026-04-13", 20),
            _evt("b", "2026-04-15", 19),
        ]
        b = list(reversed(a))
        assert compute_weekly_hash(a) == compute_weekly_hash(b)


class TestRenderWeeklyOverview:
    def test_empty_week_renders_png(self):
        monday = date(2026, 4, 13)
        result = render_weekly_overview([], monday)
        assert isinstance(result, bytes)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_parallel_raids_renders_png(self):
        """Two events on the same day at the same time must both render."""
        monday = date(2026, 4, 13)
        events = [
            _evt("a", "2026-04-15", 20, confirmed=25, raid="gruul"),
            _evt("b", "2026-04-15", 20, confirmed=10, raid="karazhan"),
            _evt("c", "2026-04-15", 20, confirmed=8, raid="zulaman"),
        ]
        result = render_weekly_overview(events, monday)
        assert isinstance(result, bytes)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_full_week_renders_png(self):
        monday = date(2026, 4, 13)
        events = [
            _evt("a", "2026-04-13", 20, confirmed=10, signed=3, raid="gruul"),
            _evt(
                "b",
                "2026-04-15",
                19,
                confirmed=25,
                signed=5,
                raid="karazhan",
                creator="Alicia",
            ),
            _evt("c", "2026-04-17", 21, confirmed=0, signed=15, raid="magtheridon"),
        ]
        result = render_weekly_overview(events, monday)
        assert isinstance(result, bytes)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(result) > 2000
