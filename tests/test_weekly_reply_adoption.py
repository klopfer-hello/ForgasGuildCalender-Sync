"""Regression tests for the next-week reply ping-pong between two clients.

Each client only knows the reply id *it* posted. Before ``find_weekly_reply``
existed, a second client's reply was therefore invisible: client A posted its
own, then deleted B's as an orphan, then B's PATCH 404'd so B reposted and
deleted A's — 65 post/delete rounds over ten days in the 2.17.0 incident, with
the next-week overview absent from the forum most of the time.

The fix identifies the reply by the week key embedded in the image filename, so
every client settles on the same message. These tests pin the two properties
that break the loop: adoption (never post beside an existing reply) and
determinism (all clients choose the same one).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from fgc_sync.models.enums import Attendance, EventType
from fgc_sync.models.events import CalendarEvent, Participant
from fgc_sync.services import sync_engine
from fgc_sync.services.config import Config
from fgc_sync.services.discord_poster import (
    _PING_HISTORY_SCAN_LIMIT,
    DiscordPoster,
)
from fgc_sync.services.weekly_overview import EMPTY_WEEK_HASH

_THREAD_ID = "1494684833199423570"
# Ids as Discord orders them: lower == posted earlier.
_OLDER_REPLY = "1552028041948037190"
_NEWER_REPLY = "1552034680889671690"


def _msg(msg_id: str, filenames: list[str]) -> dict:
    return {"id": msg_id, "attachments": [{"filename": f} for f in filenames]}


@pytest.fixture
def poster():
    p = DiscordPoster("token", "forum-1", "guild-1")
    p._request = MagicMock()
    return p


class TestFindWeeklyReply:
    def test_starter_is_never_adopted_as_the_reply(self, poster):
        """The starter holds the *current* week; adopting it would make the
        sync PATCH next week's image over the current-week overview."""
        poster._request.return_value = [
            _msg(_THREAD_ID, ["weekly_2026-W40_v2.17.0_hdeadbeef_t1700000000.png"])
        ]
        assert poster.find_weekly_reply(_THREAD_ID, "2026-W40") is None

    def test_adopts_another_clients_reply_for_the_target_week(self, poster):
        poster._request.return_value = [
            _msg(_THREAD_ID, ["weekly_2026-W39_v2.17.0_haaaa1111_t1700000000.png"]),
            _msg(_NEWER_REPLY, ["weekly_2026-W40_v2.17.0_hbbbb2222_t1700000000.png"]),
        ]
        assert poster.find_weekly_reply(_THREAD_ID, "2026-W40") == (
            _NEWER_REPLY,
            "2026-W40",
            "bbbb2222",
        )

    def test_duplicate_replies_collapse_onto_the_oldest(self, poster):
        """Two clients raced and both posted. Whichever client scans, it must
        pick the same survivor — otherwise they keep deleting each other's."""
        messages = [
            _msg(_THREAD_ID, ["weekly_2026-W39_haaaa1111_t1.png"]),
            _msg(_NEWER_REPLY, ["weekly_2026-W40_hbbbb2222_t1.png"]),
            _msg(_OLDER_REPLY, ["weekly_2026-W40_hcccc3333_t1.png"]),
        ]
        poster._request.return_value = messages
        first = poster.find_weekly_reply(_THREAD_ID, "2026-W40")

        # Same thread, opposite listing order (Discord makes no ordering promise).
        poster._request.return_value = list(reversed(messages))
        second = poster.find_weekly_reply(_THREAD_ID, "2026-W40")

        assert first == second == (_OLDER_REPLY, "2026-W40", "cccc3333")

    def test_falls_back_to_oldest_reply_on_week_rollover(self, poster):
        """At rollover no reply shows the new target week yet. The reply slot is
        PATCHed across weeks by design, so return it rather than posting anew."""
        poster._request.return_value = [
            _msg(_THREAD_ID, ["weekly_2026-W40_haaaa1111_t1.png"]),
            _msg(_NEWER_REPLY, ["weekly_2026-W40_hbbbb2222_t1.png"]),
            _msg(_OLDER_REPLY, ["weekly_2026-W40_hcccc3333_t1.png"]),
        ]
        assert poster.find_weekly_reply(_THREAD_ID, "2026-W41") == (
            _OLDER_REPLY,
            "2026-W40",
            "cccc3333",
        )

    def test_legacy_filename_without_version_segment_is_adopted(self, poster):
        """Replies written before the _v segment must not look unrecognisable."""
        poster._request.return_value = [
            _msg(_THREAD_ID, ["weekly_2026-W39_haaaa1111_t1.png"]),
            _msg(_OLDER_REPLY, ["weekly_2026-W40_hbbbb2222_t1700000000.png"]),
        ]
        assert poster.find_weekly_reply(_THREAD_ID, "2026-W40") == (
            _OLDER_REPLY,
            "2026-W40",
            "bbbb2222",
        )

    def test_non_weekly_messages_are_ignored(self, poster):
        """Chat and roster images in the thread are not the weekly reply."""
        poster._request.return_value = [
            _msg(_THREAD_ID, ["weekly_2026-W39_haaaa1111_t1.png"]),
            _msg("111", []),
            _msg("222", ["roster_fgc-1-x_v2.17.0_hbbbb2222_t1.png"]),
        ]
        assert poster.find_weekly_reply(_THREAD_ID, "2026-W40") is None

    def test_scan_limit_matches_the_orphan_cleanup(self, poster):
        """A reply the cleanup can see but this scan cannot would be deleted
        immediately after we posted a duplicate — the loop, re-armed."""
        poster._request.return_value = []
        poster.find_weekly_reply(_THREAD_ID, "2026-W40")

        assert poster._request.call_args.kwargs["params"] == {
            "limit": _PING_HISTORY_SCAN_LIMIT
        }

    def test_failed_listing_raises_instead_of_reporting_no_reply(self, poster):
        """``None`` means "post a new reply". A failed request must not say
        that, or every 429/5xx on this GET would post a duplicate."""
        poster._request.side_effect = requests.HTTPError("boom")
        with pytest.raises(requests.HTTPError):
            poster.find_weekly_reply(_THREAD_ID, "2026-W40")


