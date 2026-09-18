"""The filename scanners share one per-cycle listing per thread.

The stale-data guard and the version gate both walk every forum thread, and
the guard runs twice per cycle (per-event sync + weekly overview). Without the
cache that is three identical GETs per thread, every five minutes.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from fgc_sync.services.discord_poster import DiscordPoster


def _response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


def _poster(threads, messages):
    """A poster whose HTTP layer is stubbed at the socket end, so the real
    ``_request`` / ``_retry_request`` (and their cache handling) still run."""
    poster = DiscordPoster("token", "forum", "guild")
    poster._get_forum_threads = MagicMock(return_value=[{"id": t} for t in threads])
    calls = []

    def _send(method, url, **kwargs):
        calls.append((method, url))
        return _response(messages)

    poster._session = MagicMock()
    poster._session.request = _send
    return poster, calls


def _gets(calls):
    return [c for c in calls if c[0] == "GET"]


def _image(filename):
    return [{"id": "m1", "attachments": [{"filename": filename}]}]


class TestRecentMessageCache:
    def test_repeated_scans_hit_the_api_once_per_thread(self):
        poster, calls = _poster(["t1", "t2"], _image("roster_e_v2.16.1_habc_t99.png"))
        assert poster.get_max_remote_sv_mtime() == 99
        assert poster.get_max_remote_version() == "2.16.1"
        assert poster.get_max_remote_sv_mtime() == 99
        assert len(_gets(calls)) == 2  # one per thread, not per scan

    def test_a_read_does_not_invalidate_the_cache(self):
        poster, calls = _poster(["t1"], _image("roster_e_habc_t99.png"))
        poster.get_max_remote_sv_mtime()
        poster.get_max_remote_sv_mtime()
        assert len(_gets(calls)) == 1

    def test_a_write_invalidates_the_cache(self):
        poster, calls = _poster(["t1"], _image("roster_e_habc_t99.png"))
        poster.get_max_remote_sv_mtime()
        poster._request("POST", "/channels/t1/messages", json={})
        poster.get_max_remote_sv_mtime()
        assert len(_gets(calls)) == 2

    def test_clear_thread_cache_drops_it(self):
        poster, calls = _poster(["t1"], _image("roster_e_habc_t99.png"))
        poster.get_max_remote_sv_mtime()
        poster.clear_thread_cache()
        poster.get_max_remote_sv_mtime()
        assert len(_gets(calls)) == 2

    def test_a_failed_listing_is_not_cached(self):
        poster = DiscordPoster("token", "forum", "guild")
        poster._get_forum_threads = MagicMock(return_value=[{"id": "t1"}])
        attempts = []

        def _request(method, path, **kwargs):
            attempts.append(path)
            raise requests.HTTPError("boom")

        poster._request = _request
        with pytest.raises(requests.HTTPError):
            poster._get_recent_messages("t1")
        with pytest.raises(requests.HTTPError):
            poster._get_recent_messages("t1")
        assert len(attempts) == 2
