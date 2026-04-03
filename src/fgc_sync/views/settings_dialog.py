"""Settings dialog for post-setup configuration."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from fgc_sync.services.config import SAVED_VARIABLES_FILENAME, Config
from fgc_sync.services.google_calendar import GoogleCalendarClient
from fgc_sync.services.lua_parser import list_guild_keys, parse_saved_variables

log = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    def __init__(self, config: Config, gcal: GoogleCalendarClient, parent=None):
        super().__init__(parent)
        self._config = config
        self._gcal = gcal
        self._calendars: list[dict] = []

        self.setWindowTitle("FGC Calendar Sync - Settings")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit(self._config.get("wow_path", ""))
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_wow)
        path_row.addWidget(self._path_edit)
        path_row.addWidget(browse_btn)
        form.addRow("WoW Path:", path_row)

        self._account_edit = QLineEdit(self._config.get("account_folder", ""))
        self._account_edit.setReadOnly(True)
        form.addRow("Account:", self._account_edit)

        self._guild_combo = QComboBox()
        form.addRow("Guild:", self._guild_combo)

        cal_row = QHBoxLayout()
        self._calendar_combo = QComboBox()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._load_calendars)
        cal_row.addWidget(self._calendar_combo)
        cal_row.addWidget(refresh_btn)
        form.addRow("Calendar:", cal_row)

        self._tz_edit = QLineEdit(self._config.get("timezone", "Europe/Berlin"))
        form.addRow("Timezone:", self._tz_edit)

        self._duration_spin = QSpinBox()
        self._duration_spin.setRange(1, 12)
        self._duration_spin.setValue(self._config.get("default_duration_hours", 3))
        self._duration_spin.setSuffix(" hours")
        form.addRow("Default Duration:", self._duration_spin)

        google_row = QHBoxLayout()
        self._google_status = QLabel(
            "Logged in" if self._gcal.is_authenticated else "Not logged in"
        )
        relogin_btn = QPushButton("Re-login")
        relogin_btn.clicked.connect(self._relogin)
        logout_btn = QPushButton("Logout")
        logout_btn.clicked.connect(self._logout)
        google_row.addWidget(self._google_status)
        google_row.addWidget(relogin_btn)
        google_row.addWidget(logout_btn)
        form.addRow("Google:", google_row)

        # --- Discord integration (optional) ---
        form.addRow(QLabel(""))  # spacer
        form.addRow(QLabel("Discord Integration (optional)"))

        self._discord_token_edit = QLineEdit(self._config.get("discord_bot_token", ""))
        self._discord_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._discord_token_edit.setPlaceholderText("Bot token from Discord Developer Portal")
        form.addRow("Bot Token:", self._discord_token_edit)

        self._discord_guild_edit = QLineEdit(self._config.get("discord_guild_id", ""))
        self._discord_guild_edit.setPlaceholderText("Right-click server → Copy Server ID")
        form.addRow("Server ID:", self._discord_guild_edit)

        self._discord_channel_edit = QLineEdit(self._config.get("discord_channel_id", ""))
        self._discord_channel_edit.setPlaceholderText("Right-click channel → Copy Channel ID")
        form.addRow("Channel ID:", self._discord_channel_edit)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("Save")
        save_btn.setProperty("primary", True)
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._load_guilds()
        self._load_calendars()

    def _browse_wow(self):
        if path := QFileDialog.getExistingDirectory(self, "Select WoW Directory"):
            self._path_edit.setText(path)
            self._load_guilds()

    def _load_guilds(self):
        self._guild_combo.clear()
        account = self._account_edit.text()
        if not account:
            return
        sv_file = (
            Path(self._path_edit.text()) / "WTF" / "Account" / account
            / "SavedVariables" / SAVED_VARIABLES_FILENAME
        )
        if sv_file.exists():
            try:
                db = parse_saved_variables(sv_file)
                guilds = list_guild_keys(db)
                self._guild_combo.addItems(guilds)
                if (current := self._config.get("guild_key", "")) in guilds:
                    self._guild_combo.setCurrentText(current)
            except Exception as e:
                log.warning("Failed to load guilds: %s", e)

    @Slot()
    def _load_calendars(self):
        self._calendar_combo.clear()
        if not self._gcal.is_authenticated:
            return
        try:
            self._calendars = self._gcal.list_calendars()
            for cal in self._calendars:
                label = cal["summary"]
                if cal.get("primary"):
                    label += " (primary)"
                self._calendar_combo.addItem(label, cal["id"])
            current = self._config.get("calendar_id", "")
            for i, cal in enumerate(self._calendars):
                if cal["id"] == current:
                    self._calendar_combo.setCurrentIndex(i)
                    break
        except Exception as e:
            log.warning("Failed to load calendars: %s", e)

    @Slot()
    def _relogin(self):
        try:
            if self._gcal.authenticate():
                self._google_status.setText("Logged in")
                self._load_calendars()
        except Exception as e:
            QMessageBox.critical(self, "Login Error", str(e))

    @Slot()
    def _logout(self):
        self._gcal.logout()
        self._google_status.setText("Not logged in")
        self._calendar_combo.clear()

    @Slot()
    def _save(self):
        self._config.set("wow_path", self._path_edit.text())
        self._config.set("guild_key", self._guild_combo.currentText())
        self._config.set("timezone", self._tz_edit.text())
        self._config.set("default_duration_hours", self._duration_spin.value())
        if cal_id := self._calendar_combo.currentData():
            self._config.set("calendar_id", cal_id)
        self._config.set("discord_bot_token", self._discord_token_edit.text().strip())
        self._config.set("discord_guild_id", self._discord_guild_edit.text().strip())
        self._config.set("discord_channel_id", self._discord_channel_edit.text().strip())
        self.accept()

    def showEvent(self, event):
        super().showEvent(event)
        from fgc_sync.views.styles import apply_acrylic
        apply_acrylic(self)
