"""Tests for DiscordPoster.cleanup_weekly_thread_orphans."""

from unittest.mock import MagicMock

import pytest

from fgc_sync.services.discord_poster import DiscordPoster

_THREAD_ID = "1494684833199423570"


@pytest.fixture
def poster():
    p = DiscordPoster("token", "forum-1", "guild-1")
    p._request = MagicMock()
    return p


def _msg(msg_id: str, filenames: list[str]) -> dict:
    return {
        "id": msg_id,
        "attachments": [{"filename": f} for f in filenames],
    }


class TestCleanupWeeklyThreadOrphans:
    def test_starter_message_is_never_deleted(self, poster):
        """The starter's id equals the thread id and must never be deleted."""
        poster._request.side_effect = [
            # GET messages
            [_msg(_THREAD_ID, ["weekly_2026-W20_h1234abcd_t1700000000.png"])],
        ]
        removed = poster.cleanup_weekly_thread_orphans(_THREAD_ID)
        assert removed == 0
        # Only the GET was issued — no DELETE
        assert poster._request.call_count == 1

    def test_orphan_with_weekly_attachment_is_deleted(self, poster):
        orphan_id = "777"
        poster._request.side_effect = [
            [
                _msg(_THREAD_ID, ["weekly_2026-W20_h1_t1.png"]),  # starter, keep
                _msg(orphan_id, ["weekly_2026-W19_h2_t2.png"]),  # orphan, delete
            ],
            None,  # DELETE response
        ]
        removed = poster.cleanup_weekly_thread_orphans(_THREAD_ID)
        assert removed == 1
        # Second call must be the DELETE for the orphan
        delete_call = poster._request.call_args_list[1]
        assert delete_call.args[0] == "DELETE"
        assert delete_call.args[1] == f"/channels/{_THREAD_ID}/messages/{orphan_id}"

    def test_unrelated_messages_are_not_deleted(self, poster):
        """Messages without a weekly_*.png attachment must be left alone."""
        poster._request.side_effect = [
            [
                _msg("111", []),  # user reply, no attachments
                _msg("222", ["random.png"]),  # unrelated image
                _msg("333", ["roster_evt-1_h1234_t1700.png"]),  # per-event image
            ],
        ]
        removed = poster.cleanup_weekly_thread_orphans(_THREAD_ID)
        assert removed == 0
        assert poster._request.call_count == 1  # only the GET

    def test_get_failure_returns_zero(self, poster):
        import requests

        resp = MagicMock()
        resp.status_code = 500
        err = requests.HTTPError(response=resp)
        poster._request.side_effect = err
        assert poster.cleanup_weekly_thread_orphans(_THREAD_ID) == 0
