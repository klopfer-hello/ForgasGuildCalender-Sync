"""An in-game time change must reach Discord: the roster image re-renders and
the forum thread is renamed to the new time.

Before this, ``compute_event_hash`` covered only the roster (so a time-only
edit could leave the image showing the old time) and the thread name was
written once at creation and never touched again.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from fgc_sync.models.enums import Attendance, EventType
from fgc_sync.models.events import CalendarEvent, Participant
from fgc_sync.services import sync_engine
from fgc_sync.services.config import Config
from fgc_sync.services.discord_poster import DiscordPoster, compute_event_hash


def _future_event(event_id: str = "e1", **overrides) -> CalendarEvent:
    evt = CalendarEvent(
        event_id=event_id,
        title="Gruul mit Forga",
        event_type=EventType.RAID,
        raid="gruul",
        date=(date.today() + timedelta(days=2)).isoformat(),
        server_hour=16,
        server_minute=0,
        comment="",
        creator="Forga",
        revision=1,
        participants=[
            Participant("Klopfbernd", Attendance.CONFIRMED, "WARRIOR", "tank")
        ],
    )
    return replace(evt, **overrides) if overrides else evt


@pytest.fixture
def poster() -> DiscordPoster:
    return DiscordPoster("token", "forum-1", "guild-1")


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


# -- Hash covers the header fields the image renders --


@pytest.mark.parametrize(
    "overrides",
    [
        {"server_hour": 18},
        {"server_minute": 30},
        {"date": (date.today() + timedelta(days=3)).isoformat()},
        {"title": "Gruul mit Bernd"},
        {"raid": "ssc"},
    ],
    ids=["hour", "minute", "date", "title", "raid"],
)
def test_hash_changes_when_a_rendered_header_field_changes(overrides):
    """Roster untouched — only the header changed. The image still has to be
    re-rendered, so the hash must differ."""
    before = _future_event()
    after = _future_event(**overrides)
    assert compute_event_hash(before) != compute_event_hash(after)


def test_hash_is_stable_for_an_unchanged_event():
    assert compute_event_hash(_future_event()) == compute_event_hash(_future_event())


# -- Thread rename --


def test_pending_rename_returns_new_name_after_a_time_change(poster):
    evt = _future_event(server_hour=18)
    old_name = DiscordPoster._format_thread_name(_future_event(), "en-UK")
    poster._forum_threads_cache = [{"id": "t1", "name": old_name}]

    new_name = poster.pending_thread_rename("t1", evt)

    assert new_name == DiscordPoster._format_thread_name(evt, "en-UK")
    assert "18:00" in new_name
    assert new_name != old_name


def test_pending_rename_is_none_when_the_name_already_matches(poster):
    evt = _future_event()
    poster._forum_threads_cache = [
        {"id": "t1", "name": DiscordPoster._format_thread_name(evt, "en-UK")}
    ]
    assert poster.pending_thread_rename("t1", evt) is None


def test_pending_rename_leaves_another_languages_name_alone(poster):
    """A de-DE thread must not be renamed by an en-UK client — the two would
    rename it back and forth on every cycle."""
    evt = _future_event()
    poster._forum_threads_cache = [
        {"id": "t1", "name": DiscordPoster._format_thread_name(evt, "de-DE")}
    ]
    assert poster.pending_thread_rename("t1", evt) is None


def test_pending_rename_is_none_when_the_thread_is_unknown(poster, monkeypatch):
    poster._forum_threads_cache = []
    monkeypatch.setattr(poster, "_request", lambda *a, **kw: None)
    assert poster.pending_thread_rename("gone", _future_event()) is None


def test_rename_thread_patches_the_channel_and_updates_the_cache(poster):
    resp = MagicMock(status_code=200)
    poster._session.request = MagicMock(return_value=resp)
    poster._forum_threads_cache = [{"id": "t1", "name": "old"}]

    assert poster.rename_thread("t1", "new") is True
    method, url = poster._session.request.call_args[0]
    assert method == "PATCH"
    assert url.endswith("/channels/t1")
    assert poster._session.request.call_args[1]["json"] == {"name": "new"}
    # Later lookups in the same cycle must not see the stale name.
    assert poster._forum_threads_cache[0]["name"] == "new"


def test_rename_thread_gives_up_on_rate_limit_without_retrying(poster):
    """Thread renames are limited to 2 per 10 minutes — blocking on
    ``retry_after`` would stall the whole sync cycle."""
    poster._session.request = MagicMock(return_value=MagicMock(status_code=429))

    assert poster.rename_thread("t1", "new") is False
    assert poster._session.request.call_count == 1


def test_sync_thread_name_is_a_noop_when_nothing_changed(poster):
    evt = _future_event()
    poster._forum_threads_cache = [
        {"id": "t1", "name": DiscordPoster._format_thread_name(evt, "en-UK")}
    ]
    poster._session.request = MagicMock()

    assert poster.sync_thread_name("t1", evt) is False
    poster._session.request.assert_not_called()


# -- End to end through the sync engine --


def test_time_change_renames_the_thread_and_repatches_the_image(config, monkeypatch):
    old_evt = _future_event()
    new_evt = _future_event(server_hour=18)
    _patch_collect(monkeypatch, {new_evt.event_id: new_evt})
    config.set(
        "discord_message_mapping",
        {
            new_evt.event_id: {
                "channel_id": "t1",
                # Image was rendered while the raid was still at 16:00.
                "message_ids": {"image_id": "m1", "hash": compute_event_hash(old_evt)},
                "pinged": {"Klopfbernd": "p1"},
            }
        },
    )

    discord = MagicMock()
    discord.is_configured = True
    discord.ensure_unarchived = MagicMock(return_value=True)
    discord.sync_thread_name = MagicMock(return_value=True)
    discord.message_exists = MagicMock(return_value=True)
    discord.update_event = MagicMock(
        return_value={"image_id": "m1", "hash": compute_event_hash(new_evt)}
    )
    discord.get_already_pinged_names = MagicMock(return_value={"Klopfbernd": "p1"})
    discord.ping_members = MagicMock(return_value={})
    discord.find_ics_message = MagicMock(return_value=None)
    discord.post_ics = MagicMock(return_value={"ics_id": "i1", "hash": "h"})
    discord.update_ics = MagicMock(return_value={"ics_id": "i1", "hash": "h"})

    result = sync_engine.execute_discord_sync(config, discord)

    discord.sync_thread_name.assert_called_once()
    assert discord.sync_thread_name.call_args[0][0] == "t1"
    discord.update_event.assert_called_once()  # image re-rendered in place
    discord.create_event_thread.assert_not_called()  # same thread, not a new one
    discord.ping_members.assert_not_called()  # roster unchanged → no re-ping
    assert result.errors == []
    assert result.updated == 1
    assert result.skipped == 0


def test_rename_alone_is_not_reported_as_skipped(config, monkeypatch):
    """Creator changes move the thread name without touching the image hash."""
    evt = _future_event()
    _patch_collect(monkeypatch, {evt.event_id: evt})
    config.set(
        "discord_message_mapping",
        {
            evt.event_id: {
                "channel_id": "t1",
                "message_ids": {"image_id": "m1", "hash": compute_event_hash(evt)},
                "pinged": {"Klopfbernd": "p1"},
            }
        },
    )

    discord = MagicMock()
    discord.is_configured = True
    discord.ensure_unarchived = MagicMock(return_value=True)
    discord.sync_thread_name = MagicMock(return_value=True)
    discord.get_already_pinged_names = MagicMock(return_value={"Klopfbernd": "p1"})
    discord.ping_members = MagicMock(return_value={})
    discord.find_ics_message = MagicMock(return_value=None)
    discord.post_ics = MagicMock(return_value={"ics_id": "i1", "hash": "h"})
    discord.update_ics = MagicMock(return_value={"ics_id": "i1", "hash": "h"})

    result = sync_engine.execute_discord_sync(config, discord)

    discord.update_event.assert_not_called()
    assert result.updated == 1
    assert result.skipped == 0
