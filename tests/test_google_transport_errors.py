"""A dropped connection must never be mistaken for a deleted calendar event.

The httplib2 connection behind the cached service sits idle between poll
cycles, so the first request of a cycle can fail on a socket the peer closed
in the meantime. ``event_exists`` returning False for that would make the sync
engine re-create an event that is alive and well — once every cycle.
"""

from __future__ import annotations

import httplib2
import pytest
from googleapiclient.errors import HttpError

from fgc_sync.services.google_calendar import GoogleCalendarClient


def _client() -> GoogleCalendarClient:
    from pathlib import Path

    client = GoogleCalendarClient(Path("token.json"), Path("secrets.json"))
    client._service = object()  # non-None so _get_service is never built
    return client


def _http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b"{}", uri="/events")


class _Request:
    """Stand-in for a googleapiclient request object."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def execute(self):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _raises(exc):
    """An ``_execute`` stand-in that always fails with *exc*."""

    def _execute(build_request, **kwargs):
        raise exc

    return _execute


class TestEventExists:
    def test_404_returns_false(self):
        client = _client()
        client._execute = _raises(_http_error(404))
        assert client.event_exists("cal", "evt") is False

    def test_410_returns_false(self):
        client = _client()
        client._execute = _raises(_http_error(410))
        assert client.event_exists("cal", "evt") is False

    def test_cancelled_event_returns_false(self):
        client = _client()
        client._execute = lambda build, **kw: {"status": "cancelled"}
        assert client.event_exists("cal", "evt") is False

    def test_live_event_returns_true(self):
        client = _client()
        client._execute = lambda build, **kw: {"status": "confirmed"}
        assert client.event_exists("cal", "evt") is True

    def test_connection_error_is_raised_not_swallowed(self):
        client = _client()
        client._execute = _raises(ConnectionAbortedError(10053, "aborted"))
        with pytest.raises(ConnectionAbortedError):
            client.event_exists("cal", "evt")

    def test_server_error_is_raised_not_swallowed(self):
        client = _client()
        client._execute = _raises(_http_error(500))
        with pytest.raises(HttpError):
            client.event_exists("cal", "evt")


class TestExecuteRetry:
    def test_retries_once_after_a_dropped_connection(self):
        client = _client()
        request = _Request([ConnectionResetError(10054, "reset"), {"id": "x"}])
        assert client._execute(lambda: request) == {"id": "x"}
        assert request.calls == 2

    def test_drops_the_cached_service_so_the_retry_reconnects(self):
        client = _client()
        request = _Request([ConnectionResetError(10054, "reset"), {"id": "x"}])
        client._execute(lambda: request)
        assert client._service is None

    def test_a_second_failure_is_raised(self):
        client = _client()
        request = _Request(
            [ConnectionResetError(10054, "reset"), ConnectionResetError(10054, "reset")]
        )
        with pytest.raises(ConnectionResetError):
            client._execute(lambda: request)
        assert request.calls == 2

    def test_http_errors_are_not_retried(self):
        client = _client()
        request = _Request([_http_error(404), {"id": "x"}])
        with pytest.raises(HttpError):
            client._execute(lambda: request)
        assert request.calls == 1

    def test_non_idempotent_call_is_not_repeated(self):
        client = _client()
        request = _Request([ConnectionResetError(10054, "reset"), {"id": "x"}])
        with pytest.raises(ConnectionResetError):
            client._execute(lambda: request, retry=False)
        assert request.calls == 1
        # still reconnects next time — the dead socket is not kept
        assert client._service is None
