"""Tests for coordinate_client_versions — the defer-to-newer gate and the
new-version Discord notice that pings operators of outdated clients."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from fgc_sync.services import sync_engine
from fgc_sync.services.config import Config


def _now_iso():
    return datetime.now(UTC).isoformat()


@pytest.fixture
def config(tmp_path):
    cfg = Config(path=tmp_path / "config.json")
    cfg.set("discord_weekly_mapping", {"channel_id": "weekly-thread"})
    return cfg


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch):
    monkeypatch.setattr(sync_engine, "__version__", "2.10.0")
    monkeypatch.setattr(sync_engine, "_client_character_names", lambda config: ["Me"])
    # Default: no GitHub release info (notice target falls back to our version).
    monkeypatch.setattr(sync_engine, "check_for_update", lambda: None)


def _discord(registry):
    d = MagicMock()
    d.is_configured = True
    d.read_client_registry = MagicMock(return_value=(registry, "reg-msg"))
    d.write_client_registry = MagicMock(return_value="reg-msg")
    d.version_notice_exists = MagicMock(return_value=False)
    d.post_version_notice = MagicMock(return_value="notice-msg")
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
    # Still registers itself so it remains visible/pingable as outdated.
    discord.write_client_registry.assert_called_once()
    # Does not announce when it's the outdated one.
    discord.post_version_notice.assert_not_called()


def test_newest_client_pings_outdated_operators(config):
    registry = {
        "clients": {
            "other": {"version": "2.9.1", "names": ["Alice"], "last_seen": _now_iso()}
        }
    }
    discord = _discord(registry)

    should_defer, errors = sync_engine.coordinate_client_versions(config, discord)

    assert should_defer is False
    discord.write_client_registry.assert_called_once()
    discord.post_version_notice.assert_called_once()
    args = discord.post_version_notice.call_args.args
    assert args[2] == ["Alice"]  # names to ping
    assert args[3] == "2.10.0"  # target version


def test_no_notice_when_all_current(config):
    registry = {
        "clients": {
            "other": {"version": "2.10.0", "names": ["Alice"], "last_seen": _now_iso()}
        }
    }
    discord = _discord(registry)

    sync_engine.coordinate_client_versions(config, discord)

    discord.post_version_notice.assert_not_called()


def test_notice_deduped(config):
    registry = {
        "clients": {
            "other": {"version": "2.9.1", "names": ["Alice"], "last_seen": _now_iso()}
        }
    }
    discord = _discord(registry)
    discord.version_notice_exists = MagicMock(return_value=True)

    sync_engine.coordinate_client_versions(config, discord)

    discord.post_version_notice.assert_not_called()


def test_github_release_newer_than_all_pings_everyone(config, monkeypatch):
    info = MagicMock()
    info.latest_version = "2.12.0"
    monkeypatch.setattr(sync_engine, "check_for_update", lambda: info)
    registry = {
        "clients": {
            "other": {"version": "2.9.1", "names": ["Alice"], "last_seen": _now_iso()}
        }
    }
    discord = _discord(registry)

    sync_engine.coordinate_client_versions(config, discord)

    args = discord.post_version_notice.call_args.args
    assert args[3] == "2.12.0"
    # Both the older client AND ourselves (behind 2.12.0) are pinged.
    assert set(args[2]) == {"Alice", "Me"}


def test_no_weekly_thread_is_noop(config):
    config.set("discord_weekly_mapping", {})
    discord = _discord({})

    should_defer, errors = sync_engine.coordinate_client_versions(config, discord)

    assert should_defer is False and errors == []
    discord.read_client_registry.assert_not_called()


def test_dev_version_skips(config, monkeypatch):
    monkeypatch.setattr(sync_engine, "__version__", "dev")
    discord = _discord({})

    should_defer, errors = sync_engine.coordinate_client_versions(config, discord)

    assert should_defer is False and errors == []
    discord.read_client_registry.assert_not_called()
