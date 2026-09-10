"""Tests for fgc_sync.services.weekly_overview."""

from datetime import date

import pytest

from fgc_sync import i18n
from fgc_sync.models.enums import Attendance, EventType
from fgc_sync.models.events import CalendarEvent, Participant
from fgc_sync.services.weekly_overview import (
    candidate_weekly_thread_names,
    collect_week_events,
    compute_weekly_hash,
    current_week_bounds,
    format_weekly_summary,
    get_weekly_thread_name,
    render_weekly_overview,
)


@pytest.fixture
def lang_de():
    previous = i18n.get_language()
    i18n.set_language("de-DE")
    yield
    i18n.set_language(previous)


@pytest.fixture
def lang_en():
    previous = i18n.get_language()
    i18n.set_language("en-UK")
    yield
    i18n.set_language(previous)


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
    def test_contains_week_number_and_dates_de(self, lang_de):
        monday = date(2026, 4, 13)
        text = format_weekly_summary(monday, 3)
        assert "KW 16" in text
        assert "2026" in text
        assert "13.04.2026" in text
        assert "19.04.2026" in text
        assert "3 Raid(s)" in text
        assert "geplant" in text

    def test_contains_week_number_and_dates_en(self, lang_en):
        monday = date(2026, 4, 13)
        text = format_weekly_summary(monday, 3)
        assert "CW 16" in text
        assert "2026" in text
        assert "13.04.2026" in text
        assert "19.04.2026" in text
        assert "3 raid(s)" in text
        assert "scheduled" in text


class TestWeeklyThreadName:
    def test_active_language_de(self, lang_de):
        assert get_weekly_thread_name() == "Wöchentliche Raid Übersicht"

    def test_active_language_en(self, lang_en):
        assert get_weekly_thread_name() == "Weekly Raid Overview"

    def test_candidates_cover_all_languages(self):
        names = candidate_weekly_thread_names()
        assert "Wöchentliche Raid Übersicht" in names
        assert "Weekly Raid Overview" in names


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


class TestCardGeometry:
    """Cards reserve room for their label so text never escapes the box.

    Once durations stopped being uniform, a duration-exact box was too short
    for the six-line label on every raid under ~2.5h — which is most of them
    (gruul 30, gruul_mag 60, karazhan/ssc/tk/hyjal 120). The card grows to the
    label's height; the raid's real end is marked by the colour boundary.
    """

    def test_short_raid_card_is_grown_to_the_label_height(self):
        from fgc_sync.services.weekly_overview import (
            _MIN_CARD_MINUTES,
            _card_minutes,
        )

        evt = _evt("a", "2026-09-07", 20)
        evt.duration_minutes = 30
        assert _card_minutes(evt) == _MIN_CARD_MINUTES

    def test_long_raid_card_still_matches_its_duration(self):
        from fgc_sync.services.weekly_overview import _card_minutes

        evt = _evt("a", "2026-09-07", 20)
        evt.duration_minutes = 240
        assert _card_minutes(evt) == 240

    def test_hour_range_extends_to_fit_the_card_not_just_the_raid(self):
        from fgc_sync.services.weekly_overview import (
            _MIN_CARD_MINUTES,
            _determine_hour_range,
        )

        evt = _evt("a", "2026-09-07", 20)
        evt.duration_minutes = 30
        _start, end_hour = _determine_hour_range([evt])
        # The card runs to 20:00 + _MIN_CARD_MINUTES; the grid must reach past
        # it, otherwise the label is clipped at the bottom of the image.
        assert end_hour * 60 >= 20 * 60 + _MIN_CARD_MINUTES

    def test_blend_moves_toward_the_target_colour(self):
        from fgc_sync.services.weekly_overview import _blend

        assert _blend((0, 0, 0), (100, 100, 100), 0.0) == (0, 0, 0)
        assert _blend((0, 0, 0), (100, 100, 100), 1.0) == (100, 100, 100)
        assert _blend((0, 0, 0), (100, 200, 40), 0.5) == (50, 100, 20)

    @pytest.mark.parametrize("duration", [30, 60, 90, 120, 150, 180, 240])
    def test_renders_at_every_raid_length(self, duration):
        evt = _evt("a", "2026-09-07", 20, confirmed=25, signed=6)
        evt.duration_minutes = duration
        png = render_weekly_overview([evt], date(2026, 9, 7))
        assert png.startswith(b"\x89PNG")

    def test_sequential_short_raids_do_not_share_a_lane(self):
        # Back-to-back 30-min raids: the first card reserves space past the
        # second's start, so they must be laid out side by side rather than
        # drawn on top of each other.
        first = _evt("a", "2026-09-07", 20, confirmed=25)
        first.duration_minutes = 30
        second = _evt("b", "2026-09-07", 21, confirmed=20)
        second.duration_minutes = 30
        png = render_weekly_overview([first, second], date(2026, 9, 7))
        assert png.startswith(b"\x89PNG")

    def test_duration_still_drives_the_hash_independently_of_the_card(self):
        # Two raids whose cards are both floored to _MIN_CARD_MINUTES must
        # still hash differently — the colour boundary differs even though the
        # card geometry does not.
        a = _evt("a", "2026-09-07", 20)
        a.duration_minutes = 30
        b = _evt("a", "2026-09-07", 20)
        b.duration_minutes = 60
        assert compute_weekly_hash([a]) != compute_weekly_hash([b])
