"""Manages the sync lifecycle and background threading."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThread, Signal

from fgc_sync.models import SyncPlan, SyncResult
from fgc_sync.services.config import Config
from fgc_sync.services.google_calendar import GoogleCalendarClient
from fgc_sync.services.sync_engine import compute_sync_plan, execute_sync

log = logging.getLogger(__name__)


class _SyncWorker(QObject):
    finished = Signal(object)

    def __init__(self, config: Config, gcal: GoogleCalendarClient):
        super().__init__()
        self._config = config
        self._gcal = gcal

    def run(self):
        try:
            result = execute_sync(self._config, self._gcal)
        except Exception as e:
            result = SyncResult(errors=[str(e)])
        self.finished.emit(result)


class SyncController(QObject):
    """Coordinates sync operations on a background thread."""

    sync_completed = Signal(object)  # SyncResult

    def __init__(self, config: Config, gcal: GoogleCalendarClient, parent=None):
        super().__init__(parent)
        self._config = config
        self._gcal = gcal
        self._thread: QThread | None = None
        self._worker: _SyncWorker | None = None

    @property
    def is_syncing(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def request_sync(self):
        """Start a background sync. Ignored if already syncing."""
        if self.is_syncing:
            log.info("Sync already in progress, skipping")
            return

        if not self._gcal.is_authenticated:
            if not self._gcal.load_credentials():
                self.sync_completed.emit(
                    SyncResult(errors=["Not logged in to Google"])
                )
                return

        self._thread = QThread()
        self._worker = _SyncWorker(self._config, self._gcal)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._on_thread_done)
        self._thread.start()

    def request_preview(self) -> SyncPlan:
        """Compute a sync plan, verifying events exist in Google Calendar."""
        return compute_sync_plan(self._config, self._gcal)

    def _on_finished(self, result: SyncResult):
        self.sync_completed.emit(result)

    def _on_thread_done(self):
        self._worker = None
        self._thread = None
