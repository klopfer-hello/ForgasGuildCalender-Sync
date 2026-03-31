"""Application controller — wires views, services, and sync together."""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from fgc_sync.controllers.sync_controller import SyncController
from fgc_sync.models import SyncResult
from fgc_sync.services.config import Config
from fgc_sync.services.file_watcher import FileWatcher
from fgc_sync.services.google_calendar import GoogleCalendarClient
from fgc_sync.views.preview_dialog import PreviewDialog
from fgc_sync.views.settings_dialog import SettingsDialog
from fgc_sync.views.setup_wizard import SetupWizard
from fgc_sync.views.tray_icon import TrayIcon, create_default_icon

log = logging.getLogger(__name__)


class AppController:
    """Orchestrates the application lifecycle."""

    def __init__(self, config: Config):
        self._config = config
        self._gcal = GoogleCalendarClient(
            config.token_path, config.client_secrets_path
        )
        self._sync = SyncController(config, self._gcal)
        self._tray = TrayIcon()
        self._watcher: FileWatcher | None = None
        self._poll_timer: QTimer | None = None

    def start(self):
        self._gcal.load_credentials()

        self._tray.set_icon(create_default_icon())
        self._tray.sync_requested.connect(self._sync.request_sync)
        self._tray.preview_requested.connect(self._show_preview)
        self._tray.settings_requested.connect(self._show_settings)
        self._tray.quit_requested.connect(self._quit)
        self._sync.sync_completed.connect(self._on_sync_done)
        self._tray.show()

        if not self._config.is_setup_complete:
            self._run_setup()
        else:
            self._start_watcher()
            self._start_poll_timer()
            self._sync.request_sync()

    def _start_poll_timer(self):
        """Poll every 5 minutes as a fallback alongside the file watcher."""
        if self._poll_timer:
            self._poll_timer.stop()
        self._poll_timer = QTimer()
        self._poll_timer.timeout.connect(self._sync.request_sync)
        self._poll_timer.start(5 * 60 * 1000)
        log.info("Poll timer started (every 5 minutes)")

    def _run_setup(self):
        wizard = SetupWizard(self._config, self._gcal)
        if wizard.exec():
            self._start_watcher()
            self._start_poll_timer()
            self._sync.request_sync()
        elif not self._config.is_setup_complete:
            self._tray.show_notification(
                "Setup Incomplete",
                "Right-click the tray icon and open Settings to finish setup.",
            )

    def _start_watcher(self):
        sv_path = self._config.saved_variables_path
        if not sv_path:
            return
        sv_dir = sv_path.parent
        if not sv_dir.is_dir():
            log.warning("SavedVariables directory not found: %s", sv_dir)
            return
        if self._watcher:
            self._watcher.stop()
        self._watcher = FileWatcher(sv_dir, self._sync.request_sync)
        self._watcher.start()

    def _show_preview(self):
        plan = self._sync.request_preview()
        dialog = PreviewDialog(plan)
        if dialog.exec():
            self._sync.request_sync()

    def _show_settings(self):
        dialog = SettingsDialog(self._config, self._gcal)
        if dialog.exec():
            self._start_watcher()
            self._sync.request_sync()

    def _on_sync_done(self, result: SyncResult):
        status = str(result)
        self._tray.update_status(status)

        if result.errors:
            self._tray.show_notification(
                "Sync Error", "\n".join(result.errors[:3])
            )
            log.error("Sync errors: %s", result.errors)
        elif result.total_changes > 0:
            self._tray.show_notification("Calendar Synced", status)

        log.info("Sync complete: %s", status)

    def _quit(self):
        if self._poll_timer:
            self._poll_timer.stop()
        if self._watcher:
            self._watcher.stop()
        self._tray.hide()
        QApplication.instance().quit()
