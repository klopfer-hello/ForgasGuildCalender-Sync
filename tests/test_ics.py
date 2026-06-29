"""Tests for the iCalendar (.ics) rendering service."""

from fgc_sync.models.enums import EventType
from fgc_sync.models.events import CalendarEvent
from fgc_sync.services.ics import (
    compute_ics_hash,
    ics_filename,
    render_ics,
)


def _event(**over):
    base = dict(
        event_id="fgc-1",
        title="Gruul mit Forga",
        event_type=EventType.RAID,
        raid="gruul",
        date="2026-06-30",
        server_hour=20,
        server_minute=0,
        comment="be on time",
        creator="Forga",
        revision=1,
    )
    base.update(over)
    return CalendarEvent(**base)


def test_render_is_valid_vcalendar_with_utc_times():
    out = render_ics(_event(), "Europe/Berlin", 3).decode()
    assert out.startswith("BEGIN:VCALENDAR")
    assert out.strip().endswith("END:VCALENDAR")
    assert out.count("BEGIN:VEVENT") == 1
    # 20:00 Berlin (CEST, UTC+2) -> 18:00 UTC, +3h -> 21:00 UTC
    assert "DTSTART:20260630T180000Z" in out
    assert "DTEND:20260630T210000Z" in out
    assert "UID:fgc-1@fgc-sync" in out
    assert "SUMMARY:[Raid] Gruul mit Forga" in out
    assert "LOCATION:Gruul" in out


def test_fractional_duration():
    out = render_ics(_event(), "Europe/Berlin", 2.5).decode()
    assert "DTSTART:20260630T180000Z" in out
    assert "DTEND:20260630T203000Z" in out


def test_text_fields_are_escaped():
    out = render_ics(
        _event(comment="raid, bring; food\nand water"), "Europe/Berlin", 3
    ).decode()
    assert "DESCRIPTION:raid\\, bring\\; food\\nand water" in out


def test_lines_use_crlf():
    raw = render_ics(_event(), "Europe/Berlin", 3)
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_render_is_deterministic():
    a = render_ics(_event(), "Europe/Berlin", 3)
    b = render_ics(_event(), "Europe/Berlin", 3)
    assert a == b


def test_hash_changes_on_time_title_duration_and_comment():
    base = compute_ics_hash(_event(), "Europe/Berlin", 3)
    assert compute_ics_hash(_event(server_hour=21), "Europe/Berlin", 3) != base
    assert compute_ics_hash(_event(title="Other"), "Europe/Berlin", 3) != base
    assert compute_ics_hash(_event(), "Europe/Berlin", 2) != base
    assert compute_ics_hash(_event(comment="changed"), "Europe/Berlin", 3) != base
    # Same inputs -> stable hash
    assert compute_ics_hash(_event(), "Europe/Berlin", 3) == base


def test_filename_embeds_hash_and_is_parseable():
    h = compute_ics_hash(_event(), "Europe/Berlin", 3)
    fn = ics_filename(_event(), h)
    assert fn == f"Gruul_2026-06-30_h{h}.ics"
    assert fn.endswith(".ics")
