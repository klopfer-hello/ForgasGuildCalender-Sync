"""The weekly empty-week guard: a zero-event local render must never overwrite
a populated remote overview (a stale/incomplete second client blanking it)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from fgc_sync.services import sync_engine
from fgc_sync.services.config import Config
from fgc_sync.services.weekly_overview import EMPTY_WEEK_HASH, current_week_bounds

_THREAD_ID = "weekly-thread-1"


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


@pytest.fixture(autouse=True)
def _no_events(monkeypatch):
    """Local parse yields zero events → empty-week render."""
    monkeypatch.setattr(
        sync_engine, "_load_events_for_overview", lambda config: ({}, [])
    )
    monkeypatch.setattr(
        sync_engine, "_is_local_data_stale", lambda config, discord: False
    )


def _discord(remote_hash):
    d = MagicMock()
    d.is_configured = True
    d.clear_thread_cache = MagicMock()
    d.ensure_unarchived = MagicMock(return_value=True)
    d.message_exists = MagicMock(return_value=True)
    d.get_weekly_image_hash = MagicMock(return_value=remote_hash)
    d.update_weekly_image = MagicMock()
    d.post_weekly_image = MagicMock(return_value="reply-id")
    d.cleanup_weekly_thread_orphans = MagicMock(return_value=0)
    return d


def _mapping(cur_week):
    # message_id==channel_id and week matches, but stored hash differs from the
    # empty render so the plain skip branch does NOT fire — only the guard can.
    return {
        "channel_id": _THREAD_ID,
        "message_id": _THREAD_ID,
        "hash": "populated",
        "week_key": cur_week,
        "next_message_id": "reply-id",
        "next_hash": "populated",
        "next_week_key": "2999-W01",
        "sv_mtime": 0,
    }


def test_empty_render_does_not_overwrite_populated_starter(config):
    _, _, cur_week = current_week_bounds()
    config.set("discord_weekly_mapping", _mapping(cur_week))
    discord = _discord(remote_hash="deadbeef")  # remote currently populated

    result = sync_engine.execute_weekly_sync(config, discord)

    discord.update_weekly_image.assert_not_called()  # starter NOT blanked
    assert result.errors == []


def test_empty_render_overwrites_already_empty_remote(config):
    _, _, cur_week = current_week_bounds()
    config.set("discord_weekly_mapping", _mapping(cur_week))
    # Remote is already the empty image → guard does not block (write is a no-op
    # update, not a destructive blank).
    discord = _discord(remote_hash=EMPTY_WEEK_HASH)

    sync_engine.execute_weekly_sync(config, discord)

    discord.update_weekly_image.assert_called()
