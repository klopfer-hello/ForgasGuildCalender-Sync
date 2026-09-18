"""The scheduled-poll gate: a cycle whose input has not changed is skipped.

Our own SavedVariables only changes when the addon rewrites the file, so
between two mtimes a poll has nothing new to publish. It still has to run
eventually for the time-driven work (24h thread expiry, week rollover,
changelog) — hence the idle interval ceiling.
"""

from __future__ import annotations

from fgc_sync.services.sync_engine import (
    IDLE_FULL_SYNC_INTERVAL_SECONDS,
    can_skip_sync_cycle,
    saved_variables_mtime,
)


class TestCanSkipSyncCycle:
    def test_unchanged_mtime_within_the_interval_skips(self):
        assert can_skip_sync_cycle(1700, 1700, 300) is True

    def test_changed_mtime_always_syncs(self):
        assert can_skip_sync_cycle(1701, 1700, 5) is False

    def test_idle_interval_forces_a_cycle_eventually(self):
        assert can_skip_sync_cycle(1700, 1700, IDLE_FULL_SYNC_INTERVAL_SECONDS) is False
        assert (
            can_skip_sync_cycle(1700, 1700, IDLE_FULL_SYNC_INTERVAL_SECONDS + 1)
            is False
        )

    def test_first_cycle_of_a_session_never_skips(self):
        assert can_skip_sync_cycle(1700, 0, 10) is False

    def test_unreadable_mtime_never_skips(self):
        assert can_skip_sync_cycle(0, 1700, 10) is False

    def test_unreadable_mtime_both_sides_never_skips(self):
        assert can_skip_sync_cycle(0, 0, 10) is False


class TestSavedVariablesMtime:
    def test_reads_whole_seconds(self, tmp_path, monkeypatch):
        sv = tmp_path / "ForgasGuildCalendar.lua"
        sv.write_text("FGC_DB = {}")
        import os

        os.utime(sv, (1789670553.75, 1789670553.75))

        class _Config:
            saved_variables_path = sv

        assert saved_variables_mtime(_Config()) == 1789670553

    def test_missing_file_is_zero(self, tmp_path):
        class _Config:
            saved_variables_path = tmp_path / "nope.lua"

        assert saved_variables_mtime(_Config()) == 0

    def test_unconfigured_path_is_zero(self):
        class _Config:
            saved_variables_path = None

        assert saved_variables_mtime(_Config()) == 0
