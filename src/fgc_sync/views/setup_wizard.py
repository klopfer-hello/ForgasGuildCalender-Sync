"""First-run setup wizard: language, WoW path, Discord, Google login, calendar."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from fgc_sync import i18n
from fgc_sync.i18n import t
from fgc_sync.services.config import SAVED_VARIABLES_FILENAME, Config, decode_setup_code
from fgc_sync.services.google_calendar import GoogleCalendarClient
from fgc_sync.services.lua_parser import list_guild_keys, parse_saved_variables

log = logging.getLogger(__name__)


# Page IDs
_PAGE_LANGUAGE = 0
_PAGE_WOW = 1
_PAGE_DISCORD = 2
_PAGE_GOOGLE_LOGIN = 3
_PAGE_CALENDAR = 4


class LanguagePage(QWizardPage):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self._config = config
        self.setTitle(t("setup_wizard.language.title"))
        self.setSubTitle(t("setup_wizard.language.subtitle"))

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("language.label")))
        self._combo = QComboBox()
        for code in i18n.available_languages():
            self._combo.addItem(i18n.display_name(code), code)
        current = i18n.get_language()
        for i in range(self._combo.count()):
            if self._combo.itemData(i) == current:
                self._combo.setCurrentIndex(i)
                break
        layout.addWidget(self._combo)
        layout.addStretch()

    def validatePage(self) -> bool:
        code = self._combo.currentData()
        if code:
            self._config.set("language", code)
            # Re-translate every other page so changes take effect immediately
            wizard = self.wizard()
            if isinstance(wizard, SetupWizard):
                wizard.retranslate_pages()
        return True


class WowPathPage(QWizardPage):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self._config = config

        layout = QVBoxLayout(self)

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.textChanged.connect(self._on_path_changed)
        self._browse_btn = QPushButton()
        self._browse_btn.clicked.connect(self._browse)
        path_row.addWidget(self._path_edit)
        path_row.addWidget(self._browse_btn)
        layout.addLayout(path_row)

        self._account_label = QLabel()
        layout.addWidget(self._account_label)
        self._account_combo = QComboBox()
        self._account_combo.currentTextChanged.connect(self._on_account_changed)
        layout.addWidget(self._account_combo)

        self._guild_label = QLabel()
        layout.addWidget(self._guild_label)
        self._guild_combo = QComboBox()
        layout.addWidget(self._guild_combo)

        layout.addStretch()

        if existing := self._config.get("wow_path", ""):
            self._path_edit.setText(existing)

        self.retranslate()

    def retranslate(self):
        self.setTitle(t("setup_wizard.wow.title"))
        self.setSubTitle(t("setup_wizard.wow.subtitle"))
        self._path_edit.setPlaceholderText(t("setup_wizard.wow.path_placeholder"))
        self._browse_btn.setText(t("common.browse"))
        self._account_label.setText(t("setup_wizard.wow.account_label"))
        self._guild_label.setText(t("setup_wizard.wow.guild_label"))

    def _browse(self):
        if path := QFileDialog.getExistingDirectory(
            self, t("settings.select_wow_dir_title")
        ):
            self._path_edit.setText(path)

    @Slot()
    def _on_path_changed(self):
        wtf = Path(self._path_edit.text()) / "WTF" / "Account"
        self._account_combo.clear()
        if wtf.is_dir():
            accounts = [
                d.name
                for d in wtf.iterdir()
                if d.is_dir() and d.name != "SavedVariables"
            ]
            self._account_combo.addItems(accounts)
            if (existing := self._config.get("account_folder", "")) in accounts:
                self._account_combo.setCurrentText(existing)

    @Slot()
    def _on_account_changed(self):
        self._guild_combo.clear()
        account = self._account_combo.currentText()
        if not account:
            return
        sv_file = self._sv_file_path(account)
        if sv_file and sv_file.exists():
            try:
                db = parse_saved_variables(sv_file)
                guilds = list_guild_keys(db)
                self._guild_combo.addItems(guilds)
                if (existing := self._config.get("guild_key", "")) in guilds:
                    self._guild_combo.setCurrentText(existing)
            except Exception as e:
                log.warning("Could not parse SavedVariables: %s", e)

    def _sv_file_path(self, account: str) -> Path | None:
        wow = self._path_edit.text()
        if not wow:
            return None
        return (
            Path(wow)
            / "WTF"
            / "Account"
            / account
            / "SavedVariables"
            / SAVED_VARIABLES_FILENAME
        )

    def validatePage(self) -> bool:
        wow_path = self._path_edit.text()
        account = self._account_combo.currentText()
        guild = self._guild_combo.currentText()

        if not wow_path or not account or not guild:
            QMessageBox.warning(
                self,
                t("common.incomplete_title"),
                t("setup_wizard.wow.incomplete_message"),
            )
            return False

        sv_file = self._sv_file_path(account)
        if not sv_file or not sv_file.exists():
            QMessageBox.warning(
                self,
                t("common.not_found_title"),
                t("setup_wizard.wow.not_found_message", path=sv_file),
            )
            return False

        self._config.set("wow_path", wow_path)
        self._config.set("account_folder", account)
        self._config.set("guild_key", guild)
        return True


class DiscordPage(QWizardPage):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self._config = config

        layout = QVBoxLayout(self)

        # Setup code import
        code_row = QHBoxLayout()
        self._code_edit = QLineEdit()
        self._import_btn = QPushButton()
        self._import_btn.clicked.connect(self._import_code)
        code_row.addWidget(self._code_edit)
        code_row.addWidget(self._import_btn)
        layout.addLayout(code_row)

        self._token_label = QLabel()
        layout.addWidget(self._token_label)
        self._token_edit = QLineEdit()
        self._token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self._token_edit)

        self._guild_label = QLabel()
        layout.addWidget(self._guild_label)
        self._guild_edit = QLineEdit()
        layout.addWidget(self._guild_edit)

        self._forum_label = QLabel()
        layout.addWidget(self._forum_label)
        self._forum_edit = QLineEdit()
        layout.addWidget(self._forum_edit)

        layout.addStretch()

        # Pre-fill existing values
        if v := self._config.get("discord_bot_token", ""):
            self._token_edit.setText(v)
        if v := self._config.get("discord_guild_id", ""):
            self._guild_edit.setText(v)
        if v := self._config.get("discord_forum_id", ""):
            self._forum_edit.setText(v)

        self.retranslate()

    def retranslate(self):
        self.setTitle(t("setup_wizard.discord.title"))
        self.setSubTitle(t("setup_wizard.discord.subtitle"))
        self._code_edit.setPlaceholderText(
            t("setup_wizard.discord.setup_code_placeholder")
        )
        self._import_btn.setText(t("setup_wizard.discord.import_button"))
        self._token_label.setText(t("setup_wizard.discord.bot_token_label"))
        self._token_edit.setPlaceholderText(
            t("setup_wizard.discord.bot_token_placeholder")
        )
        self._guild_label.setText(t("setup_wizard.discord.server_id_label"))
        self._guild_edit.setPlaceholderText(
            t("setup_wizard.discord.server_id_placeholder")
        )
        self._forum_label.setText(t("setup_wizard.discord.forum_id_label"))
        self._forum_edit.setPlaceholderText(
            t("setup_wizard.discord.forum_id_placeholder")
        )

    @Slot()
    def _import_code(self):
        code = self._code_edit.text().strip()
        if not code:
            return
        values = decode_setup_code(code)
        if values:
            self._token_edit.setText(values.get("discord_bot_token", ""))
            self._guild_edit.setText(values.get("discord_guild_id", ""))
            self._forum_edit.setText(values.get("discord_forum_id", ""))
            self._code_edit.clear()
        else:
            QMessageBox.warning(
                self,
                t("setup_wizard.discord.invalid_code_title"),
                t("setup_wizard.discord.invalid_code_message"),
            )

    def validatePage(self) -> bool:
        if self.wizard().skipped_page:
            return True

        token = self._token_edit.text().strip()
        guild_id = self._guild_edit.text().strip()
        forum_id = self._forum_edit.text().strip()

        # All or nothing — if any field is filled, all must be
        filled = [bool(token), bool(guild_id), bool(forum_id)]
        if any(filled) and not all(filled):
            QMessageBox.warning(
                self,
                t("common.incomplete_title"),
                t("setup_wizard.discord.incomplete_message"),
            )
            return False

        if all(filled):
            self._config.set("discord_bot_token", token)
            self._config.set("discord_guild_id", guild_id)
            self._config.set("discord_forum_id", forum_id)

        return True


class GoogleLoginPage(QWizardPage):
    def __init__(self, config: Config, gcal: GoogleCalendarClient, parent=None):
        super().__init__(parent)
        self._config = config
        self._gcal = gcal
        self._authenticated = False

        layout = QVBoxLayout(self)
        self._status_label = QLabel()
        layout.addWidget(self._status_label)

        self._login_btn = QPushButton()
        self._login_btn.setProperty("primary", True)
        self._login_btn.clicked.connect(self._do_login)
        layout.addWidget(self._login_btn)
        layout.addStretch()

        if self._gcal.load_credentials():
            self._authenticated = True

        self.retranslate()

    def retranslate(self):
        self.setTitle(t("setup_wizard.google.title"))
        self.setSubTitle(t("setup_wizard.google.subtitle"))
        if self._authenticated:
            self._status_label.setText(t("setup_wizard.google.logged_in"))
            self._login_btn.setText(t("setup_wizard.google.relogin_button"))
        else:
            self._status_label.setText(t("setup_wizard.google.not_logged_in"))
            self._login_btn.setText(t("setup_wizard.google.login_button"))

    @Slot()
    def _do_login(self):
        try:
            if not self._config.client_secrets_path.exists():
                QMessageBox.critical(
                    self,
                    t("setup_wizard.google.secrets_missing_title"),
                    t(
                        "setup_wizard.google.secrets_missing_message",
                        path=self._config.client_secrets_path,
                    ),
                )
                return
            self._status_label.setText(t("setup_wizard.google.opening_browser"))
            if self._gcal.authenticate():
                self._authenticated = True
                self._status_label.setText(t("setup_wizard.google.login_success"))
                self._login_btn.setText(t("setup_wizard.google.relogin_button"))
            else:
                self._status_label.setText(t("setup_wizard.google.login_failed"))
        except Exception as e:
            QMessageBox.critical(
                self, t("setup_wizard.google.login_error_title"), str(e)
            )
            self._status_label.setText(t("setup_wizard.google.login_failed"))

    def validatePage(self) -> bool:
        if self.wizard().skipped_page:
            return True
        if not self._authenticated:
            QMessageBox.warning(
                self,
                t("setup_wizard.google.not_logged_in_title"),
                t("setup_wizard.google.not_logged_in_message"),
            )
            return False
        return True


class CalendarSelectPage(QWizardPage):
    def __init__(self, config: Config, gcal: GoogleCalendarClient, parent=None):
        super().__init__(parent)
        self._config = config
        self._gcal = gcal
        self._calendars: list[dict] = []

        layout = QVBoxLayout(self)
        self._calendar_combo = QComboBox()
        layout.addWidget(self._calendar_combo)

        self._refresh_btn = QPushButton()
        self._refresh_btn.clicked.connect(self._load_calendars)
        layout.addWidget(self._refresh_btn)
        layout.addStretch()

        self.retranslate()

    def retranslate(self):
        self.setTitle(t("setup_wizard.calendar.title"))
        self.setSubTitle(t("setup_wizard.calendar.subtitle"))
        self._refresh_btn.setText(t("setup_wizard.calendar.refresh_button"))

    def initializePage(self):
        self._load_calendars()

    @Slot()
    def _load_calendars(self):
        self._calendar_combo.clear()
        try:
            self._calendars = self._gcal.list_calendars()
            for cal in self._calendars:
                label = cal["summary"]
                if cal.get("primary"):
                    label += t("setup_wizard.calendar.primary_suffix")
                self._calendar_combo.addItem(label, cal["id"])

            existing = self._config.get("calendar_id", "")
            for i, cal in enumerate(self._calendars):
                if cal["id"] == existing:
                    self._calendar_combo.setCurrentIndex(i)
                    break
        except Exception as e:
            QMessageBox.warning(
                self,
                t("common.error_title"),
                t("setup_wizard.calendar.load_error_message", error=e),
            )

    def validatePage(self) -> bool:
        if self.wizard().skipped_page:
            return True
        if self._calendar_combo.currentIndex() < 0 or not self._calendars:
            QMessageBox.warning(
                self,
                t("setup_wizard.calendar.no_calendar_title"),
                t("setup_wizard.calendar.no_calendar_message"),
            )
            return False
        self._config.set("calendar_id", self._calendar_combo.currentData())
        return True


class SetupWizard(QWizard):
    # Set to True momentarily by _on_skip so validatePage() passes
    skipped_page = False

    def __init__(self, config: Config, gcal: GoogleCalendarClient, parent=None):
        super().__init__(parent)
        self.setMinimumSize(500, 480)
        # Use ModernStyle so QSS is honored (Aero/Vista native style on
        # Windows ignores stylesheet colors for QLineEdit/QComboBox).
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

        self.setPage(_PAGE_LANGUAGE, LanguagePage(config, self))
        self.setPage(_PAGE_WOW, WowPathPage(config, self))
        self.setPage(_PAGE_DISCORD, DiscordPage(config, self))
        self.setPage(_PAGE_GOOGLE_LOGIN, GoogleLoginPage(config, gcal, self))
        self.setPage(_PAGE_CALENDAR, CalendarSelectPage(config, gcal, self))

        self.setOption(QWizard.WizardOption.HaveCustomButton1, True)
        self.customButtonClicked.connect(self._on_skip)
        self.currentIdChanged.connect(self._on_page_changed)

        self.retranslate_pages()

    def retranslate_pages(self):
        """Re-apply translations on every page (called when language changes)."""
        self.setWindowTitle(t("setup_wizard.window_title"))
        self.setButtonText(QWizard.WizardButton.CustomButton1, t("common.skip"))
        for page_id in (
            _PAGE_WOW,
            _PAGE_DISCORD,
            _PAGE_GOOGLE_LOGIN,
            _PAGE_CALENDAR,
        ):
            page = self.page(page_id)
            if hasattr(page, "retranslate"):
                page.retranslate()

    def _on_page_changed(self, page_id: int):
        # Reset skip flag when navigating normally
        self.skipped_page = False
        is_optional = page_id in (
            _PAGE_DISCORD,
            _PAGE_GOOGLE_LOGIN,
            _PAGE_CALENDAR,
        )
        self.button(QWizard.WizardButton.CustomButton1).setVisible(is_optional)

    def _on_skip(self):
        self.skipped_page = True
        current = self.currentId()
        if current == _PAGE_DISCORD:
            # Skip to Google login page
            self.next()
        elif current in (_PAGE_GOOGLE_LOGIN, _PAGE_CALENDAR):
            # Skip Google entirely — finish the wizard
            self.accept()

    def showEvent(self, event):
        super().showEvent(event)
        self.button(QWizard.WizardButton.CustomButton1).setVisible(False)
        from fgc_sync.views.styles import apply_acrylic

        apply_acrylic(self)
