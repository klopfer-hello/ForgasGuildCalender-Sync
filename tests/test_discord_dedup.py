"""When several forum threads exist for one event (cross-language / multi-client
duplicates), the sync keeps a single survivor — the one whose roster image has
the highest tool version — and deletes the rest.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from fgc_sync.models.enums import Attendance, EventType
from fgc_sync.models.events import CalendarEvent, Participant
from fgc_sync.services import sync_engine
from fgc_sync.services.config import Config


def _future_event(event_id="e1"):
    return CalendarEvent(
        event_id=event_id,
        title="Kara mit Muckli",
        event_type=EventType.RAID,
        raid="karazhan",
        date=(date.today() + timedelta(days=2)).isoformat(),
        server_hour=20,
        server_minute=0,
        comment="",
        creator="Muckli",
        revision=1,
        participants=[
            Participant("Klopfbernd", Attendance.CONFIRMED, "WARRIOR", "tank")
        ],
    )


@pytest.fixture
def config(tmp_path):
    cfg = Config(path=tmp_path / "config.json")
    cfg.set("wow_path", str(tmp_path / "wow"))
    cfg.set("account_folder", "acc")
    cfg.set("guild_key", "Guild")
    sv = cfg.saved_variables_path
    sv.parent.mkdir(parents=True, exist_ok=True)
    sv.write_text("-- fake")
    return cfg


def _patch_collect(monkeypatch, all_events):
    monkeypatch.setattr(
        sync_engine,
        "_collect_all_future_events",
        lambda config: (all_events, set(), []),
    )
    monkeypatch.setattr(
        sync_engine, "_is_local_data_stale", lambda config, discord: False
    )


def test_keeps_highest_version_thread_and_deletes_the_rest(config, monkeypatch):
    evt = _future_event()
    _patch_collect(monkeypatch, {evt.event_id: evt})  # no local mapping → adopt path

    discord = MagicMock()
    discord.is_configured = True
    discord.clear_members_cache = MagicMock()
    discord.clear_thread_cache = MagicMock()
    discord.ensure_unarchived = MagicMock(return_value=True)
    # Two threads for the same event: a versioned one and an old no-version one.
    discord.find_event_threads = MagicMock(
        return_value=[
            {"channel_id": "999", "image_id": "img-old", "hash": "h", "version": None},
            {
                "channel_id": "100",
                "image_id": "img-new",
                "hash": "h",
                "version": "2.11.1",
            },
        ]
    )
    content_hash = sync_engine.compute_event_hash(evt)
    discord.get_already_pinged_names = MagicMock(return_value={})
    discord.ping_members = MagicMock(return_value={})
    discord.delete_thread = MagicMock()
    discord.message_exists = MagicMock(return_value=True)
    discord.update_event = MagicMock(
        return_value={"image_id": "img-new", "hash": content_hash}
    )
    discord.find_image_message = MagicMock(return_value="img-new")
    discord.find_ics_message = MagicMock(return_value=None)
    discord.post_ics = MagicMock(return_value={"ics_id": "ics1", "hash": "h"})
    discord.update_ics = MagicMock(return_value={"ics_id": "ics1", "hash": "h"})

    result = sync_engine.execute_discord_sync(config, discord)

    # The no-version duplicate (id 999) is deleted; the versioned one is kept.
    discord.delete_thread.assert_called_once_with("999")
    assert result.errors == []
    discord.create_event_thread.assert_not_called()
    assert config.get("discord_message_mapping")[evt.event_id]["channel_id"] == "100"


def test_dead_mapping_routes_through_dedup_and_collapses(config, monkeypatch):
    """The real incident: a stale mapping pointing at a deleted thread must
    self-heal *through* the dedup path and collapse the surviving duplicates —
    not blindly recreate (which is what produced the duplicates)."""
    evt = _future_event()
    _patch_collect(monkeypatch, {evt.event_id: evt})
    config.set(
        "discord_message_mapping",
        {
            evt.event_id: {
                "channel_id": "dead",
                "message_ids": {"hash": "old"},
                "pinged": {},
            }
        },
    )

    discord = MagicMock()
    discord.is_configured = True
    discord.clear_members_cache = MagicMock()
    discord.clear_thread_cache = MagicMock()
    # "dead" 404s (forget it); survivor unarchive returns True.
    discord.ensure_unarchived = MagicMock(side_effect=lambda cid: cid != "dead")
    discord.find_event_threads = MagicMock(
        return_value=[
            {"channel_id": "999", "image_id": "old", "hash": "h", "version": None},
            {"channel_id": "100", "image_id": "new", "hash": "h", "version": "2.11.2"},
        ]
    )
    discord.get_already_pinged_names = MagicMock(return_value={})
    discord.ping_members = MagicMock(return_value={})
    discord.delete_thread = MagicMock()
    discord.message_exists = MagicMock(return_value=True)
    discord.update_event = MagicMock(return_value={"image_id": "new", "hash": "x"})
    discord.find_image_message = MagicMock(return_value="new")
    discord.find_ics_message = MagicMock(return_value=None)
    discord.post_ics = MagicMock(return_value={"ics_id": "ics1", "hash": "h"})
    discord.update_ics = MagicMock(return_value={"ics_id": "ics1", "hash": "h"})

    sync_engine.execute_discord_sync(config, discord)

    discord.create_event_thread.assert_not_called()  # adopted, not recreated
    discord.delete_thread.assert_called_once_with("999")  # no-version dup removed
    assert config.get("discord_message_mapping")[evt.event_id]["channel_id"] == "100"


def test_single_thread_is_just_adopted(config, monkeypatch):
    evt = _future_event()
    _patch_collect(monkeypatch, {evt.event_id: evt})

    discord = MagicMock()
    discord.is_configured = True
    discord.clear_members_cache = MagicMock()
    discord.clear_thread_cache = MagicMock()
    discord.ensure_unarchived = MagicMock(return_value=True)
    discord.find_event_threads = MagicMock(
        return_value=[
            {"channel_id": "100", "image_id": "m1", "hash": "h", "version": "2.11.1"}
        ]
    )
    discord.get_already_pinged_names = MagicMock(return_value={})
    discord.ping_members = MagicMock(return_value={})
    discord.delete_thread = MagicMock()
    discord.message_exists = MagicMock(return_value=True)
    discord.update_event = MagicMock(return_value={"image_id": "m1", "hash": "x"})
    discord.find_image_message = MagicMock(return_value="m1")
    discord.find_ics_message = MagicMock(return_value=None)
    discord.post_ics = MagicMock(return_value={"ics_id": "ics1", "hash": "h"})
    discord.update_ics = MagicMock(return_value={"ics_id": "ics1", "hash": "h"})

    sync_engine.execute_discord_sync(config, discord)

    discord.delete_thread.assert_not_called()
    discord.create_event_thread.assert_not_called()
