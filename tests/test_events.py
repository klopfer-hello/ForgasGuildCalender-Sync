"""Tests for fgc_sync.models.events."""

from fgc_sync.models.enums import Attendance, EventType
from fgc_sync.models.events import CalendarEvent, Participant


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
    defaults = dict(class_code="warrior", role_code="tank", comment="")
    defaults.update(kw)
    return Participant(name=name, attendance=attendance, **defaults)


# --- CalendarEvent properties ---


class TestTypeLabelProperty:
    def test_known_type(self):
        evt = _make_event(event_type=EventType.RAID)
        assert evt.type_label == "Raid"

    def test_unknown_type_capitalizes(self):
        evt = _make_event(event_type="custom_event")
        assert evt.type_label == "Custom_event"

    def test_empty_type(self):
        evt = _make_event(event_type="")
        assert isinstance(evt.type_label, str)


class TestConfirmedCount:
    def test_no_participants(self):
        evt = _make_event()
        assert evt.confirmed_count == 0

    def test_mixed_attendance(self):
        evt = _make_event(
            participants=[
                _make_participant("A", Attendance.CONFIRMED),
                _make_participant("B", Attendance.SIGNED),
                _make_participant("C", Attendance.CONFIRMED),
                _make_participant("D", Attendance.DECLINED),
            ]
        )
        assert evt.confirmed_count == 2


class TestSignedCount:
    def test_no_participants(self):
        evt = _make_event()
        assert evt.signed_count == 0

    def test_mixed_attendance(self):
        evt = _make_event(
            participants=[
                _make_participant("A", Attendance.SIGNED),
                _make_participant("B", Attendance.CONFIRMED),
                _make_participant("C", Attendance.SIGNED),
            ]
        )
        assert evt.signed_count == 2


class TestTimeStr:
    def test_formats_with_leading_zeros(self):
        evt = _make_event(server_hour=9, server_minute=5)
        assert evt.time_str == "09:05"

    def test_formats_normal_time(self):
        evt = _make_event(server_hour=19, server_minute=45)
        assert evt.time_str == "19:45"

    def test_midnight(self):
        evt = _make_event(server_hour=0, server_minute=0)
        assert evt.time_str == "00:00"


# --- CalendarEvent methods ---


class TestSummaryLine:
    def test_without_character_name(self):
        evt = _make_event(event_type=EventType.RAID, title="Gruul mit Forga")
        assert evt.summary_line() == "[Raid] Gruul mit Forga"

    def test_with_character_name(self):
        evt = _make_event(event_type=EventType.RAID, title="Gruul mit Forga")
        assert evt.summary_line("Klopfbernd") == "[Raid] Gruul mit Forga (Klopfbernd)"

    def test_empty_character_name(self):
        evt = _make_event(title="Test")
        result = evt.summary_line("")
        assert "(" not in result


class TestDescriptionText:
    def test_empty_event(self):
        evt = _make_event()
        text = evt.description_text()
        assert "Confirmed: 0" in text
        assert "Signed: 0" in text
        assert "Total: 0" in text

    def test_with_comment(self):
        evt = _make_event(comment="Bring flasks!")
        text = evt.description_text()
        assert "Bring flasks!" in text

    def test_with_participants_grouped_by_status(self):
        evt = _make_event(
            participants=[
                _make_participant("Alice", Attendance.CONFIRMED),
                _make_participant("Bob", Attendance.SIGNED),
                _make_participant("Charlie", Attendance.DECLINED),
            ]
        )
        text = evt.description_text()
        assert "Confirmed (1)" in text
        assert "Signed (1)" in text
        assert "Declined (1)" in text
        assert "Alice" in text
        assert "Bob" in text
        assert "Charlie" in text

    def test_participant_with_comment(self):
        evt = _make_event(
            participants=[
                _make_participant("Alice", Attendance.CONFIRMED, comment="late 10min"),
            ]
        )
        text = evt.description_text()
        assert "late 10min" in text

    def test_no_comment_field(self):
        evt = _make_event(comment="")
        text = evt.description_text()
        lines = text.strip().split("\n")
        # First non-empty line should be the counts, not an empty comment
        assert "Confirmed:" in lines[0]
