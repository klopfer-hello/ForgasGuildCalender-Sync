"""Tests for private helper functions in fgc_sync.services.sync_engine."""

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from fgc_sync.models.enums import Attendance, EventType
from fgc_sync.models.events import CalendarEvent, Participant
from fgc_sync.services.sync_engine import (
    DISCORD_LOOKAHEAD_DAYS,
    EXPIRED_EVENT_HOURS,
    _collect_all_future_events,
    _collect_syncable_events,
    _event_to_datetime,
    _find_participating_character,
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


def _make_participant(name: str, attendance: Attendance, **kw) -> Participant:
    defaults = dict(class_code="warrior", role_code="tank")
    defaults.update(kw)
    return Participant(name=name, attendance=attendance, **defaults)


# --- _event_to_datetime ---


class TestEventToDatetime:
    def test_basic_conversion(self):
        evt = _make_event(date="2026-04-10", server_hour=19, server_minute=45)
        dt = _event_to_datetime(evt, "Europe/Berlin")
        assert dt.year == 2026
        assert dt.month == 4
        assert dt.day == 10
        assert dt.hour == 19
        assert dt.minute == 45
        assert dt.tzinfo == ZoneInfo("Europe/Berlin")

    def test_midnight(self):
        evt = _make_event(date="2026-01-01", server_hour=0, server_minute=0)
        dt = _event_to_datetime(evt, "UTC")
        assert dt.hour == 0
        assert dt.minute == 0

    def test_different_timezone(self):
        evt = _make_event(date="2026-04-10", server_hour=20, server_minute=0)
        dt = _event_to_datetime(evt, "US/Eastern")
        assert dt.tzinfo == ZoneInfo("US/Eastern")


# --- _find_participating_character ---


class TestFindParticipatingCharacter:
    def test_finds_signed_character(self):
        evt = _make_event(
            participants=[
                _make_participant("Klopfbernd", Attendance.SIGNED),
                _make_participant("Other", Attendance.DECLINED),
            ]
        )
        result = _find_participating_character(evt, ["Klopfbernd", "Aluriel"])
        assert result == "Klopfbernd"

    def test_finds_confirmed_character(self):
        evt = _make_event(
            participants=[
                _make_participant("Aluriel", Attendance.CONFIRMED),
            ]
        )
        result = _find_participating_character(evt, ["Klopfbernd", "Aluriel"])
        assert result == "Aluriel"

    def test_skips_declined(self):
        evt = _make_event(
            participants=[
                _make_participant("Klopfbernd", Attendance.DECLINED),
            ]
        )
        result = _find_participating_character(evt, ["Klopfbernd"])
        assert result is None

    def test_skips_benched(self):
        evt = _make_event(
            participants=[
                _make_participant("Klopfbernd", Attendance.BENCHED),
            ]
        )
        result = _find_participating_character(evt, ["Klopfbernd"])
        assert result is None

    def test_no_matching_character(self):
        evt = _make_event(
            participants=[
                _make_participant("Stranger", Attendance.CONFIRMED),
            ]
        )
        result = _find_participating_character(evt, ["Klopfbernd"])
        assert result is None

    def test_empty_participants(self):
        evt = _make_event(participants=[])
        result = _find_participating_character(evt, ["Klopfbernd"])
        assert result is None

    def test_empty_character_list(self):
        evt = _make_event(
            participants=[
                _make_participant("Klopfbernd", Attendance.CONFIRMED),
            ]
        )
        result = _find_participating_character(evt, [])
        assert result is None


# --- _collect_syncable_events ---


class TestCollectSyncableEvents:
    def _write_sv(
        self, tmp_path, guild_key, events_by_date, profile_keys=None, deleted=None
    ):
        """Write a minimal SavedVariables file and return a Config."""
        from fgc_sync.services.config import Config

        # Build Lua content
        lines = [
            'FGC_DB = {\n  ["profiles"] = {\n    ["Default"] = {\n      ["guildScoped"] = {\n'
        ]
        lines.append(f'        ["{guild_key}"] = {{\n')
        lines.append('          ["events"] = {\n')
        for date_key, evts in events_by_date.items():
            lines.append(f'            ["{date_key}"] = {{\n')
            for i, evt in enumerate(evts, 1):
                lines.append(f"              [{i}] = {{\n")
                for k, v in evt.items():
                    if isinstance(v, str):
                        lines.append(f'                ["{k}"] = "{v}",\n')
                    elif isinstance(v, dict):
                        lines.append(f'                ["{k}"] = {{\n')
                        for pk, pv in v.items():
                            lines.append(f'                  ["{pk}"] = {{\n')
                            for ppk, ppv in pv.items():
                                if isinstance(ppv, str):
                                    lines.append(
                                        f'                    ["{ppk}"] = "{ppv}",\n'
                                    )
                                else:
                                    lines.append(
                                        f'                    ["{ppk}"] = {ppv},\n'
                                    )
                            lines.append("                  },\n")
                        lines.append("                },\n")
                    else:
                        lines.append(f'                ["{k}"] = {v},\n')
                lines.append("              },\n")
            lines.append("            },\n")
        lines.append("          },\n")
        if deleted:
            lines.append('          ["sync"] = {\n            ["deletedEvents"] = {\n')
            for d in deleted:
                lines.append(f'              ["{d}"] = true,\n')
            lines.append("            },\n          },\n")
        lines.append("        },\n")
        lines.append("      },\n    },\n  },\n")

        if profile_keys:
            lines.append('  ["profileKeys"] = {\n')
            for pk in profile_keys:
                lines.append(f'    ["{pk}"] = true,\n')
            lines.append("  },\n")

        lines.append("}\n")

        sv_dir = tmp_path / "WTF" / "Account" / "TestAccount" / "SavedVariables"
        sv_dir.mkdir(parents=True)
        sv_file = sv_dir / "ForgasGuildCalendar.lua"
        sv_file.write_text("".join(lines), encoding="utf-8")

        config = Config(tmp_path / "config.json")
        config.set("wow_path", str(tmp_path))
        config.set("account_folder", "TestAccount")
        config.set("guild_key", guild_key)
        return config

    def test_returns_future_events_with_active_character(self, tmp_path):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        config = self._write_sv(
            tmp_path,
            "TestGuild",
            {
                tomorrow: [
                    {
                        "eventId": "e1",
                        "title": "Raid",
                        "type": "raid",
                        "raid": "gruul",
                        "serverTimeMinutes": 1200,
                        "comment": "",
                        "creator": "Forga",
                        "revision": 1,
                        "participants": {
                            "Klopf": {
                                "attendance": 1,
                                "classCode": "warrior",
                                "roleCode": "tank",
                            },
                        },
                    }
                ],
            },
            profile_keys=["Klopf - Realm"],
        )
        result, deleted_ids, errors = _collect_syncable_events(config)
        assert errors == []
        assert "e1" in result
        assert result["e1"][1] == "Klopf"

    def test_skips_past_events(self, tmp_path):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        config = self._write_sv(
            tmp_path,
            "TestGuild",
            {
                yesterday: [
                    {
                        "eventId": "e1",
                        "title": "Old Raid",
                        "serverTimeMinutes": 1200,
                        "participants": {
                            "Klopf": {
                                "attendance": 1,
                                "classCode": "warrior",
                                "roleCode": "tank",
                            },
                        },
                    }
                ],
            },
            profile_keys=["Klopf - Realm"],
        )
        result, _, errors = _collect_syncable_events(config)
        assert errors == []
        assert result == {}

    def test_errors_on_missing_sv(self, tmp_path):
        from fgc_sync.services.config import Config

        config = Config(tmp_path / "config.json")
        config.set("wow_path", str(tmp_path / "nonexistent"))
        config.set("account_folder", "X")
        config.set("guild_key", "G")
        _, _, errors = _collect_syncable_events(config)
        assert len(errors) == 1
        assert "not found" in errors[0].lower()

    def test_errors_on_missing_guild_key(self, tmp_path):
        from fgc_sync.services.config import Config

        config = Config(tmp_path / "config.json")
        config.set("wow_path", str(tmp_path))
        config.set("account_folder", "X")
        # no guild_key
        _, _, errors = _collect_syncable_events(config)
        assert len(errors) == 1
        assert "guild" in errors[0].lower()


# --- _collect_all_future_events ---


class TestCollectAllFutureEvents:
    def test_errors_on_no_sv_path(self, tmp_path):
        from fgc_sync.services.config import Config

        config = Config(tmp_path / "config.json")
        # no wow_path set
        _, _, errors = _collect_all_future_events(config)
        assert len(errors) == 1

    def test_errors_on_no_guild_key(self, tmp_path):
        from fgc_sync.services.config import Config

        config = Config(tmp_path / "config.json")
        config.set("wow_path", str(tmp_path))
        config.set("account_folder", "X")
        _, _, errors = _collect_all_future_events(config)
        assert len(errors) == 1


# --- Constants ---


class TestConstants:
    def test_expired_event_hours(self):
        assert EXPIRED_EVENT_HOURS == 24

    def test_discord_lookahead_days(self):
        assert DISCORD_LOOKAHEAD_DAYS == 7
