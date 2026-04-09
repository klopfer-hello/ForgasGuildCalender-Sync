"""Tests for fgc_sync.services.roster_image."""

from fgc_sync.models.enums import Attendance, EventType
from fgc_sync.models.events import CalendarEvent, Participant
from fgc_sync.services.roster_image import render_roster


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


def _make_participant(name: str, attendance: Attendance, **kw) -> Participant:
    defaults = dict(class_code="WARRIOR", role_code="tank")
    defaults.update(kw)
    return Participant(name=name, attendance=attendance, **defaults)


class TestRenderRoster:
    def test_returns_png_bytes(self):
        evt = _make_event()
        result = render_roster(evt, "Europe/Berlin")
        assert isinstance(result, bytes)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic bytes

    def test_with_participants(self):
        evt = _make_event(
            participants=[
                _make_participant("Alice", Attendance.CONFIRMED, group=1, slot=1),
                _make_participant("Bob", Attendance.SIGNED),
                _make_participant("Charlie", Attendance.BENCHED),
            ]
        )
        result = render_roster(evt, "Europe/Berlin")
        assert isinstance(result, bytes)
        assert len(result) > 1000  # non-trivial image

    def test_with_multiple_groups(self):
        participants = []
        for i in range(10):
            participants.append(
                _make_participant(
                    f"Player{i}",
                    Attendance.CONFIRMED,
                    group=(i % 4) + 1,
                    slot=i + 1,
                )
            )
        evt = _make_event(participants=participants)
        result = render_roster(evt, "Europe/Berlin")
        assert isinstance(result, bytes)

    def test_empty_event(self):
        evt = _make_event(participants=[])
        result = render_roster(evt, "Europe/Berlin")
        assert isinstance(result, bytes)

    def test_with_comment(self):
        evt = _make_event(comment="Bring flasks and food!")
        result = render_roster(evt, "Europe/Berlin")
        assert isinstance(result, bytes)

    def test_different_timezone(self):
        evt = _make_event()
        result = render_roster(evt, "US/Eastern")
        assert isinstance(result, bytes)

    def test_all_attendance_types(self):
        evt = _make_event(
            participants=[
                _make_participant("Confirmed1", Attendance.CONFIRMED, group=1, slot=1),
                _make_participant("Signed1", Attendance.SIGNED),
                _make_participant("Benched1", Attendance.BENCHED),
                _make_participant("Declined1", Attendance.DECLINED),
            ]
        )
        result = render_roster(evt, "Europe/Berlin")
        assert isinstance(result, bytes)

    def test_various_class_codes(self):
        classes = [
            "WARRIOR",
            "PALADIN",
            "HUNTER",
            "ROGUE",
            "PRIEST",
            "SHAMAN",
            "MAGE",
            "WARLOCK",
            "DRUID",
        ]
        participants = [
            _make_participant(
                f"Player{i}", Attendance.CONFIRMED, class_code=cls, group=1, slot=i + 1
            )
            for i, cls in enumerate(classes)
        ]
        evt = _make_event(participants=participants)
        result = render_roster(evt, "Europe/Berlin")
        assert isinstance(result, bytes)

    def test_various_role_codes(self):
        evt = _make_event(
            participants=[
                _make_participant(
                    "Tank", Attendance.CONFIRMED, role_code="tank", group=1, slot=1
                ),
                _make_participant(
                    "Healer", Attendance.CONFIRMED, role_code="healer", group=1, slot=2
                ),
                _make_participant(
                    "DD", Attendance.CONFIRMED, role_code="dd", group=1, slot=3
                ),
            ]
        )
        result = render_roster(evt, "Europe/Berlin")
        assert isinstance(result, bytes)
