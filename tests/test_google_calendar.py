"""Tests for the GoogleCalendarClient event-body builder."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fgc_sync.services.google_calendar import GoogleCalendarClient

_START = datetime(2026, 4, 10, 20, 0, tzinfo=ZoneInfo("Europe/Berlin"))


class TestBuildEventBody:
    def test_default_status_is_confirmed_and_busy(self):
        body = GoogleCalendarClient._build_event_body(
            "Raid", _START, 3, "desc", "Gruul"
        )
        assert body["status"] == "confirmed"
        assert body["transparency"] == "opaque"

    def test_tentative_flag_sets_tentative_and_free(self):
        body = GoogleCalendarClient._build_event_body(
            "Raid", _START, 3, "desc", "Gruul", tentative=True
        )
        assert body["status"] == "tentative"
        assert body["transparency"] == "transparent"

    def test_tentative_false_sets_confirmed_and_busy(self):
        body = GoogleCalendarClient._build_event_body(
            "Raid", _START, 3, "desc", "Gruul", tentative=False
        )
        assert body["status"] == "confirmed"
        assert body["transparency"] == "opaque"

    def test_core_fields_present(self):
        body = GoogleCalendarClient._build_event_body(
            "Raid", _START, 3, "desc", "Gruul"
        )
        assert body["summary"] == "Raid"
        assert body["description"] == "desc"
        assert body["location"] == "Gruul"
        assert body["start"]["dateTime"] == _START.isoformat()
