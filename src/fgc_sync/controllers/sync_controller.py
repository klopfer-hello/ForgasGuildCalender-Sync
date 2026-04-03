"""Manages the sync lifecycle and background threading."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThread, Signal

from fgc_sync.models import SyncPlan, SyncResult
from fgc_sync.services.config import Config
from fgc_sync.services.discord_poster import DiscordPoster
from fgc_sync.services.google_calendar import GoogleCalendarClient
from fgc_sync.services.sync_engine import compute_sync_plan, execute_discord_sync, execute_sync

log = logging.getLogger(__name__)


class _SyncWorker(QObject):
    finished = Signal(object)

    def __init__(self, config: Config, gcal: GoogleCalendarClient, discord: DiscordPoster | None = None):
        super().__init__()
        self._config = config
        self._gcal = gcal
        self._discord = discord

    def run(self):
        result = SyncResult()

        if self._config.is_google_configured and self._gcal.is_authenticated:
            try:
                gcal_result = execute_sync(self._config, self._gcal)
                result.created += gcal_result.created
                result.updated += gcal_result.updated
                result.deleted += gcal_result.deleted
                result.skipped += gcal_result.skipped
                result.errors.extend(gcal_result.errors)
            except Exception as e:
                result.errors.append(f"Google sync failed: {e}")
                log.error("Google sync failed: %s", e)

        if self._discord and self._discord.is_configured:
            try:
                discord_result = execute_discord_sync(self._config, self._discord)
                result.created += discord_result.created
                result.updated += discord_result.updated
                result.deleted += discord_result.deleted
                result.skipped += discord_result.skipped
                result.errors.extend(discord_result.errors)
            except Exception as e:
                result.errors.append(f"Discord sync failed: {e}")
                log.error("Discord sync failed: %s", e)

        self.finished.emit(result)


class SyncController(QObject):
    """Coordinates sync operations on a background thread."""

    sync_completed = Signal(object)  # SyncResult

    def __init__(self, config: Config, gcal: GoogleCalendarClient, discord: DiscordPoster | None = None, parent=None):
        super().__init__(parent)
        self._config = config
        self._gcal = gcal
        self._discord = discord
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

        # Try loading Google credentials if configured, but don't block sync
        if self._config.is_google_configured and not self._gcal.is_authenticated:
            self._gcal.load_credentials()

        self._thread = QThread()
        self._worker = _SyncWorker(self._config, self._gcal, self._discord)
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
