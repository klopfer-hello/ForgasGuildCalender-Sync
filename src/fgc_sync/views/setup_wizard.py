"""First-run setup wizard: WoW path, Discord, Google login, calendar."""

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

from fgc_sync.services.config import SAVED_VARIABLES_FILENAME, Config, decode_setup_code
from fgc_sync.services.google_calendar import GoogleCalendarClient
from fgc_sync.services.lua_parser import list_guild_keys, parse_saved_variables

log = logging.getLogger(__name__)


# Page IDs
_PAGE_WOW = 0
_PAGE_DISCORD = 1
_PAGE_GOOGLE_LOGIN = 2
_PAGE_CALENDAR = 3


class WowPathPage(QWizardPage):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self._config = config
        self.setTitle("WoW Installation")
        self.setSubTitle("Select your WoW directory and guild.")

        layout = QVBoxLayout(self)

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Path to WoW directory...")
        self._path_edit.textChanged.connect(self._on_path_changed)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(self._path_edit)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        layout.addWidget(QLabel("Account:"))
        self._account_combo = QComboBox()
        self._account_combo.currentTextChanged.connect(self._on_account_changed)
        layout.addWidget(self._account_combo)

        layout.addWidget(QLabel("Guild:"))
        self._guild_combo = QComboBox()
        layout.addWidget(self._guild_combo)

        layout.addStretch()

        if existing := self._config.get("wow_path", ""):
            self._path_edit.setText(existing)

    def _browse(self):
        if path := QFileDialog.getExistingDirectory(self, "Select WoW Directory"):
            self._path_edit.setText(path)

    @Slot()
    def _on_path_changed(self):
        wtf = Path(self._path_edit.text()) / "WTF" / "Account"
        self._account_combo.clear()
        if wtf.is_dir():
            accounts = [
                d.name for d in wtf.iterdir()
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
            Path(wow) / "WTF" / "Account" / account
            / "SavedVariables" / SAVED_VARIABLES_FILENAME
        )

    def validatePage(self) -> bool:
        wow_path = self._path_edit.text()
        account = self._account_combo.currentText()
        guild = self._guild_combo.currentText()

        if not wow_path or not account or not guild:
            QMessageBox.warning(self, "Incomplete", "Please fill all fields.")
            return False

        sv_file = self._sv_file_path(account)
        if not sv_file or not sv_file.exists():
            QMessageBox.warning(self, "Not Found", f"SavedVariables not found:\n{sv_file}")
            return False

        self._config.set("wow_path", wow_path)
        self._config.set("account_folder", account)
        self._config.set("guild_key", guild)
        return True


class DiscordPage(QWizardPage):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self._config = config
        self.setTitle("Discord Bot (optional)")
        self.setSubTitle(
            "Paste a setup code to auto-fill, or enter credentials manually. "
            "Press Skip to configure this later in Settings."
        )

        layout = QVBoxLayout(self)

        # Setup code import
        code_row = QHBoxLayout()
        self._code_edit = QLineEdit()
        self._code_edit.setPlaceholderText("Paste setup code (fgc1-...)...")
        import_btn = QPushButton("Import")
        import_btn.clicked.connect(self._import_code)
        code_row.addWidget(self._code_edit)
        code_row.addWidget(import_btn)
        layout.addLayout(code_row)

        layout.addWidget(QLabel("Bot Token:"))
        self._token_edit = QLineEdit()
        self._token_edit.setPlaceholderText("Paste your bot token...")
        self._token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self._token_edit)

        layout.addWidget(QLabel("Server (Guild) ID:"))
        self._guild_edit = QLineEdit()
        self._guild_edit.setPlaceholderText("Right-click server → Copy Server ID")
        layout.addWidget(self._guild_edit)

        layout.addWidget(QLabel("Forum Channel ID:"))
        self._forum_edit = QLineEdit()
        self._forum_edit.setPlaceholderText("Right-click forum channel → Copy Channel ID")
        layout.addWidget(self._forum_edit)

        layout.addStretch()

        # Pre-fill existing values
        if v := self._config.get("discord_bot_token", ""):
            self._token_edit.setText(v)
        if v := self._config.get("discord_guild_id", ""):
            self._guild_edit.setText(v)
        if v := self._config.get("discord_forum_id", ""):
            self._forum_edit.setText(v)

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
            QMessageBox.warning(self, "Invalid Code", "The setup code is invalid or corrupted.")

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
                self, "Incomplete",
                "Please fill all three fields or press Skip.",
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
        self.setTitle("Google Calendar (optional)")
        self.setSubTitle(
            "Login with Google to sync raids to your calendar. "
            "Press Skip to configure this later in Settings."
        )

        layout = QVBoxLayout(self)
        self._status_label = QLabel("Not logged in")
        layout.addWidget(self._status_label)

        self._login_btn = QPushButton("Login with Google")
        self._login_btn.setProperty("primary", True)
        self._login_btn.clicked.connect(self._do_login)
        layout.addWidget(self._login_btn)
        layout.addStretch()

        if self._gcal.load_credentials():
            self._authenticated = True
            self._status_label.setText("Already logged in")
            self._login_btn.setText("Re-login")

    @Slot()
    def _do_login(self):
        try:
            if not self._config.client_secrets_path.exists():
                QMessageBox.critical(
                    self, "Missing Credentials",
                    f"client_secrets.json not found at:\n{self._config.client_secrets_path}",
                )
                return
            self._status_label.setText("Opening browser...")
            if self._gcal.authenticate():
                self._authenticated = True
                self._status_label.setText("Logged in successfully!")
                self._login_btn.setText("Re-login")
            else:
                self._status_label.setText("Login failed")
        except Exception as e:
            QMessageBox.critical(self, "Login Error", str(e))
            self._status_label.setText("Login failed")

    def validatePage(self) -> bool:
        if self.wizard().skipped_page:
            return True
        if not self._authenticated:
            QMessageBox.warning(
                self, "Not Logged In",
                "Please login with Google or press Skip.",
            )
            return False
        return True


