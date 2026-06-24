"""Regression tests for the per-event Discord cleanup bulk-deletion guard.

A divergent or partial SavedVariables read (e.g. another client resolving a
different namespace, or one whose data lacks this guild's events) yields a
*non-empty* but wrong event set. The pre-existing all-empty guard only catches
a fully-empty parse, so without a second guard such a read would mark many
still-valid events as "removed" and permanently delete their forum threads.

These tests exercise ``execute_discord_sync``'s cleanup with events that are
all expired (so the create/update/ping path is skipped on the first ``continue``
in the main loop) — isolating the deletion logic.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from fgc_sync.models.enums import Attendance, EventType
from fgc_sync.models.events import CalendarEvent, Participant
from fgc_sync.services import sync_engine
from fgc_sync.services.config import Config


def _expired_event(event_id: str) -> CalendarEvent:
    """An event well in the past — skipped by the main loop, not in mapping."""
    return CalendarEvent(
        event_id=event_id,
        title="Old Raid",
        event_type=EventType.RAID,
        raid="gruul",
        date=(date.today() - timedelta(days=10)).isoformat(),
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


def _make_discord() -> MagicMock:
    discord = MagicMock()
    discord.is_configured = True
    discord.clear_members_cache = MagicMock()
    discord.clear_thread_cache = MagicMock()
    discord.delete_thread = MagicMock()
    return discord


def _mapping(n: int) -> dict:
    return {f"e{i}": {"channel_id": f"chan-{i}"} for i in range(n)}


def _patch_collect(monkeypatch, all_events: dict, deleted_ids=None):
    monkeypatch.setattr(
        sync_engine,
        "_collect_all_future_events",
        lambda config: (all_events, set(deleted_ids or ()), []),
    )
    monkeypatch.setattr(
        sync_engine, "_is_local_data_stale", lambda config, discord: False
    )


class TestBulkDeletionGuard:
    def test_blocks_mass_deletion_from_divergent_read(self, config, monkeypatch):
        """13 mapped threads all 'missing' in one sync → guard skips every delete."""
        config.set("discord_message_mapping", _mapping(13))
        # One unrelated (expired, not-in-mapping) event keeps the parse non-empty,
        # so the all-empty guard does NOT fire — only the bulk guard can save us.
        _patch_collect(monkeypatch, {"other": _expired_event("other")})
        discord = _make_discord()

        result = sync_engine.execute_discord_sync(config, discord)

        discord.delete_thread.assert_not_called()
        assert result.deleted == 0
        # Mapping is preserved so the threads can be recreated/retained.
        assert len(config.get("discord_message_mapping")) == 13
        assert any("bulk-deletion guard" in e.lower() for e in result.errors)

    def test_small_removal_still_proceeds(self, config, monkeypatch):
        """Below the floor, normal churn deletes as before (no false guard)."""
        config.set("discord_message_mapping", _mapping(2))
        _patch_collect(monkeypatch, {"other": _expired_event("other")})
        discord = _make_discord()

        result = sync_engine.execute_discord_sync(config, discord)

        assert discord.delete_thread.call_count == 2
        assert result.deleted == 2
        assert config.get("discord_message_mapping") == {}
