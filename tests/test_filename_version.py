"""The tool version embedded in image filenames must (a) be extractable by new
clients and (b) NOT break the hash/mtime parsing that older clients rely on —
the version sits before _h precisely so the suffix is unchanged.
"""

from __future__ import annotations

from fgc_sync.services.discord_poster import (
    _FILENAME_PATTERN,
    _FILENAME_VERSION_PATTERN,
    _WEEKLY_HASH_PATTERN,
)

EVENT_ID = "fgc-1781633138-305122-81"


class TestRosterFilenameParsing:
    def test_versioned_filename_yields_event_id_hash_mtime(self):
        fn = f"roster_{EVENT_ID}_v2.11.1_h1a2b3c4d_t1782300000.png"
        m = _FILENAME_PATTERN.match(fn)
        assert m is not None
        assert m.group(1) == EVENT_ID  # version excluded from the event id
        assert m.group(2) == "1a2b3c4d"
        assert m.group(3) == "1782300000"

    def test_legacy_unversioned_filename_still_parses(self):
        fn = f"roster_{EVENT_ID}_h1a2b3c4d_t1782300000.png"
        m = _FILENAME_PATTERN.match(fn)
        assert m is not None
        assert m.group(1) == EVENT_ID
        assert m.group(2) == "1a2b3c4d"
        assert m.group(3) == "1782300000"


class TestVersionExtraction:
    def test_extracts_version_from_roster_and_weekly(self):
        roster = f"roster_{EVENT_ID}_v2.11.1_h1a2b3c4d_t1782300000.png"
        weekly = "weekly_2026-W26_v2.12.0_hdeadbeef_t1782300000.png"
        assert _FILENAME_VERSION_PATTERN.search(roster).group(1) == "2.11.1"
        assert _FILENAME_VERSION_PATTERN.search(weekly).group(1) == "2.12.0"

    def test_no_version_segment_returns_none(self):
        fn = f"roster_{EVENT_ID}_h1a2b3c4d_t1782300000.png"
        assert _FILENAME_VERSION_PATTERN.search(fn) is None


class TestWeeklyHashUnaffected:
    def test_weekly_hash_parses_with_version(self):
        fn = "weekly_2026-W26_v2.11.1_hdeadbeef_t1782300000.png"
        m = _WEEKLY_HASH_PATTERN.match(fn)
        assert m is not None and m.group(1) == "deadbeef"
