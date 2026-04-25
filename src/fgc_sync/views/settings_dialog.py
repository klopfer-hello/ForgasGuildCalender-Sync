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

from fgc_sync import i18n
from fgc_sync.i18n import t
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

        self.setWindowTitle(t("settings.window_title"))
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # Language
        self._language_combo = QComboBox()
        for code in i18n.available_languages():
            self._language_combo.addItem(i18n.display_name(code), code)
        current_lang = i18n.get_language()
        for i in range(self._language_combo.count()):
            if self._language_combo.itemData(i) == current_lang:
                self._language_combo.setCurrentIndex(i)
                break
        form.addRow(t("settings.language_label"), self._language_combo)

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit(self._config.get("wow_path", ""))
        browse_btn = QPushButton(t("settings.browse_button"))
        browse_btn.clicked.connect(self._browse_wow)
        path_row.addWidget(self._path_edit)
        path_row.addWidget(browse_btn)
        form.addRow(t("settings.wow_path_label"), path_row)

        self._account_edit = QLineEdit(self._config.get("account_folder", ""))
        self._account_edit.setReadOnly(True)
        form.addRow(t("settings.account_label"), self._account_edit)

        self._guild_combo = QComboBox()
        form.addRow(t("settings.guild_label"), self._guild_combo)

        cal_row = QHBoxLayout()
        self._calendar_combo = QComboBox()
        refresh_btn = QPushButton(t("settings.refresh_button"))
        refresh_btn.clicked.connect(self._load_calendars)
        cal_row.addWidget(self._calendar_combo)
        cal_row.addWidget(refresh_btn)
        form.addRow(t("settings.calendar_label"), cal_row)

        self._tz_edit = QLineEdit(self._config.get("timezone", "Europe/Berlin"))
        form.addRow(t("settings.timezone_label"), self._tz_edit)

        self._duration_spin = QSpinBox()
        self._duration_spin.setRange(1, 12)
        self._duration_spin.setValue(self._config.get("default_duration_hours", 3))
        self._duration_spin.setSuffix(t("settings.duration_suffix"))
        form.addRow(t("settings.duration_label"), self._duration_spin)

        google_row = QHBoxLayout()
        self._google_status = QLabel(
            t("settings.logged_in")
            if self._gcal.is_authenticated
            else t("settings.not_logged_in")
        )
        relogin_btn = QPushButton(t("settings.relogin_button"))
        relogin_btn.clicked.connect(self._relogin)
        logout_btn = QPushButton(t("settings.logout_button"))
        logout_btn.clicked.connect(self._logout)
        google_row.addWidget(self._google_status)
        google_row.addWidget(relogin_btn)
        google_row.addWidget(logout_btn)
        form.addRow(t("settings.google_label"), google_row)

        # --- Discord integration (optional) ---
        form.addRow(QLabel(""))  # spacer
        form.addRow(QLabel(t("settings.discord_section")))

        self._discord_token_edit = QLineEdit(self._config.get("discord_bot_token", ""))
        self._discord_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._discord_token_edit.setPlaceholderText(t("settings.bot_token_placeholder"))
        form.addRow(t("settings.bot_token_label"), self._discord_token_edit)

        self._discord_guild_edit = QLineEdit(self._config.get("discord_guild_id", ""))
        self._discord_guild_edit.setPlaceholderText(t("settings.server_id_placeholder"))
        form.addRow(t("settings.server_id_label"), self._discord_guild_edit)

        self._discord_forum_edit = QLineEdit(self._config.get("discord_forum_id", ""))
        self._discord_forum_edit.setPlaceholderText(t("settings.forum_id_placeholder"))
        form.addRow(t("settings.forum_id_label"), self._discord_forum_edit)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton(t("settings.save_button"))
        save_btn.setProperty("primary", True)
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton(t("settings.cancel_button"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._load_guilds()
        self._load_calendars()

    def _browse_wow(self):
        if path := QFileDialog.getExistingDirectory(
            self, t("settings.select_wow_dir_title")
        ):
            self._path_edit.setText(path)
            self._load_guilds()

    def _load_guilds(self):
        self._guild_combo.clear()
        account = self._account_edit.text()
        if not account:
            return
        sv_file = (
            Path(self._path_edit.text())
            / "WTF"
            / "Account"
            / account
            / "SavedVariables"
            / SAVED_VARIABLES_FILENAME
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
                    label += t("setup_wizard.calendar.primary_suffix")
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
                self._google_status.setText(t("settings.logged_in"))
                self._load_calendars()
        except Exception as e:
            QMessageBox.critical(self, t("settings.login_error_title"), str(e))

    @Slot()
    def _logout(self):
        self._gcal.logout()
        self._google_status.setText(t("settings.not_logged_in"))
        self._calendar_combo.clear()

    @Slot()
    def _save(self):
        new_lang = self._language_combo.currentData()
        if new_lang and new_lang != i18n.get_language():
            self._config.set("language", new_lang)
        self._config.set("wow_path", self._path_edit.text())
        self._config.set("guild_key", self._guild_combo.currentText())
        self._config.set("timezone", self._tz_edit.text())
        self._config.set("default_duration_hours", self._duration_spin.value())
        if cal_id := self._calendar_combo.currentData():
            self._config.set("calendar_id", cal_id)
        self._config.set("discord_bot_token", self._discord_token_edit.text().strip())
        new_guild = self._discord_guild_edit.text().strip()
        new_forum = self._discord_forum_edit.text().strip()
        old_guild = self._config.get("discord_guild_id", "")
        old_forum = self._config.get("discord_forum_id", "")
        self._config.set("discord_guild_id", new_guild)
        self._config.set("discord_forum_id", new_forum)
        if new_guild != old_guild or new_forum != old_forum:
            # Stale thread/message IDs from the previous server/forum would
            # otherwise cause sync to skip events with matching content hashes.
            self._config.set("discord_message_mapping", {})
        self.accept()

    def showEvent(self, event):
        super().showEvent(event)
        from fgc_sync.views.styles import apply_acrylic

        apply_acrylic(self)
