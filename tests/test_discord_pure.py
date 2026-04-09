"""Tests for pure functions in discord_poster (not requiring HTTP)."""

from fgc_sync.models.enums import EventType
from fgc_sync.models.events import CalendarEvent
from fgc_sync.services.discord_poster import (
    DiscordPoster,
    _short_raid_name,
    _slugify,
)


def _make_event(**overrides) -> CalendarEvent:
    defaults = dict(
        event_id="test-1",
        title="Gruul mit Forga",
        event_type=EventType.RAID,
        raid="gruul",
        date="2026-04-10",
        server_hour=19,
        server_minute=45,
        comment="",
        creator="Forga",
        revision=1,
        participants=[],
    )
    defaults.update(overrides)
    return CalendarEvent(**defaults)


# --- _short_raid_name ---


class TestShortRaidName:
    def test_exact_match(self):
        assert _short_raid_name("karazhan") == "Kara"
        assert _short_raid_name("gruul") == "Gruul"
        assert _short_raid_name("zulaman") == "ZA"

    def test_case_insensitive(self):
        assert _short_raid_name("Karazhan") == "Kara"
        assert _short_raid_name("GRUUL") == "Gruul"

    def test_space_to_underscore(self):
        assert _short_raid_name("Serpentshrine") == "SSC"
        assert _short_raid_name("Tempest Keep") == "TK"

    def test_partial_match(self):
        assert _short_raid_name("karazhan_heroic") == "Kara"

    def test_unknown_raid_fallback(self):
        result = _short_raid_name("unknown_dungeon")
        assert result == "Unknown Dungeon"

    def test_empty_string_returns_event(self):
        assert _short_raid_name("") == "Event"

    def test_truncates_long_name(self):
        result = _short_raid_name("a_very_long_raid_name_that_exceeds_limit")
        assert len(result) <= 15


# --- _slugify ---


class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars_removed(self):
        assert _slugify("Raids & Events!") == "raids-events"

    def test_unicode_stripped(self):
        result = _slugify("Über Café")
        assert "u" in result  # ü → u via NFKD
        assert "cafe" in result

    def test_max_length(self):
        result = _slugify("a" * 200, max_len=10)
        assert len(result) <= 10

    def test_empty_input(self):
        result = _slugify("")
        assert result == ""

    def test_no_leading_trailing_hyphens(self):
        result = _slugify("---hello---")
        assert not result.startswith("-")
        assert not result.endswith("-")


# --- DiscordPoster._thread_name ---


class TestThreadName:
    def test_format(self):
        # 2026-04-10 is a Friday
        evt = _make_event(
            date="2026-04-10",
            server_hour=20,
            server_minute=0,
            raid="karazhan",
            creator="Forga",
        )
        name = DiscordPoster._thread_name(evt)
        assert name == "Fr 10.04. 20:00 \u2014 Kara mit Forga"

    def test_saturday(self):
        # 2026-04-11 is a Saturday
        evt = _make_event(
            date="2026-04-11",
            server_hour=19,
            server_minute=45,
            raid="gruul",
            creator="Dastanky",
        )
        name = DiscordPoster._thread_name(evt)
        assert name.startswith("Sa ")
        assert "Gruul" in name
        assert "Dastanky" in name

    def test_no_raid_uses_title(self):
        evt = _make_event(
            date="2026-04-10", raid="", title="Custom Event", creator="Test"
        )
        name = DiscordPoster._thread_name(evt)
        assert "Custom Event" in name

    def test_no_creator_uses_unknown(self):
        evt = _make_event(date="2026-04-10", creator="", raid="gruul")
        name = DiscordPoster._thread_name(evt)
        assert "Unknown" in name

    def test_leading_zeros(self):
        # 2026-01-05 is a Monday
        evt = _make_event(
            date="2026-01-05", server_hour=9, server_minute=5, raid="gruul", creator="X"
        )
        name = DiscordPoster._thread_name(evt)
        assert "05.01." in name
        assert "09:05" in name
