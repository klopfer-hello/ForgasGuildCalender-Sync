"""Tests for fgc_sync.models.sync."""

from fgc_sync.models.enums import SyncAction
from fgc_sync.models.sync import SyncPlan, SyncPlanEntry, SyncResult


def _entry(action: SyncAction, event_id: str = "e1") -> SyncPlanEntry:
    return SyncPlanEntry(
        action=action,
        event_id=event_id,
        title="Test",
        date="2026-04-10",
        time="19:45",
        event_type="raid",
    )


# --- SyncPlan ---


class TestSyncPlanFilters:
    def test_creates(self):
        plan = SyncPlan(
            entries=[
                _entry(SyncAction.CREATE, "e1"),
                _entry(SyncAction.UPDATE, "e2"),
                _entry(SyncAction.CREATE, "e3"),
            ]
        )
        assert len(plan.creates) == 2
        assert all(e.action == SyncAction.CREATE for e in plan.creates)

    def test_updates(self):
        plan = SyncPlan(
            entries=[
                _entry(SyncAction.UPDATE, "e1"),
                _entry(SyncAction.DELETE, "e2"),
            ]
        )
        assert len(plan.updates) == 1

    def test_deletes(self):
        plan = SyncPlan(
            entries=[
                _entry(SyncAction.DELETE, "e1"),
                _entry(SyncAction.DELETE, "e2"),
            ]
        )
        assert len(plan.deletes) == 2

    def test_empty_plan(self):
        plan = SyncPlan()
        assert plan.creates == []
        assert plan.updates == []
        assert plan.deletes == []
        assert plan.errors == []


# --- SyncResult ---


class TestSyncResultTotalChanges:
    def test_all_zero(self):
        r = SyncResult()
        assert r.total_changes == 0

    def test_counts_created_updated_deleted(self):
        r = SyncResult(created=2, updated=3, deleted=1, skipped=5)
        assert r.total_changes == 6

    def test_skipped_not_counted(self):
        r = SyncResult(skipped=10)
        assert r.total_changes == 0


class TestSyncResultStr:
    def test_no_changes(self):
        r = SyncResult()
        assert str(r) == "No changes"

    def test_only_created(self):
        r = SyncResult(created=3)
        assert "3 created" in str(r)

    def test_all_fields(self):
        r = SyncResult(created=1, updated=2, deleted=3, skipped=4)
        s = str(r)
        assert "1 created" in s
        assert "2 updated" in s
        assert "3 deleted" in s
        assert "4 unchanged" in s

    def test_skipped_only(self):
        r = SyncResult(skipped=5)
        assert "5 unchanged" in str(r)
