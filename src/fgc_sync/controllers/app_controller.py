"""Application controller — wires views, services, and sync together."""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from fgc_sync._version import APP_NAME, about_text
from fgc_sync.controllers.sync_controller import SyncController
from fgc_sync.models import SyncResult, UpdateInfo
from fgc_sync.services.config import Config
from fgc_sync.services.discord_poster import DiscordPoster
from fgc_sync.services.file_watcher import FileWatcher
from fgc_sync.services.google_calendar import GoogleCalendarClient
from fgc_sync.views.preview_dialog import PreviewDialog
from fgc_sync.views.settings_dialog import SettingsDialog
from fgc_sync.views.setup_wizard import SetupWizard
from fgc_sync.views.tray_icon import TrayIcon, create_default_icon

log = logging.getLogger(__name__)

_UPDATE_CHECK_INTERVAL = 6 * 60 * 60 * 1000  # 6 hours


class AppController:
    """Orchestrates the application lifecycle."""

    def __init__(self, config: Config):
        self._config = config
        self._gcal = GoogleCalendarClient(
            config.token_path, config.client_secrets_path
        )
        self._discord = self._create_discord_poster()
        self._sync = SyncController(config, self._gcal, self._discord)
        self._tray = TrayIcon()
        self._watcher: FileWatcher | None = None
        self._poll_timer: QTimer | None = None
        self._update_timer: QTimer | None = None
        self._update_checking = False
        self._pending_update: UpdateInfo | None = None

    def _create_discord_poster(self) -> DiscordPoster | None:
        token = self._config.get("discord_bot_token", "")
        category = self._config.get("discord_category_id", "")
        guild = self._config.get("discord_guild_id", "")
        if token and category and guild:
            return DiscordPoster(token, category, guild)
        return None

    def start(self):
        self._gcal.load_credentials()

        self._tray.set_icon(create_default_icon())
        self._tray.sync_requested.connect(self._sync.request_sync)
        self._tray.preview_requested.connect(self._show_preview)
        self._tray.settings_requested.connect(self._show_settings)
        self._tray.update_requested.connect(self._perform_update)
        self._tray.about_requested.connect(self._show_about)
        self._tray.quit_requested.connect(self._quit)
        self._sync.sync_completed.connect(self._on_sync_done)
        self._tray.show()

        if not self._config.is_setup_complete:
            self._run_setup()
        else:
            self._start_watcher()
            self._start_poll_timer()
            self._sync.request_sync()

        # Delay update check to avoid crashing during startup
        QTimer.singleShot(5000, self._start_update_checks)

    def _start_update_checks(self):
        self._check_for_update()
        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self._check_for_update)
        self._update_timer.start(_UPDATE_CHECK_INTERVAL)

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
            self._discord = self._create_discord_poster()
            self._sync._discord = self._discord
            self._start_watcher()
            self._sync.request_sync()

    def _check_for_update(self):
        if self._update_checking:
            return
        self._update_checking = True

        def _run():
            try:
                from fgc_sync.services.updater import check_for_update
                info = check_for_update()
            except Exception:
                log.exception("Update check failed")
                info = None
            self._update_checking = False
            # Deliver result to the main thread via a single-shot timer
            QTimer.singleShot(0, lambda: self._on_update_checked(info))

        threading.Thread(target=_run, daemon=True).start()

    def _on_update_checked(self, info: UpdateInfo | None):
        if info and info.is_newer:
            log.info("Update available: v%s -> v%s", info.current_version, info.latest_version)
            self._pending_update = info
            self._tray.set_update_available(info.latest_version)
            self._prompt_update(info)

    def _prompt_update(self, info: UpdateInfo):
        from PySide6.QtWidgets import QMessageBox
        msg = QMessageBox()
        msg.setWindowTitle("Update Available")
        msg.setText(
            f"A new version of {APP_NAME} is available.\n\n"
            f"Current version: v{info.current_version}\n"
            f"New version: v{info.latest_version}"
        )
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        msg.button(QMessageBox.StandardButton.Yes).setText("Update Now")
        msg.button(QMessageBox.StandardButton.No).setText("Later")
        msg.setDefaultButton(QMessageBox.StandardButton.Yes)
        if msg.exec() == QMessageBox.StandardButton.Yes:
            self._perform_update()

    def _perform_update(self):
        if not self._pending_update:
            return
        from PySide6.QtWidgets import QMessageBox
        try:
            from fgc_sync.services.updater import perform_update
            result = perform_update(self._pending_update)
        except Exception as e:
            log.exception("Update failed")
            QMessageBox.warning(None, "Update Failed", str(e))
            return

        if result == "exit":
            self._quit()
        else:
            QMessageBox.information(None, "Update Complete", result)

    def _show_about(self):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.about(None, f"About {APP_NAME}", about_text())

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
