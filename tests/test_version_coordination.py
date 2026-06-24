"""Tests for coordinate_client_versions — the defer-to-newer gate and the
release changelog posted to the dedicated updates thread (pinging config users).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from fgc_sync.services import sync_engine
from fgc_sync.services.config import Config


def _now_iso():
    return datetime.now(UTC).isoformat()


def _update_info(version="2.11.0", notes="- fixed stuff"):
    info = MagicMock()
    info.latest_version = version
    info.release_notes = notes
    return info


@pytest.fixture
def config(tmp_path):
    cfg = Config(path=tmp_path / "config.json")
    cfg.set("discord_weekly_mapping", {"channel_id": "weekly-thread"})
    return cfg


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch):
    monkeypatch.setattr(sync_engine, "__version__", "2.10.0")
    monkeypatch.setattr(sync_engine, "_client_character_names", lambda config: ["Me"])
    monkeypatch.setattr(sync_engine, "check_for_update", lambda: _update_info())


def _discord(registry):
    d = MagicMock()
    d.is_configured = True
    d.read_client_registry = MagicMock(return_value=(registry, "reg-msg"))
    d.write_client_registry = MagicMock(return_value="reg-msg")
    d.clear_thread_cache = MagicMock()
    d.ensure_unarchived = MagicMock(return_value=True)
    d.find_thread_by_name = MagicMock(return_value=None)
    d.changelog_exists = MagicMock(return_value=False)
    d.create_changelog_thread = MagicMock(return_value="updates-thread")
    d.post_changelog = MagicMock(return_value="changelog-msg")
    return d


def test_defers_when_newer_client_active(config):
    registry = {
        "clients": {
            "other": {"version": "2.11.0", "names": ["Alice"], "last_seen": _now_iso()}
        }
    }
    discord = _discord(registry)

    should_defer, errors = sync_engine.coordinate_client_versions(config, discord)

    assert should_defer is True
    assert errors == []
    discord.write_client_registry.assert_called_once()  # still registers itself
    # A deferring client does NOT post the changelog (newer client handles it).
    discord.create_changelog_thread.assert_not_called()
    discord.post_changelog.assert_not_called()


def test_creates_updates_thread_with_changelog_and_config_pings(config):
    config.set("discord_update_ping_user_ids", ["111", "222"])
    discord = _discord({})

    should_defer, errors = sync_engine.coordinate_client_versions(config, discord)

    assert should_defer is False
    discord.create_changelog_thread.assert_called_once()
    name, body, ping_ids, version = discord.create_changelog_thread.call_args.args
    assert version == "2.11.0"
    assert "2.11.0" in body and "fixed stuff" in body
    assert ping_ids == ["111", "222"]
    # Thread id persisted for next time.
    assert config.get("discord_updates_thread_id") == "updates-thread"


def test_posts_reply_when_thread_exists(config):
    config.set("discord_updates_thread_id", "updates-thread")
    discord = _discord({})

    sync_engine.coordinate_client_versions(config, discord)

    discord.post_changelog.assert_called_once()
    discord.create_changelog_thread.assert_not_called()


def test_changelog_deduped_per_version(config):
    config.set("discord_updates_thread_id", "updates-thread")
    discord = _discord({})
    discord.changelog_exists = MagicMock(return_value=True)

    sync_engine.coordinate_client_versions(config, discord)

    discord.post_changelog.assert_not_called()
    discord.create_changelog_thread.assert_not_called()


def test_self_heals_deleted_updates_thread(config):
    config.set("discord_updates_thread_id", "dead-thread")
    discord = _discord({})
    discord.ensure_unarchived = MagicMock(return_value=False)  # 404 → recreate

    sync_engine.coordinate_client_versions(config, discord)

    discord.create_changelog_thread.assert_called_once()


def test_changelog_works_without_weekly_thread(config):
    config.set("discord_weekly_mapping", {})  # no registry home
    discord = _discord({})

    should_defer, errors = sync_engine.coordinate_client_versions(config, discord)

    assert should_defer is False
    discord.read_client_registry.assert_not_called()  # registry skipped
    discord.create_changelog_thread.assert_called_once()  # changelog independent


def test_no_changelog_when_no_release_info(config, monkeypatch):
    monkeypatch.setattr(sync_engine, "check_for_update", lambda: None)
    discord = _discord({})

    sync_engine.coordinate_client_versions(config, discord)

    discord.create_changelog_thread.assert_not_called()
    discord.post_changelog.assert_not_called()


def test_dev_version_skips(config, monkeypatch):
    monkeypatch.setattr(sync_engine, "__version__", "dev")
    discord = _discord({})

    should_defer, errors = sync_engine.coordinate_client_versions(config, discord)

    assert should_defer is False and errors == []
    discord.read_client_registry.assert_not_called()
    discord.create_changelog_thread.assert_not_called()
