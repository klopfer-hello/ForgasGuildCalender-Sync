"""Unit tests for the 429/5xx retry loop in the Discord client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from fgc_sync.services.discord_poster import _MAX_RETRIES, DiscordPoster


def _poster():
    return DiscordPoster("token", "forum", "guild")


def _response(status, json_body=None):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    if isinstance(json_body, Exception):
        resp.json.side_effect = json_body
    else:
        resp.json.return_value = json_body if json_body is not None else {}
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status} Error")
    else:
        resp.raise_for_status.return_value = None
    return resp


class TestRetryRequest:
    def test_retries_server_error_then_succeeds(self):
        p = _poster()
        ok = _response(200)
        p._session.request = MagicMock(side_effect=[_response(503), ok])
        with patch("fgc_sync.services.discord_poster.time.sleep") as sleep:
            assert p._retry_request("GET", "http://x") is ok
        assert p._session.request.call_count == 2
        sleep.assert_called_once()

    def test_raises_after_exhausting_server_error_retries(self):
        p = _poster()
        p._session.request = MagicMock(side_effect=[_response(503)] * _MAX_RETRIES)
        with (
            patch("fgc_sync.services.discord_poster.time.sleep"),
            pytest.raises(requests.HTTPError),
        ):
            p._retry_request("GET", "http://x")
        assert p._session.request.call_count == _MAX_RETRIES

    def test_client_error_is_not_retried(self):
        p = _poster()
        p._session.request = MagicMock(return_value=_response(404))
        with pytest.raises(requests.HTTPError):
            p._retry_request("GET", "http://x")
        assert p._session.request.call_count == 1

    def test_rate_limit_honours_retry_after(self):
        p = _poster()
        ok = _response(200)
        p._session.request = MagicMock(
            side_effect=[_response(429, {"retry_after": 2.5}), ok]
        )
        with patch("fgc_sync.services.discord_poster.time.sleep") as sleep:
            assert p._retry_request("GET", "http://x") is ok
        sleep.assert_called_once_with(2.5)

    def test_rate_limit_with_non_json_body_still_retries(self):
        p = _poster()
        ok = _response(200)
        p._session.request = MagicMock(
            side_effect=[_response(429, ValueError("not json")), ok]
        )
        with patch("fgc_sync.services.discord_poster.time.sleep") as sleep:
            assert p._retry_request("GET", "http://x") is ok
        sleep.assert_called_once_with(1.0)

    def test_no_sleep_after_final_attempt(self):
        p = _poster()
        p._session.request = MagicMock(
            side_effect=[_response(429, {"retry_after": 3.0})] * _MAX_RETRIES
        )
        with (
            patch("fgc_sync.services.discord_poster.time.sleep") as sleep,
            pytest.raises(requests.HTTPError),
        ):
            p._retry_request("GET", "http://x")
        assert sleep.call_count == _MAX_RETRIES - 1
