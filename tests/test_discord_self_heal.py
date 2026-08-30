"""Self-heal: a per-event mapping pointing at an externally-deleted thread must
recreate the thread instead of erroring on every sync forever.

This is what previously forced users to hand-clear ``discord_message_mapping``
after another client deleted their threads.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from fgc_sync.models.enums import Attendance, EventType
from fgc_sync.models.events import CalendarEvent, Participant
from fgc_sync.services import sync_engine
from fgc_sync.services.config import Config


def _future_event(event_id: str = "e1") -> CalendarEvent:
    return CalendarEvent(
        event_id=event_id,
        title="Gruul mit Forga",
        event_type=EventType.RAID,
        raid="gruul",
        date=(date.today() + timedelta(days=2)).isoformat(),
        server_hour=20,
        server_minute=0,
        comment="",
        creator="Forga",
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
        lambda config: (all_events, set(), [], set()),
    )
    monkeypatch.setattr(
        sync_engine, "_is_local_data_stale", lambda config, discord: False
    )


def test_recreates_thread_when_mapped_one_is_gone(config, monkeypatch):
    evt = _future_event()
    _patch_collect(monkeypatch, {evt.event_id: evt})
    config.set(
        "discord_message_mapping",
        {
            evt.event_id: {
                "channel_id": "dead-thread",
                "message_ids": {"hash": "old"},
                "pinged": {},
            }
        },
    )

    discord = MagicMock()
    discord.is_configured = True
    discord.clear_members_cache = MagicMock()
    discord.clear_thread_cache = MagicMock()
    discord.find_ics_message = MagicMock(return_value=None)
    discord.post_ics = MagicMock(return_value={"ics_id": "ics1", "hash": "h"})
    discord.update_ics = MagicMock(return_value={"ics_id": "ics1", "hash": "h"})
    discord.ensure_unarchived = MagicMock(return_value=False)  # thread is gone (404)
    discord.find_event_threads = MagicMock(return_value=[])  # nothing to adopt
    discord.create_event_thread = MagicMock(
        return_value=("new-thread", {"image_id": "m1", "hash": "h1", "sv_mtime": 0})
    )
    discord.ping_members = MagicMock(return_value={})
    discord.delete_thread = MagicMock()

    result = sync_engine.execute_discord_sync(config, discord)

    # Recreated (nothing to adopt), not errored.
    discord.create_event_thread.assert_called_once()
    assert result.errors == []
    assert result.created == 1
    discord.delete_thread.assert_not_called()
    # Mapping now points at the fresh thread — no manual reset needed.
    assert (
        config.get("discord_message_mapping")[evt.event_id]["channel_id"]
        == "new-thread"
    )


def test_live_thread_is_not_recreated(config, monkeypatch):
    evt = _future_event()
    _patch_collect(monkeypatch, {evt.event_id: evt})
    # Mapping hash matches current content so the existing-thread path is a no-op.
    content_hash = sync_engine.compute_event_hash(evt)
    config.set(
        "discord_message_mapping",
        {
            evt.event_id: {
                "channel_id": "live-thread",
                "message_ids": {"image_id": "m1", "hash": content_hash},
                "pinged": {"Klopfbernd": "p1"},
            }
        },
    )

    discord = MagicMock()
    discord.is_configured = True
    discord.clear_members_cache = MagicMock()
    discord.clear_thread_cache = MagicMock()
    discord.find_ics_message = MagicMock(return_value=None)
    discord.post_ics = MagicMock(return_value={"ics_id": "ics1", "hash": "h"})
    discord.update_ics = MagicMock(return_value={"ics_id": "ics1", "hash": "h"})
    discord.ensure_unarchived = MagicMock(return_value=True)  # thread is alive
    discord.get_already_pinged_names = MagicMock(return_value={"Klopfbernd": "p1"})
    discord.ping_members = MagicMock(return_value={})
    discord.remove_mentions = MagicMock()
    discord.delete_thread = MagicMock()

    result = sync_engine.execute_discord_sync(config, discord)

    discord.create_event_thread.assert_not_called()
    assert result.errors == []