class CalendarSelectPage(QWizardPage):
    def __init__(self, config: Config, gcal: GoogleCalendarClient, parent=None):
        super().__init__(parent)
        self._config = config
        self._gcal = gcal
        self._calendars: list[dict] = []
        self.setTitle("Select Calendar")
        self.setSubTitle("Choose which calendar to sync your raids to.")

        layout = QVBoxLayout(self)
        self._calendar_combo = QComboBox()
        layout.addWidget(self._calendar_combo)

        refresh_btn = QPushButton("Refresh Calendars")
        refresh_btn.clicked.connect(self._load_calendars)
        layout.addWidget(refresh_btn)
        layout.addStretch()

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
                    label += " (primary)"
                self._calendar_combo.addItem(label, cal["id"])

            existing = self._config.get("calendar_id", "")
            for i, cal in enumerate(self._calendars):
                if cal["id"] == existing:
                    self._calendar_combo.setCurrentIndex(i)
                    break
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not load calendars:\n{e}")

    def validatePage(self) -> bool:
        if self.wizard().skipped_page:
            return True
        if self._calendar_combo.currentIndex() < 0 or not self._calendars:
            QMessageBox.warning(self, "No Calendar", "Please select a calendar.")
            return False
        self._config.set("calendar_id", self._calendar_combo.currentData())
        return True


class SetupWizard(QWizard):
    # Set to True momentarily by _on_skip so validatePage() passes
    skipped_page = False

    def __init__(self, config: Config, gcal: GoogleCalendarClient, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FGC Sync - Setup")
        self.setMinimumSize(500, 380)
        # Use ModernStyle so QSS is honored (Aero/Vista native style on
        # Windows ignores stylesheet colors for QLineEdit/QComboBox).
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

        self.setPage(_PAGE_WOW, WowPathPage(config, self))
        self.setPage(_PAGE_DISCORD, DiscordPage(config, self))
        self.setPage(_PAGE_GOOGLE_LOGIN, GoogleLoginPage(config, gcal, self))
        self.setPage(_PAGE_CALENDAR, CalendarSelectPage(config, gcal, self))

        self.setButtonText(QWizard.WizardButton.CustomButton1, "Skip")
        self.setOption(QWizard.WizardOption.HaveCustomButton1, True)
        self.customButtonClicked.connect(self._on_skip)
        self.currentIdChanged.connect(self._on_page_changed)

    def _on_page_changed(self, page_id: int):
        # Reset skip flag when navigating normally
        self.skipped_page = False
        is_optional = page_id in (
            _PAGE_DISCORD, _PAGE_GOOGLE_LOGIN, _PAGE_CALENDAR,
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
