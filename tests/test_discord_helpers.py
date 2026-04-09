"""Tests for pure functions in fgc_sync.services.discord_poster."""

from fgc_sync.models.enums import Attendance, EventType
from fgc_sync.models.events import CalendarEvent, Participant
from fgc_sync.services.discord_poster import RAID_SHORT_NAMES, compute_event_hash


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
    defaults = dict(class_code="warrior", role_code="tank")
    defaults.update(kw)
    return Participant(name=name, attendance=attendance, **defaults)


# --- compute_event_hash ---


class TestComputeEventHash:
    def test_returns_8_char_hex(self):
        evt = _make_event()
        h = compute_event_hash(evt)
        assert len(h) == 8
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        evt = _make_event()
        assert compute_event_hash(evt) == compute_event_hash(evt)

    def test_different_revision_different_hash(self):
        e1 = _make_event(revision=1)
        e2 = _make_event(revision=2)
        assert compute_event_hash(e1) != compute_event_hash(e2)

    def test_different_participants_different_hash(self):
        e1 = _make_event(
            participants=[_make_participant("Alice", Attendance.CONFIRMED)]
        )
        e2 = _make_event(participants=[_make_participant("Bob", Attendance.CONFIRMED)])
        assert compute_event_hash(e1) != compute_event_hash(e2)

    def test_same_participants_same_hash(self):
        participants = [
            _make_participant("Alice", Attendance.CONFIRMED),
            _make_participant("Bob", Attendance.SIGNED),
        ]
        e1 = _make_event(participants=participants)
        e2 = _make_event(participants=participants)
        assert compute_event_hash(e1) == compute_event_hash(e2)

    def test_participant_order_irrelevant(self):
        """Hash should be the same regardless of participant insertion order."""
        p1 = [
            _make_participant("Alice", Attendance.CONFIRMED),
            _make_participant("Bob", Attendance.CONFIRMED),
        ]
        p2 = [
            _make_participant("Bob", Attendance.CONFIRMED),
            _make_participant("Alice", Attendance.CONFIRMED),
        ]
        e1 = _make_event(participants=p1)
        e2 = _make_event(participants=p2)
        assert compute_event_hash(e1) == compute_event_hash(e2)

    def test_attendance_change_changes_hash(self):
        e1 = _make_event(participants=[_make_participant("Alice", Attendance.SIGNED)])
        e2 = _make_event(
            participants=[_make_participant("Alice", Attendance.CONFIRMED)]
        )
        assert compute_event_hash(e1) != compute_event_hash(e2)

    def test_group_change_changes_hash(self):
        e1 = _make_event(
            participants=[
                _make_participant("Alice", Attendance.CONFIRMED, group=1, slot=1)
            ]
        )
        e2 = _make_event(
            participants=[
                _make_participant("Alice", Attendance.CONFIRMED, group=2, slot=1)
            ]
        )
        assert compute_event_hash(e1) != compute_event_hash(e2)

    def test_no_participants(self):
        evt = _make_event(participants=[])
        h = compute_event_hash(evt)
        assert len(h) == 8  # still produces a valid hash


# --- RAID_SHORT_NAMES ---


class TestRaidShortNames:
    def test_known_raids_present(self):
        assert "karazhan" in RAID_SHORT_NAMES
        assert "gruul" in RAID_SHORT_NAMES
        assert "zulaman" in RAID_SHORT_NAMES

    def test_values_are_short(self):
        for key, short in RAID_SHORT_NAMES.items():
            assert len(short) <= 5, f"{key} -> {short} is too long"
