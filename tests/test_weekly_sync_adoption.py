"""Regression tests for execute_weekly_sync across multi-client scenarios.

The weekly-overview thread is a single permanent forum thread. Discord's
forum-channel invariant is that the starter message id equals the thread
id, so the sync always targets ``channel_id`` as the PATCH target — even
when a client's local mapping is empty (first-time adoption) or stale
(legacy mapping pointing at a previous orphan reply).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from fgc_sync.models.enums import Attendance, EventType
from fgc_sync.models.events import CalendarEvent, Participant
from fgc_sync.services import sync_engine
from fgc_sync.services.config import Config

_EXISTING_THREAD_ID = "1494684833199423570"
_STALE_REPLY_ID = "9999999999999999999"


def _evt() -> CalendarEvent:
    monday, _sunday, _key = sync_engine.current_week_bounds()
    return CalendarEvent(
        event_id="e1",
        title="Gruul mit Forga",
        event_type=EventType.RAID,
        raid="gruul",
        date=monday.isoformat(),
        server_hour=20,
        server_minute=0,
        comment="",
        creator="Forga",
        revision=1,
        participants=[
            Participant("Klopfbernd", Attendance.CONFIRMED, "WARRIOR", "tank"),
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


@pytest.fixture
def patched_collect(monkeypatch):
    """Bypass SavedVariables parsing — load_events returns a single test event."""
    evt = _evt()
    monkeypatch.setattr(
        sync_engine,
        "_load_events_for_overview",
        lambda config: ({evt.event_id: evt}, []),
    )
    monkeypatch.setattr(
        sync_engine, "_is_local_data_stale", lambda config, discord: False
    )


def _make_discord(message_exists: bool = True) -> MagicMock:
    discord = MagicMock()
    discord.is_configured = True
    discord.clear_thread_cache = MagicMock()
    discord.find_thread_by_name = MagicMock(return_value=_EXISTING_THREAD_ID)
    discord.ensure_unarchived = MagicMock()
    discord.message_exists = MagicMock(return_value=message_exists)
    discord.update_weekly_image = MagicMock()
    discord.post_weekly_image = MagicMock(return_value="new-reply-id")
    discord.create_weekly_thread = MagicMock(
        return_value=(_EXISTING_THREAD_ID, _EXISTING_THREAD_ID)
    )
    discord.cleanup_weekly_thread_orphans = MagicMock(return_value=0)
    return discord


class TestStarterTargeting:
    def test_first_time_adoption_patches_starter(self, config, patched_collect):
        """Second client with empty mapping must PATCH the starter (current week)
        and POST the next-week reply on first sync."""
        discord = _make_discord()

        result = sync_engine.execute_weekly_sync(config, discord)

        assert result.errors == []
        # Starter (current week) is PATCHed in place at channel_id
        discord.update_weekly_image.assert_called_once()
        args = discord.update_weekly_image.call_args.args
        assert args[0] == _EXISTING_THREAD_ID
        assert args[1] == _EXISTING_THREAD_ID  # starter id == thread id
        # Next-week reply is created on first sync because next_message_id is absent
        discord.post_weekly_image.assert_called_once()

        mapping = config.get("discord_weekly_mapping")
        assert mapping["channel_id"] == _EXISTING_THREAD_ID
        assert mapping["message_id"] == _EXISTING_THREAD_ID
        assert mapping["next_message_id"] == "new-reply-id"

    def test_stale_mapping_converges_on_starter(self, config, patched_collect):
        """A client whose mapping points at an orphan reply must re-target the starter."""
        # Linux client's mapping from a previous buggy run pointed at an
        # orphan reply (id != channel_id).
        config.set(
            "discord_weekly_mapping",
            {
                "channel_id": _EXISTING_THREAD_ID,
                "message_id": _STALE_REPLY_ID,
                "hash": "deadbeef",
                "week_key": "2020-W01",
                "sv_mtime": 0,
            },
        )
        discord = _make_discord()

        sync_engine.execute_weekly_sync(config, discord)

        # Must PATCH the starter, not the stale reply id
        args = discord.update_weekly_image.call_args.args
        assert args[1] == _EXISTING_THREAD_ID
        # Mapping must be rewritten to point at the starter going forward
        assert config.get("discord_weekly_mapping")["message_id"] == _EXISTING_THREAD_ID

    def test_skip_when_mapping_already_correct_and_unchanged(
        self, config, patched_collect, monkeypatch
    ):
        """Both messages skip when current+next mappings match content and week keys."""
        from fgc_sync.services.weekly_overview import (
            collect_week_events,
            compute_weekly_hash,
            current_week_bounds,
            next_week_bounds,
        )

        evt = _evt()
        by_id = {evt.event_id: evt}
        _cur_mon, _cur_sun, cur_week = current_week_bounds()
        _nxt_mon, _nxt_sun, nxt_week = next_week_bounds()
        cur_hash = compute_weekly_hash(collect_week_events(by_id, week_offset=0))
        nxt_hash = compute_weekly_hash(collect_week_events(by_id, week_offset=1))

        config.set(
            "discord_weekly_mapping",
            {
                "channel_id": _EXISTING_THREAD_ID,
                "message_id": _EXISTING_THREAD_ID,
                "hash": cur_hash,
                "week_key": cur_week,
                "next_message_id": "reply-id-123",
                "next_hash": nxt_hash,
                "next_week_key": nxt_week,
                "sv_mtime": 0,
            },
        )
        discord = _make_discord()

        result = sync_engine.execute_weekly_sync(config, discord)

        assert result.skipped == 2  # starter + next both skip
        discord.update_weekly_image.assert_not_called()
        discord.post_weekly_image.assert_not_called()


class TestOrphanCleanup:
    def test_cleanup_invoked_after_patch(self, config, patched_collect):
        """After every successful sync we attempt to clean up orphan replies.
        The next-week reply id must be in keep_ids so it isn't deleted."""
        discord = _make_discord()

        sync_engine.execute_weekly_sync(config, discord)

        discord.cleanup_weekly_thread_orphans.assert_called_once_with(
            _EXISTING_THREAD_ID, keep_ids={"new-reply-id"}
        )

    def test_cleanup_failure_does_not_break_sync(self, config, patched_collect):
        """If cleanup raises, the rest of the sync still reports success."""
        discord = _make_discord()
        discord.cleanup_weekly_thread_orphans.side_effect = RuntimeError("boom")

        result = sync_engine.execute_weekly_sync(config, discord)

        # Sync still reports the starter PATCH (updated) and next-week POST (created);
        # cleanup failure is logged but doesn't propagate.
        assert result.errors == []
        assert result.updated == 1
        assert result.created == 1
