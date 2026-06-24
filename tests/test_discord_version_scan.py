"""Unit tests for the filename-embedded version scan that drives the
defer-to-newer gate (get_max_remote_version) and the duplicate finder
(find_event_threads)."""

from __future__ import annotations

from unittest.mock import MagicMock

from fgc_sync.models.enums import EventType
from fgc_sync.models.events import CalendarEvent
from fgc_sync.services.discord_poster import DiscordPoster


def _poster():
    return DiscordPoster("token", "forum", "guild")


def _messages(*filenames):
    return [{"id": "m", "attachments": [{"filename": f} for f in filenames]}]


class TestGetMaxRemoteVersion:
    def test_returns_highest_version_across_threads(self):
        p = _poster()
        p._get_forum_threads = MagicMock(return_value=[{"id": "1"}, {"id": "2"}])
        data = {
            "1": _messages("roster_e1_v2.10.0_habcdef0_t1.png"),
            "2": _messages(
                "roster_e2_v2.11.1_hbeef0001_t2.png",
                "weekly_2026-W26_v2.9.1_hcafe0002_t3.png",
            ),
        }
        p._request = lambda method, path, **kw: data[path.split("/")[2]]
        assert p.get_max_remote_version() == "2.11.1"

    def test_returns_none_when_no_versioned_image(self):
        p = _poster()
        p._get_forum_threads = MagicMock(return_value=[{"id": "1"}])
        p._request = lambda method, path, **kw: _messages(
            "roster_e1_habcdef0_t1.png"  # legacy, no _v
        )
        assert p.get_max_remote_version() is None

    def test_skips_unreadable_threads(self):
        import requests

        p = _poster()
        p._get_forum_threads = MagicMock(return_value=[{"id": "1"}, {"id": "2"}])

        def req(method, path, **kw):
            if path.split("/")[2] == "1":
                raise requests.HTTPError("boom")
            return _messages("roster_e2_v2.11.2_hbeef0001_t2.png")

        p._request = req
        assert p.get_max_remote_version() == "2.11.2"


class TestFindEventThreads:
    def _event(self):
        return CalendarEvent(
            event_id="evt-1",
            title="Kara",
            event_type=EventType.RAID,
            raid="karazhan",
            date="2026-06-30",
            server_hour=20,
            server_minute=0,
            comment="",
            creator="Muckli",
            revision=1,
            participants=[],
        )

    def test_collects_threads_by_image_with_versions(self):
        p = _poster()
        p._candidate_thread_names = MagicMock(return_value=["irrelevant-name"])
        p._get_forum_threads = MagicMock(
            return_value=[{"id": "100", "name": "x"}, {"id": "999", "name": "y"}]
        )
        data = {
            "100": _messages("roster_evt-1_v2.11.1_haaaa0001_t1.png"),
            "999": _messages("roster_evt-1_hbbbb0002_t2.png"),  # no version
        }
        p._request = lambda method, path, **kw: data[path.split("/")[2]]

        out = {t["channel_id"]: t for t in p.find_event_threads(self._event())}
        assert set(out) == {"100", "999"}
        assert out["100"]["version"] == "2.11.1"
        assert out["999"]["version"] is None
