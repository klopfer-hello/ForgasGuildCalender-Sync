"""Data models for sync operations."""

from __future__ import annotations

from dataclasses import dataclass, field

from fgc_sync.models.enums import SyncAction


@dataclass
class SyncPlanEntry:
    action: SyncAction
    event_id: str
    title: str
    date: str
    time: str
    event_type: str
    participants_info: str = ""


@dataclass
class SyncPlan:
    entries: list[SyncPlanEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def creates(self) -> list[SyncPlanEntry]:
        return [e for e in self.entries if e.action == SyncAction.CREATE]

    @property
    def updates(self) -> list[SyncPlanEntry]:
        return [e for e in self.entries if e.action == SyncAction.UPDATE]

    @property
    def deletes(self) -> list[SyncPlanEntry]:
        return [e for e in self.entries if e.action == SyncAction.DELETE]


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_changes(self) -> int:
        return self.created + self.updated + self.deleted

    def __str__(self) -> str:
        parts = []
        if self.created:
            parts.append(f"{self.created} created")
        if self.updated:
            parts.append(f"{self.updated} updated")
        if self.deleted:
            parts.append(f"{self.deleted} deleted")
        if self.skipped:
            parts.append(f"{self.skipped} unchanged")
        return ", ".join(parts) if parts else "No changes"
