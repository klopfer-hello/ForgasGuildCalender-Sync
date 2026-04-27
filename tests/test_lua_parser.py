"""Tests for fgc_sync.services.lua_parser."""

import pytest

from fgc_sync.models.enums import Attendance
from fgc_sync.services.lua_parser import (
    extract_events,
    get_deleted_event_ids,
    list_character_names,
    list_guild_keys,
    parse_saved_variables,
)

# Minimal FGC_DB structure for testing
_MINIMAL_DB = {
    "profiles": {
        "Default": {
            "guildScoped": {
                "Thunderstrike-TestGuild": {
                    "events": {
                        "2026-04-10": {
                            1: {
                                "eventId": "evt-1",
                                "title": "Gruul mit Forga",
                                "type": "raid",
                                "raid": "gruul",
                                "serverTimeMinutes": 1185,  # 19:45
                                "comment": "Bring flasks",
                                "creator": "Forga",
                                "revision": 3,
                                "participants": {
                                    "Alice": {
                                        "attendance": 2,
                                        "classCode": "warrior",
                                        "roleCode": "tank",
                                        "group": 1,
                                        "slot": 1,
                                    },
                                    "Bob": {
                                        "attendance": 1,
                                        "classCode": "mage",
                                        "roleCode": "dd",
                                    },
                                },
                            },
                            2: {
                                "eventId": "evt-2",
                                "title": "Karazhan mit Forga",
                                "type": "raid",
                                "raid": "karazhan",
                                "serverTimeMinutes": 1200,  # 20:00
                                "comment": "",
                                "creator": "Forga",
                                "revision": 1,
                                "participants": {},
                            },
                        },
                    },
                    "sync": {
                        "deletedEvents": {
                            "evt-deleted-1": True,
                            "evt-deleted-2": True,
                        },
                    },
                },
            },
        },
    },
    "profileKeys": {
        "Klopfbernd - Thunderstrike": True,
        "Aluriel - Thunderstrike": True,
    },
}


# --- parse_saved_variables ---


class TestParseSavedVariables:
    def test_valid_file(self, tmp_path):
        sv = tmp_path / "ForgasGuildCalendar.lua"
        sv.write_text(
            'FGC_DB = {\n  ["profileKeys"] = {\n    ["Test - Realm"] = true,\n  },\n}\n',
            encoding="utf-8",
        )
        db = parse_saved_variables(sv)
        assert isinstance(db, dict)
        assert "profileKeys" in db

    def test_missing_fgc_db_raises(self, tmp_path):
        sv = tmp_path / "ForgasGuildCalendar.lua"
        sv.write_text("SOME_OTHER_VAR = {}", encoding="utf-8")
        with pytest.raises(ValueError, match="Could not find FGC_DB"):
            parse_saved_variables(sv)

    def test_empty_file_raises(self, tmp_path):
        sv = tmp_path / "ForgasGuildCalendar.lua"
        sv.write_text("", encoding="utf-8")
        with pytest.raises(ValueError):
            parse_saved_variables(sv)

    def test_nonexistent_file_raises(self, tmp_path):
        sv = tmp_path / "does_not_exist.lua"
        with pytest.raises(FileNotFoundError):
            parse_saved_variables(sv)


# --- extract_events ---


