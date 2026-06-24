"""Tests for coordinate_client_versions — the defer-to-newer gate (read from the
version embedded in forum image filenames) and the release changelog posted to
the dedicated updates thread (pinging config-provided users)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from fgc_sync.services import sync_engine
from fgc_sync.services.config import Config


def _update_info(version="2.11.1", notes="- fixed stuff"):
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
    monkeypatch.setattr(sync_engine, "__version__", "2.11.1")
    monkeypatch.setattr(sync_engine, "check_for_update", lambda: _update_info())


def _discord(max_remote_version=None):
    d = MagicMock()
    d.is_configured = True
    d.delete_registry_messages = MagicMock(return_value=0)
    d.get_max_remote_version = MagicMock(return_value=max_remote_version)
    d.clear_thread_cache = MagicMock()
    d.ensure_unarchived = MagicMock(return_value=True)
    d.find_thread_by_name = MagicMock(return_value=None)
    d.changelog_exists = MagicMock(return_value=False)
    d.create_changelog_thread = MagicMock(return_value="updates-thread")
    d.post_changelog = MagicMock(return_value="changelog-msg")
    return d


def test_defers_when_newer_version_in_filenames(config):
    discord = _discord(max_remote_version="2.12.0")  # newer client posted images

    should_defer, errors = sync_engine.coordinate_client_versions(config, discord)

    assert should_defer is True
    assert errors == []
    # A deferring client does not announce the changelog.
    discord.create_changelog_thread.assert_not_called()
    discord.post_changelog.assert_not_called()


def test_no_defer_when_remote_not_newer(config):
    discord = _discord(max_remote_version="2.11.0")

    should_defer, _ = sync_engine.coordinate_client_versions(config, discord)

    assert should_defer is False


def test_cleans_up_leftover_registry_message(config):
    discord = _discord()

    sync_engine.coordinate_client_versions(config, discord)

    discord.delete_registry_messages.assert_called_once_with("weekly-thread")


def test_creates_updates_thread_with_changelog_and_config_pings(config):
    config.set("discord_update_ping_user_ids", ["111", "222"])
    discord = _discord()

    should_defer, _ = sync_engine.coordinate_client_versions(config, discord)

    assert should_defer is False
    discord.create_changelog_thread.assert_called_once()
    name, body, ping_ids, version = discord.create_changelog_thread.call_args.args
    assert version == "2.11.1"
    assert "2.11.1" in body and "fixed stuff" in body
    assert ping_ids == ["111", "222"]
    assert config.get("discord_updates_thread_id") == "updates-thread"


def test_posts_reply_when_thread_exists(config):
    config.set("discord_updates_thread_id", "updates-thread")
    discord = _discord()

    sync_engine.coordinate_client_versions(config, discord)

    discord.post_changelog.assert_called_once()
    discord.create_changelog_thread.assert_not_called()


def test_changelog_deduped_per_version(config):
    config.set("discord_updates_thread_id", "updates-thread")
    discord = _discord()
    discord.changelog_exists = MagicMock(return_value=True)

    sync_engine.coordinate_client_versions(config, discord)

    discord.post_changelog.assert_not_called()
    discord.create_changelog_thread.assert_not_called()


def test_self_heals_deleted_updates_thread(config):
    config.set("discord_updates_thread_id", "dead-thread")
    discord = _discord()
    discord.ensure_unarchived = MagicMock(return_value=False)  # 404 → recreate

    sync_engine.coordinate_client_versions(config, discord)

    discord.create_changelog_thread.assert_called_once()


def test_no_changelog_when_no_release_info(config, monkeypatch):
    monkeypatch.setattr(sync_engine, "check_for_update", lambda: None)
    discord = _discord()

    sync_engine.coordinate_client_versions(config, discord)

    discord.create_changelog_thread.assert_not_called()
    discord.post_changelog.assert_not_called()


def test_dev_version_skips_everything(config, monkeypatch):
    monkeypatch.setattr(sync_engine, "__version__", "dev")
    discord = _discord(max_remote_version="2.12.0")

    should_defer, errors = sync_engine.coordinate_client_versions(config, discord)

    assert should_defer is False and errors == []
    discord.get_max_remote_version.assert_not_called()
    discord.delete_registry_messages.assert_not_called()
    discord.create_changelog_thread.assert_not_called()
