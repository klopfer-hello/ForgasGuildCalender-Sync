"""System tray icon with context menu and notifications."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


def _startup_shortcut_path() -> Path:
    startup = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return startup / "FGCCalendarSync.lnk"


def _exe_path() -> str:
    """Return the path to fgc-sync.exe in the venv."""
    return str(Path(sys.executable).parent / "fgc-sync.exe")


def is_autostart_enabled() -> bool:
    return _startup_shortcut_path().exists()


def set_autostart(enabled: bool):
    shortcut = _startup_shortcut_path()
    if enabled:
        try:
            import subprocess
            exe = _exe_path()
            ps_script = (
                f'$ws = New-Object -ComObject WScript.Shell; '
                f'$sc = $ws.CreateShortcut("{shortcut}"); '
                f'$sc.TargetPath = "{exe}"; '
                f'$sc.Description = "FGC Calendar Sync"; '
                f'$sc.Save()'
            )
            subprocess.run(
                ["powershell", "-Command", ps_script],
                check=True, capture_output=True,
            )
        except Exception:
            pass
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

    @Slot(bool)
    def _toggle_autostart(self, enabled: bool):
        set_autostart(enabled)