# --- Whole-sync behaviour ---


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


@pytest.fixture
def patched_collect(monkeypatch):
    evt = _evt()
    monkeypatch.setattr(
        sync_engine,
        "_load_events_for_overview",
        lambda config: ({evt.event_id: evt}, []),
    )
    monkeypatch.setattr(
        sync_engine, "_is_local_data_stale", lambda config, discord: False
    )


def _discord(reply):
    d = MagicMock()
    d.is_configured = True
    d.clear_thread_cache = MagicMock()
    d.find_thread_by_name = MagicMock(return_value=_THREAD_ID)
    d.ensure_unarchived = MagicMock(return_value=True)
    d.message_exists = MagicMock(return_value=True)
    d.find_weekly_reply = MagicMock(return_value=reply)
    # Next week is eventless in this fixture, so the empty-week guard would
    # otherwise mask the adoption behaviour under test. It has its own file.
    d.get_weekly_image_hash = MagicMock(return_value=EMPTY_WEEK_HASH)
    d.update_weekly_image = MagicMock()
    d.post_weekly_image = MagicMock(return_value="brand-new-reply")
    d.cleanup_weekly_thread_orphans = MagicMock(return_value=0)
    return d


class TestSyncAdoptsInsteadOfDuplicating:
    def test_empty_mapping_adopts_the_other_clients_reply(
        self, config, patched_collect
    ):
        """The exact 2.17.0 loop: our mapping knows no reply, but one exists.
        Posting here is what started the delete-and-repost cycle."""
        discord = _discord(reply=(_OLDER_REPLY, "1999-W01", "stale"))

        result = sync_engine.execute_weekly_sync(config, discord)

        assert result.errors == []
        discord.post_weekly_image.assert_not_called()
        # PATCHed in place, at the adopted id
        assert discord.update_weekly_image.call_args.args[1] == _OLDER_REPLY
        assert config.get("discord_weekly_mapping")["next_message_id"] == _OLDER_REPLY

    def test_adopted_reply_is_protected_from_the_orphan_sweep(
        self, config, patched_collect
    ):
        """Half the loop was the cleanup deleting the other client's reply
        because keep_ids only ever held our own id."""
        discord = _discord(reply=(_OLDER_REPLY, "1999-W01", "stale"))

        sync_engine.execute_weekly_sync(config, discord)

        discord.cleanup_weekly_thread_orphans.assert_called_once_with(
            _THREAD_ID, keep_ids={_OLDER_REPLY}
        )

    def test_stale_local_reply_id_does_not_trigger_a_repost(
        self, config, patched_collect
    ):
        """Our stored id was deleted by the other client, but its replacement
        is right there. Trusting the mapping would post a third message."""
        config.set(
            "discord_weekly_mapping",
            {
                "channel_id": _THREAD_ID,
                "message_id": _THREAD_ID,
                "hash": "x",
                "week_key": "1999-W01",
                "next_message_id": "deleted-by-the-other-client",
                "next_hash": "x",
                "next_week_key": "1999-W01",
                "sv_mtime": 0,
            },
        )
        discord = _discord(reply=(_NEWER_REPLY, "1999-W01", "stale"))

        sync_engine.execute_weekly_sync(config, discord)

        discord.post_weekly_image.assert_not_called()
        assert config.get("discord_weekly_mapping")["next_message_id"] == _NEWER_REPLY

    def test_posts_only_when_the_thread_truly_has_no_reply(
        self, config, patched_collect
    ):
        discord = _discord(reply=None)

        result = sync_engine.execute_weekly_sync(config, discord)

        discord.post_weekly_image.assert_called_once()
        assert result.created == 1
        assert config.get("discord_weekly_mapping")["next_message_id"] == (
            "brand-new-reply"
        )

    def test_two_clients_converge_on_one_reply(self, config, tmp_path, patched_collect):
        """The property that ends the ping-pong: clients with different local
        mappings must end the cycle pointing at the same message."""
        other = Config(path=tmp_path / "other.json")
        other.set("wow_path", str(tmp_path / "wow"))
        other.set("account_folder", "acc")
        other.set("guild_key", "Guild")
        for cfg, own_id in ((config, _OLDER_REPLY), (other, _NEWER_REPLY)):
            cfg.set(
                "discord_weekly_mapping",
                {
                    "channel_id": _THREAD_ID,
                    "message_id": _THREAD_ID,
                    "hash": "x",
                    "week_key": "1999-W01",
                    "next_message_id": own_id,
                    "next_hash": "x",
                    "next_week_key": "1999-W01",
                    "sv_mtime": 0,
                },
            )
            # Both scan the same thread and so see the same survivor.
            sync_engine.execute_weekly_sync(
                cfg, _discord(reply=(_OLDER_REPLY, "1999-W01", "stale"))
            )

        assert (
            config.get("discord_weekly_mapping")["next_message_id"]
            == other.get("discord_weekly_mapping")["next_message_id"]
            == _OLDER_REPLY
        )


class TestFailedScan:
    def test_sync_aborts_instead_of_posting_a_duplicate(self, config, patched_collect):
        """A failed scan tells us nothing about whether a reply exists; the
        cycle must stop rather than guess "none" and post."""
        discord = _discord(reply=None)
        discord.find_weekly_reply.side_effect = requests.HTTPError("429")

        result = sync_engine.execute_weekly_sync(config, discord)

        discord.post_weekly_image.assert_not_called()
        discord.cleanup_weekly_thread_orphans.assert_not_called()
        assert result.errors

    def test_plan_reports_nothing_rather_than_a_create(self, config, patched_collect):
        """The dry-run must not abort, and must not promise a CREATE the real
        sync would never perform."""
        config.set("discord_weekly_mapping", {"channel_id": _THREAD_ID})
        discord = _discord(reply=None)
        discord.find_weekly_reply.side_effect = requests.HTTPError("429")

        plan = sync_engine.compute_weekly_sync_plan(config, discord)

        next_week = [e for e in plan.entries if e.event_type == "Overview (next week)"]
        assert next_week == []
