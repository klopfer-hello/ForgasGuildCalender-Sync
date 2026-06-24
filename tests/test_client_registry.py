"""Tests for the pure cross-client version-coordination logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fgc_sync.services import client_registry as cr

NOW = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)


def _entry(version, names, age_hours=0):
    seen = (NOW - timedelta(hours=age_hours)).isoformat()
    return {"version": version, "names": names, "last_seen": seen}


class TestVersionParsing:
    def test_orders_correctly(self):
        assert cr.parse_version("2.10.0") > cr.parse_version("2.9.1")
        assert cr.parse_version("2.10.0") > cr.parse_version("2.2.9")

    def test_unparseable_is_zero(self):
        assert cr.parse_version("dev") == (0,)
        assert cr.parse_version(None) == (0,)


class TestUpsertAndActive:
    def test_upsert_adds_entry(self):
        reg = cr.upsert_client({}, "Klopf", "2.10.0", ["Klopf"], NOW)
        assert reg["clients"]["Klopf"]["version"] == "2.10.0"

    def test_stale_entries_filtered(self):
        reg = {
            "clients": {
                "fresh": _entry("2.10.0", ["A"]),
                "old": _entry("2.10.0", ["B"], age_hours=48),
            }
        }
        active = cr.active_clients(reg, NOW)
        assert "fresh" in active and "old" not in active


class TestDeferDecision:
    def test_newer_client_triggers_defer(self):
        reg = {"clients": {"other": _entry("2.11.0", ["B"])}}
        assert cr.newer_client_active(reg, "2.10.0", NOW) is True

    def test_same_or_older_does_not_defer(self):
        reg = {"clients": {"other": _entry("2.9.1", ["B"])}}
        assert cr.newer_client_active(reg, "2.10.0", NOW) is False

    def test_excludes_self(self):
        reg = {"clients": {"me": _entry("2.11.0", ["A"])}}
        assert cr.newer_client_active(reg, "2.10.0", NOW, exclude_key="me") is False

    def test_stale_newer_client_ignored(self):
        reg = {"clients": {"other": _entry("2.11.0", ["B"], age_hours=48)}}
        assert cr.newer_client_active(reg, "2.10.0", NOW) is False


class TestSerialization:
    def test_roundtrip(self):
        reg = cr.upsert_client({}, "Klopf", "2.10.0", ["Klopf"], NOW)
        body = cr.serialize(reg)
        assert cr.REGISTRY_MARKER in body
        assert cr.deserialize(body) == reg

    def test_deserialize_junk(self):
        assert cr.deserialize("just a normal chat message") == {}
        assert cr.deserialize(f"{cr.REGISTRY_MARKER}\nnot json") == {}