class TestExtractEvents:
    def test_extracts_events(self):
        events = extract_events(_MINIMAL_DB, "Thunderstrike-TestGuild")
        assert len(events) == 2

    def test_event_fields(self):
        events = extract_events(_MINIMAL_DB, "Thunderstrike-TestGuild")
        evt = next(e for e in events if e.event_id == "evt-1")
        assert evt.title == "Gruul mit Forga"
        assert evt.raid == "gruul"
        assert evt.date == "2026-04-10"
        assert evt.server_hour == 19
        assert evt.server_minute == 45
        assert evt.comment == "Bring flasks"
        assert evt.creator == "Forga"
        assert evt.revision == 3

    def test_participants_parsed(self):
        events = extract_events(_MINIMAL_DB, "Thunderstrike-TestGuild")
        evt = next(e for e in events if e.event_id == "evt-1")
        assert len(evt.participants) == 2
        alice = next(p for p in evt.participants if p.name == "Alice")
        assert alice.attendance == Attendance.CONFIRMED
        assert alice.class_code == "warrior"
        assert alice.group == 1
        bob = next(p for p in evt.participants if p.name == "Bob")
        assert bob.attendance == Attendance.SIGNED

    def test_unknown_guild_returns_empty(self):
        events = extract_events(_MINIMAL_DB, "NonexistentGuild")
        assert events == []

    def test_missing_profile_returns_empty(self):
        events = extract_events(_MINIMAL_DB, "Thunderstrike-TestGuild", profile="Nope")
        assert events == []

    def test_empty_db(self):
        events = extract_events({}, "AnyGuild")
        assert events == []

    def test_serverTimeMinutes_preferred(self):
        """serverTimeMinutes should be used over serverHour/serverMinute."""
        db = {
            "profiles": {
                "Default": {
                    "guildScoped": {
                        "G": {
                            "events": {
                                "2026-01-01": {
                                    1: {
                                        "eventId": "e1",
                                        "serverTimeMinutes": 605,  # 10:05
                                        "serverHour": 99,  # wrong, should be ignored
                                        "serverMinute": 99,
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }
        events = extract_events(db, "G")
        assert events[0].server_hour == 10
        assert events[0].server_minute == 5

    def test_fallback_to_serverHour(self):
        """When serverTimeMinutes is missing, fall back to serverHour/serverMinute."""
        db = {
            "profiles": {
                "Default": {
                    "guildScoped": {
                        "G": {
                            "events": {
                                "2026-01-01": {
                                    1: {
                                        "eventId": "e1",
                                        "serverHour": 20,
                                        "serverMinute": 30,
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }
        events = extract_events(db, "G")
        assert events[0].server_hour == 20
        assert events[0].server_minute == 30

    def test_events_as_list(self):
        """Events stored as a Lua array (list) instead of dict."""
        db = {
            "profiles": {
                "Default": {
                    "guildScoped": {
                        "G": {
                            "events": {
                                "2026-01-01": [
                                    {
                                        "eventId": "e1",
                                        "title": "Test",
                                        "serverTimeMinutes": 600,
                                    },
                                ],
                            },
                        },
                    },
                },
            },
        }
        events = extract_events(db, "G")
        assert len(events) == 1

    def test_skips_non_dict_events(self):
        """Non-dict entries in events should be skipped."""
        db = {
            "profiles": {
                "Default": {
                    "guildScoped": {
                        "G": {
                            "events": {
                                "2026-01-01": {
                                    1: "not a dict",
                                    2: {"eventId": "e1", "serverTimeMinutes": 0},
                                },
                            },
                        },
                    },
                },
            },
        }
        events = extract_events(db, "G")
        assert len(events) == 1

    def test_skips_entries_without_eventId(self):
        db = {
            "profiles": {
                "Default": {
                    "guildScoped": {
                        "G": {
                            "events": {
                                "2026-01-01": {
                                    1: {"title": "No ID", "serverTimeMinutes": 0},
                                    2: {"eventId": "e1", "serverTimeMinutes": 0},
                                },
                            },
                        },
                    },
                },
            },
        }
        events = extract_events(db, "G")
        assert len(events) == 1
        assert events[0].event_id == "e1"


# --- get_deleted_event_ids ---


class TestGetDeletedEventIds:
    def test_returns_deleted_ids(self):
        ids = get_deleted_event_ids(_MINIMAL_DB, "Thunderstrike-TestGuild")
        assert ids == {"evt-deleted-1", "evt-deleted-2"}

    def test_unknown_guild_returns_empty(self):
        ids = get_deleted_event_ids(_MINIMAL_DB, "NonexistentGuild")
        assert ids == set()

    def test_empty_db(self):
        ids = get_deleted_event_ids({}, "AnyGuild")
        assert ids == set()

    def test_no_deleted_events_key(self):
        db = {
            "profiles": {
                "Default": {
                    "guildScoped": {
                        "G": {"sync": {}},
                    },
                },
            },
        }
        ids = get_deleted_event_ids(db, "G")
        assert ids == set()


# --- list_guild_keys ---


class TestListGuildKeys:
    def test_returns_guild_keys(self):
        keys = list_guild_keys(_MINIMAL_DB)
        assert keys == ["Thunderstrike-TestGuild"]

    def test_empty_db(self):
        keys = list_guild_keys({})
        assert keys == []

    def test_missing_profile(self):
        keys = list_guild_keys(_MINIMAL_DB, profile="Nonexistent")
        assert keys == []


# --- list_character_names ---


class TestListCharacterNames:
    def test_returns_names_without_realm(self):
        names = list_character_names(_MINIMAL_DB)
        assert "Klopfbernd" in names
        assert "Aluriel" in names

    def test_no_realm_in_names(self):
        names = list_character_names(_MINIMAL_DB)
        for name in names:
            assert " - " not in name

    def test_empty_db(self):
        names = list_character_names({})
        assert names == []

    def test_deduplicates(self):
        db = {
            "profileKeys": {
                "Char - Realm1": True,
                "Char - Realm2": True,
            },
        }
        names = list_character_names(db)
        assert names == ["Char"]


# --- v2 (packed positional) layout ---


def _v2_event(
    event_id="evt-1",
    type_="raid",
    raid="gruul",
    title="Gruul mit Forga",
    comment="Bring flasks",
    creator="Forga",
    server_time_minutes=1185,
    revision=3,
    participants=None,
    roster=None,
):
    """Build a v2 packed event as slpp would decode it (1-based dict)."""
    return {
        1: event_id,
        2: type_,
        3: raid,
        4: title,
        5: comment,
        6: creator,
        7: server_time_minutes,
        8: revision,
        9: 1777000000,  # updatedAt
        10: creator,  # updatedBy
        11: participants or {},
        12: {},  # reservesByPlayer
        13: roster or {},
    }


def _v2_participant(
    attendance=1,
    class_code="WARRIOR",
    role_code="TANK",
    spec_index=1,
    item_level=118.0,
    comment="",
):
    """Build a v2 packed participant (dict form, 1-based)."""
    return {
        1: attendance,
        2: class_code,
        3: role_code,
        4: spec_index,
        5: item_level,
        6: comment,
        7: 1777000000,
        8: 1,
        9: 1777000000,
        10: "Forga",
    }


def _v2_roster(group=1, slot=1):
    return {1: group, 2: slot, 3: 1, 4: 1777000000, 5: "Forga"}


_V2_DB = {
    "profiles": {
        "Default": {
            "guildScoped": {
                "Thunderstrike-V2Guild": {
                    "_fgcEventStorageVersion": 2,
                    "events": {
                        "2026-05-15": [
                            _v2_event(
                                event_id="v2-evt-1",
                                title="TK mit Forga",
                                raid="tk",
                                comment="Consumables Pflicht",
                                server_time_minutes=1185,
                                revision=23,
                                participants={
                                    "Alice": _v2_participant(
                                        attendance=2,
                                        class_code="WARRIOR",
                                        role_code="TANK",
                                        item_level=118.5,
                                        comment="MT",
                                    ),
                                    "Bob": _v2_participant(
                                        attendance=1,
                                        class_code="MAGE",
                                        role_code="DAMAGER",
                                        item_level=115.0,
                                    ),
                                    "Carol": _v2_participant(
                                        attendance=3,  # DECLINED
                                        class_code="PRIEST",
                                        role_code="HEALER",
                                    ),
                                },
                                roster={
                                    "Alice": _v2_roster(group=1, slot=1),
                                    "Bob": _v2_roster(group=2, slot=3),
                                    # Carol declined → no roster entry
                                },
                            ),
                        ],
                    },
                    "sync": {
                        "deletedEvents": {
                            "v2-evt-deleted": {
                                "revision": 5,
                                "updatedBy": "Forga",
                                "updatedAt": 1777000000,
                            },
                        },
                    },
                },
            },
        },
    },
    "profileKeys": {
        "Forga - Thunderstrike": "Default",
    },
}


class TestV2ExtractEvents:
    def test_extracts_v2_event(self):
        events = extract_events(_V2_DB, "Thunderstrike-V2Guild")
        assert len(events) == 1
        evt = events[0]
        assert evt.event_id == "v2-evt-1"
        assert evt.title == "TK mit Forga"
        assert evt.event_type == "raid"
        assert evt.raid == "tk"
        assert evt.date == "2026-05-15"
        assert evt.server_hour == 19
        assert evt.server_minute == 45
        assert evt.comment == "Consumables Pflicht"
        assert evt.creator == "Forga"
        assert evt.revision == 23

    def test_v2_participants_merged_with_roster(self):
        events = extract_events(_V2_DB, "Thunderstrike-V2Guild")
        evt = events[0]
        assert len(evt.participants) == 3

        alice = next(p for p in evt.participants if p.name == "Alice")
        assert alice.attendance == Attendance.CONFIRMED
        assert alice.class_code == "WARRIOR"
        assert alice.role_code == "TANK"
        assert alice.item_level == 118.5
        assert alice.comment == "MT"
        assert alice.group == 1
        assert alice.slot == 1

        bob = next(p for p in evt.participants if p.name == "Bob")
        assert bob.attendance == Attendance.SIGNED
        assert bob.group == 2
        assert bob.slot == 3

        carol = next(p for p in evt.participants if p.name == "Carol")
        assert carol.attendance == Attendance.DECLINED
        # No roster entry → defaults to 0
        assert carol.group == 0
        assert carol.slot == 0

    def test_v2_event_as_list_form(self):
        """slpp may decode pure 1..n positional tables as Python lists."""
        list_form_event = [
            "v2-list-evt",
            "raid",
            "kara",
            "Karazhan",
            "",
            "Forga",
            1200,  # 20:00
            1,
            1777000000,
            "Forga",
            {},  # participants
            {},  # reserves
            {},  # roster
        ]
        db = {
            "profiles": {
                "Default": {
                    "guildScoped": {
                        "G": {
                            "_fgcEventStorageVersion": 2,
                            "events": {"2026-04-10": [list_form_event]},
                        },
                    },
                },
            },
        }
        events = extract_events(db, "G")
        assert len(events) == 1
        assert events[0].event_id == "v2-list-evt"
        assert events[0].server_hour == 20
        assert events[0].server_minute == 0

    def test_v2_get_deleted_event_ids(self):
        ids = get_deleted_event_ids(_V2_DB, "Thunderstrike-V2Guild")
        assert ids == {"v2-evt-deleted"}

    def test_v2_list_guild_keys(self):
        keys = list_guild_keys(_V2_DB)
        assert keys == ["Thunderstrike-V2Guild"]

    def test_v2_skips_event_without_id(self):
        bad_event = _v2_event(event_id=None)
        good_event = _v2_event(event_id="ok")
        db = {
            "profiles": {
                "Default": {
                    "guildScoped": {
                        "G": {
                            "_fgcEventStorageVersion": 2,
                            "events": {"2026-04-10": [bad_event, good_event]},
                        },
                    },
                },
            },
        }
        events = extract_events(db, "G")
        assert len(events) == 1
        assert events[0].event_id == "ok"

    def test_v2_missing_title_falls_back(self):
        evt = _v2_event(title=None)
        db = {
            "profiles": {
                "Default": {
                    "guildScoped": {
                        "G": {
                            "_fgcEventStorageVersion": 2,
                            "events": {"2026-04-10": [evt]},
                        },
                    },
                },
            },
        }
        events = extract_events(db, "G")
        assert events[0].title == "Untitled"


class TestVersionDispatch:
    def test_v1_used_when_version_missing(self):
        """A guild without _fgcEventStorageVersion dispatches to v1 (named keys)."""
        events = extract_events(_MINIMAL_DB, "Thunderstrike-TestGuild")
        assert len(events) == 2
        assert {e.event_id for e in events} == {"evt-1", "evt-2"}

    def test_v1_used_when_version_is_1(self):
        db = {
            "profiles": {
                "Default": {
                    "guildScoped": {
                        "G": {
                            "_fgcEventStorageVersion": 1,
                            "events": {
                                "2026-04-10": [
                                    {
                                        "eventId": "v1-evt",
                                        "title": "T",
                                        "type": "raid",
                                        "serverTimeMinutes": 600,
                                    },
                                ],
                            },
                        },
                    },
                },
            },
        }
        events = extract_events(db, "G")
        assert events[0].event_id == "v1-evt"

    def test_per_guild_dispatch(self):
        """One DB can hold a v1 guild and a v2 guild simultaneously."""
        db = {
            "profiles": {
                "Default": {
                    "guildScoped": {
                        "GuildV1": {
                            "events": {
                                "2026-04-10": [
                                    {
                                        "eventId": "v1-evt",
                                        "title": "T1",
                                        "serverTimeMinutes": 0,
                                    },
                                ],
                            },
                        },
                        "GuildV2": {
                            "_fgcEventStorageVersion": 2,
                            "events": {
                                "2026-04-10": [
                                    _v2_event(event_id="v2-evt", title="T2"),
                                ],
                            },
                        },
                    },
                },
            },
        }
        v1_events = extract_events(db, "GuildV1")
        v2_events = extract_events(db, "GuildV2")
        assert v1_events[0].event_id == "v1-evt"
        assert v2_events[0].event_id == "v2-evt"


class TestParseSavedVariablesFGC2Prefix:
    def test_fgc2_db_prefix_accepted(self, tmp_path):
        """Test fixture uses FGC2_DB while production uses FGC_DB."""
        sv = tmp_path / "ForgasGuildCalendar2Test.lua"
        sv.write_text(
            'FGC2_DB = {\n  ["profileKeys"] = {\n    ["Test - Realm"] = true,\n  },\n}\n',
            encoding="utf-8",
        )
        db = parse_saved_variables(sv)
        assert "profileKeys" in db
