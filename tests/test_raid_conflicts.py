"""Tests for the cross-event unavailability derivation (raid_conflicts)."""

from fgc_sync.models.enums import Attendance, EventType
from fgc_sync.models.events import CalendarEvent, Participant
from fgc_sync.services.discord_poster import compute_event_hash
from fgc_sync.services.raid_conflicts import (
    is_event_cancelled,
    mark_unavailable_participants,
)


def _participant(name: str, attendance: Attendance) -> Participant:
    return Participant(
        name=name, attendance=attendance, class_code="MAGE", role_code="DAMAGER"
    )


def _event(event_id, raid, date, creator, participants, **overrides) -> CalendarEvent:
    defaults = dict(
        event_id=event_id,
        title=f"{raid} run",
        event_type=EventType.RAID,
        raid=raid,
        date=date,
        server_hour=20,
        server_minute=0,
        comment="",
        creator=creator,
        revision=1,
        participants=participants,
    )
    defaults.update(overrides)
    return CalendarEvent(**defaults)


def _find(event: CalendarEvent, name: str) -> Participant:
    return next(p for p in event.participants if p.name == name)


class TestMarkUnavailableParticipants:
    def test_signed_and_confirmed_elsewhere_same_week(self):
        # 2026-07-22 and 2026-07-26 are both in the Wed-anchored reset week
        signed_evt = _event(
            "a", "tk", "2026-07-26", "Walze", [_participant("Vivi", Attendance.SIGNED)]
        )
        confirmed_evt = _event(
            "b",
            "tk",
            "2026-07-22",
            "Forga",
            [_participant("Vivi", Attendance.CONFIRMED)],
        )
        mark_unavailable_participants([signed_evt, confirmed_evt])
        assert _find(signed_evt, "Vivi").unavailable is True

    def test_same_creator_does_not_conflict(self):
        signed_evt = _event(
            "a", "tk", "2026-07-26", "Forga", [_participant("Vivi", Attendance.SIGNED)]
        )
        confirmed_evt = _event(
            "b",
            "tk",
            "2026-07-22",
            "Forga",
            [_participant("Vivi", Attendance.CONFIRMED)],
        )
        mark_unavailable_participants([signed_evt, confirmed_evt])
        assert _find(signed_evt, "Vivi").unavailable is False

    def test_different_raid_does_not_conflict(self):
        signed_evt = _event(
            "a", "tk", "2026-07-26", "Walze", [_participant("Vivi", Attendance.SIGNED)]
        )
        confirmed_evt = _event(
            "b",
            "ssc",
            "2026-07-22",
            "Forga",
            [_participant("Vivi", Attendance.CONFIRMED)],
        )
        mark_unavailable_participants([signed_evt, confirmed_evt])
        assert _find(signed_evt, "Vivi").unavailable is False

    def test_reset_week_boundary(self):
        # Tue 2026-07-21 belongs to the previous reset week of Wed 2026-07-22
        signed_evt = _event(
            "a", "tk", "2026-07-22", "Walze", [_participant("Vivi", Attendance.SIGNED)]
        )
        confirmed_evt = _event(
            "b",
            "tk",
            "2026-07-21",
            "Forga",
            [_participant("Vivi", Attendance.CONFIRMED)],
        )
        mark_unavailable_participants([signed_evt, confirmed_evt])
        assert _find(signed_evt, "Vivi").unavailable is False

        # Tue 2026-07-28 is still inside the week that started Wed 2026-07-22
        late_signed = _event(
            "c", "tk", "2026-07-28", "Walze", [_participant("Vivi", Attendance.SIGNED)]
        )
        mark_unavailable_participants([late_signed, confirmed_evt])
        assert _find(late_signed, "Vivi").unavailable is False  # different week
        mark_unavailable_participants(
            [
                late_signed,
                _event(
                    "d",
                    "tk",
                    "2026-07-24",
                    "Forga",
                    [_participant("Vivi", Attendance.CONFIRMED)],
                ),
            ]
        )
        assert _find(late_signed, "Vivi").unavailable is True

    def test_double_raid_conflicts_with_component(self):
        signed_evt = _event(
            "a", "tk", "2026-07-26", "Walze", [_participant("Vivi", Attendance.SIGNED)]
        )
        confirmed_evt = _event(
            "b",
            "ssc_tk",
            "2026-07-24",
            "Forga",
            [_participant("Vivi", Attendance.CONFIRMED)],
        )
        mark_unavailable_participants([signed_evt, confirmed_evt])
        assert _find(signed_evt, "Vivi").unavailable is True

    def test_legacy_long_raid_name_shares_lockout(self):
        signed_evt = _event(
            "a",
            "tempest_keep",
            "2026-07-26",
            "Walze",
            [_participant("Vivi", Attendance.SIGNED)],
        )
        confirmed_evt = _event(
            "b",
            "tk",
            "2026-07-24",
            "Forga",
            [_participant("Vivi", Attendance.CONFIRMED)],
        )
        mark_unavailable_participants([signed_evt, confirmed_evt])
        assert _find(signed_evt, "Vivi").unavailable is True

    def test_cancelled_event_is_no_conflict_source(self):
        signed_evt = _event(
            "a", "tk", "2026-07-26", "Walze", [_participant("Vivi", Attendance.SIGNED)]
        )
        cancelled = _event(
            "b",
            "tk",
            "2026-07-24",
            "Forga",
            [_participant("Vivi", Attendance.CONFIRMED)],
            comment="!}called off",
        )
        assert is_event_cancelled(cancelled) is True
        mark_unavailable_participants([signed_evt, cancelled])
        assert _find(signed_evt, "Vivi").unavailable is False

    def test_only_signed_participants_are_marked(self):
        evt = _event(
            "a",
            "tk",
            "2026-07-26",
            "Walze",
            [
                _participant("Conf", Attendance.CONFIRMED),
                _participant("Benched", Attendance.BENCHED),
                _participant("Decl", Attendance.DECLINED),
            ],
        )
        other = _event(
            "b",
            "tk",
            "2026-07-24",
            "Forga",
            [
                _participant("Conf", Attendance.CONFIRMED),
                _participant("Benched", Attendance.CONFIRMED),
                _participant("Decl", Attendance.CONFIRMED),
            ],
        )
        mark_unavailable_participants([evt, other])
        assert all(p.unavailable is False for p in evt.participants)

    def test_non_raid_events_ignored(self):
        signed_evt = _event(
            "a",
            "",
            "2026-07-26",
            "Walze",
            [_participant("Vivi", Attendance.SIGNED)],
            event_type=EventType.MEETING,
        )
        confirmed_evt = _event(
            "b",
            "tk",
            "2026-07-24",
            "Forga",
            [_participant("Vivi", Attendance.CONFIRMED)],
        )
        mark_unavailable_participants([signed_evt, confirmed_evt])
        assert _find(signed_evt, "Vivi").unavailable is False


class TestUnavailableInEventHash:
    def test_flag_changes_hash(self):
        evt = _event(
            "a", "tk", "2026-07-26", "Walze", [_participant("Vivi", Attendance.SIGNED)]
        )
        base = compute_event_hash(evt)
        evt.participants[0].unavailable = True
        assert compute_event_hash(evt) != base

    def test_no_flag_keeps_legacy_hash_payload(self):
        evt_a = _event(
            "a", "tk", "2026-07-26", "Walze", [_participant("Vivi", Attendance.SIGNED)]
        )
        evt_b = _event(
            "a", "tk", "2026-07-26", "Walze", [_participant("Vivi", Attendance.SIGNED)]
        )
        assert compute_event_hash(evt_a) == compute_event_hash(evt_b)
