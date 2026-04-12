"""Manages the sync lifecycle and background threading."""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, QThread, Signal, Slot

from fgc_sync.models import SyncPlan, SyncResult
from fgc_sync.services.config import Config
from fgc_sync.services.discord_poster import DiscordPoster
from fgc_sync.services.google_calendar import GoogleCalendarClient
from fgc_sync.services.sync_engine import (
    compute_sync_plan,
    execute_discord_sync,
    execute_sync,
)

log = logging.getLogger(__name__)


class _SyncThread(QThread):
    """Runs sync work directly in the thread — no event loop needed."""

    sync_done = Signal(object)

    def __init__(
        self,
        config: Config,
        gcal: GoogleCalendarClient,
        discord: DiscordPoster | None = None,
    ):
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

        self.sync_done.emit(result)


_SYNC_TIMEOUT = 120  # seconds before a stuck sync thread is force-reset


class SyncController(QObject):
    """Coordinates sync operations on a background thread."""

    sync_completed = Signal(object)  # SyncResult

    def __init__(
        self,
        config: Config,
        gcal: GoogleCalendarClient,
        discord: DiscordPoster | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._config = config
        self._gcal = gcal
        self._discord = discord
        self._thread: _SyncThread | None = None
        self._sync_started_at: float = 0

    @property
    def is_syncing(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    @Slot()
    def request_sync(self):
        """Start a background sync. Ignored if already syncing."""
        if self.is_syncing:
            elapsed = time.monotonic() - self._sync_started_at
            if elapsed > _SYNC_TIMEOUT:
                log.warning(
                    "Sync thread stuck for %.0fs, force-resetting",
                    elapsed,
                )
                self._force_reset()
            else:
                log.info("Sync already in progress, skipping")
                return

        # Try loading Google credentials if configured, but don't block sync
        if self._config.is_google_configured and not self._gcal.is_authenticated:
            self._gcal.load_credentials()

        self._sync_started_at = time.monotonic()
        log.debug("Starting sync thread")
        self._thread = _SyncThread(self._config, self._gcal, self._discord)
        self._thread.sync_done.connect(self._on_finished)
        self._thread.finished.connect(self._on_thread_done)
        self._thread.start()

    def request_preview(self) -> SyncPlan:
        """Compute a sync plan, verifying events exist in Google Calendar."""
        return compute_sync_plan(self._config, self._gcal)

    def _force_reset(self):
        """Force-reset a stuck sync thread so the next sync can proceed."""
        if self._thread is not None and not self._thread.wait(2000):  # 2s grace period
            log.warning("Sync thread did not finish, terminating")
            self._thread.terminate()
            self._thread.wait(1000)
        self._thread = None

    def _on_finished(self, result: SyncResult):
        elapsed = time.monotonic() - self._sync_started_at
        log.debug("Sync worker finished in %.1fs", elapsed)
        self.sync_completed.emit(result)

    def _on_thread_done(self):
        log.debug("Sync thread cleaned up")
        self._thread = None
