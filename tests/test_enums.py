"""Tests for fgc_sync.models.enums."""

from fgc_sync.models.enums import Attendance, EventType, SyncAction

# --- Attendance ---


class TestAttendanceLabel:
    def test_signed_label(self):
        assert Attendance.SIGNED.label == "Signed"

    def test_confirmed_label(self):
        assert Attendance.CONFIRMED.label == "Confirmed"

    def test_declined_label(self):
        assert Attendance.DECLINED.label == "Declined"

    def test_benched_label(self):
        assert Attendance.BENCHED.label == "Benched"


class TestAttendanceIsActive:
    def test_signed_is_active(self):
        assert Attendance.is_active(Attendance.SIGNED) is True

    def test_confirmed_is_active(self):
        assert Attendance.is_active(Attendance.CONFIRMED) is True

    def test_declined_is_not_active(self):
        assert Attendance.is_active(Attendance.DECLINED) is False

    def test_benched_is_not_active(self):
        assert Attendance.is_active(Attendance.BENCHED) is False

    def test_unknown_value_is_not_active(self):
        assert Attendance.is_active(99) is False

    def test_zero_is_not_active(self):
        assert Attendance.is_active(0) is False


# --- EventType ---


class TestEventTypeLabel:
    def test_raid_label(self):
        assert EventType.RAID.label == "Raid"

    def test_dungeon_label(self):
        assert EventType.DUNGEON.label == "Dungeon"

    def test_pvp_label(self):
        assert EventType.PVP.label == "Pvp"

    def test_meeting_label(self):
        assert EventType.MEETING.label == "Meeting"


# --- SyncAction ---


class TestSyncActionValues:
    def test_create(self):
        assert SyncAction.CREATE == "create"

    def test_update(self):
        assert SyncAction.UPDATE == "update"

    def test_delete(self):
        assert SyncAction.DELETE == "delete"
