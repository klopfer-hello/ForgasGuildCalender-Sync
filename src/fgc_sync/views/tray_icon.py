"""System tray icon with context menu and notifications."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


def _startup_shortcut_path() -> Path:
    startup = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return startup / "FGCCalendarSync.lnk"


log = logging.getLogger(__name__)


def _exe_path() -> str:
    """Return the path to fgc-sync.exe, checking multiple locations."""
    # 1. Next to the running interpreter (works when launched from the correct venv)
    candidate = Path(sys.executable).parent / "fgc-sync.exe"
    if candidate.exists():
        return str(candidate)
    # 2. Resolve via PATH (works when installed in a different venv/environment)
    found = shutil.which("fgc-sync")
    if found:
        return found
    # 3. Fallback to the original assumption
    return str(candidate)


def is_autostart_enabled() -> bool:
    return _startup_shortcut_path().exists()


def set_autostart(enabled: bool):
    shortcut = _startup_shortcut_path()
    if enabled:
        try:
            import subprocess
            exe = _exe_path()
            ps_script = (
                "$ws = New-Object -ComObject WScript.Shell; "
                f"$sc = $ws.CreateShortcut('{shortcut}'); "
                f"$sc.TargetPath = '{exe}'; "
                "$sc.Description = 'FGC Calendar Sync'; "
                "$sc.Save()"
            )
            subprocess.run(
                ["powershell", "-Command", ps_script],
                check=True, capture_output=True,
            )
        except Exception:
            log.exception("Failed to create startup shortcut")
    elif shortcut.exists():
        shortcut.unlink()


def create_default_icon() -> QIcon:
    """Render a simple purple 'G' icon."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(124, 77, 255))
    painter.setPen(QColor(92, 53, 204))
    painter.drawRoundedRect(4, 4, 56, 56, 12, 12)
    painter.setPen(QColor(255, 255, 255))
    painter.setFont(QFont("Segoe UI", 28, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), 0x0084, "G")  # Qt.AlignCenter
    painter.end()
    return QIcon(pixmap)


class TrayIcon(QObject):
    sync_requested = Signal()
    preview_requested = Signal()
    settings_requested = Signal()
    update_requested = Signal()
    about_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tray = QSystemTrayIcon(self)
        self._tray.setToolTip("FGC Calendar Sync")

        self._menu = QMenu()

        self._status_action = QAction("Not synced yet")
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)
        self._menu.addSeparator()

        preview = QAction("Preview Sync...", self._menu)
        preview.triggered.connect(self.preview_requested.emit)
        self._menu.addAction(preview)

        sync = QAction("Sync Now", self._menu)
        sync.triggered.connect(self.sync_requested.emit)
        self._menu.addAction(sync)

        settings = QAction("Settings...", self._menu)
        settings.triggered.connect(self.settings_requested.emit)
        self._menu.addAction(settings)

        self._menu.addSeparator()

        self._autostart_action = QAction("Start with Windows", self._menu)
        self._autostart_action.setCheckable(True)
        self._autostart_action.setChecked(is_autostart_enabled())
        self._autostart_action.toggled.connect(self._toggle_autostart)
        self._menu.addAction(self._autostart_action)

        self._menu.addSeparator()

        self._update_action = QAction("", self._menu)
        self._update_action.triggered.connect(self.update_requested.emit)
        self._update_action.setVisible(False)
        self._menu.addAction(self._update_action)

        about = QAction("About...", self._menu)
        about.triggered.connect(self.about_requested.emit)
        self._menu.addAction(about)

        quit_action = QAction("Quit", self._menu)
        quit_action.triggered.connect(self.quit_requested.emit)
        self._menu.addAction(quit_action)

        self._tray.setContextMenu(self._menu)

    def set_icon(self, icon: QIcon):
        self._tray.setIcon(icon)

    def show(self):
        self._tray.show()

    def hide(self):
        self._tray.hide()

    @Slot(str, str)
    def show_notification(self, title: str, message: str):
        self._tray.showMessage(
            title, message, QSystemTrayIcon.MessageIcon.Information, 5000
        )

    @Slot(str)
    def update_status(self, text: str):
        now = datetime.now().strftime("%H:%M")
        self._status_action.setText(f"Last sync: {now} - {text}")

    @Slot(str)
    def set_update_available(self, version: str):
        self._update_action.setText(f"Update to v{version}...")
        self._update_action.setVisible(True)

    @Slot(bool)
    def _toggle_autostart(self, enabled: bool):
        set_autostart(enabled)
        actual = is_autostart_enabled()
        if actual != enabled:
            self._autostart_action.blockSignals(True)
            self._autostart_action.setChecked(actual)
            self._autostart_action.blockSignals(False)
            self._tray.showMessage(
                "Autostart Error",
                "Failed to create startup shortcut. Check logs for details.",
                QSystemTrayIcon.MessageIcon.Warning, 5000,
            )
