"""Configuration persistence — JSON file in %APPDATA%."""

from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME = "ForgasGuildCalendar-Sync"
SAVED_VARIABLES_FILENAME = "ForgasGuildCalendar.lua"


def _app_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    app_dir = base / APP_NAME
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


class Config:
    """Simple key-value config backed by a JSON file."""

    def __init__(self, path: Path | None = None):
        self._path = path or (_app_data_dir() / "config.json")
        self._data: dict = {}
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def app_data_dir(self) -> Path:
        return self._path.parent

    def load(self):
        if self._path.exists():
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        else:
            self._data = {}

    def save(self):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value
        self.save()

    @property
    def is_setup_complete(self) -> bool:
        return bool(
            self.get("wow_path")
            and self.get("account_folder")
            and self.get("guild_key")
        )

    @property
    def is_google_configured(self) -> bool:
        return bool(self.get("calendar_id"))

    @property
    def saved_variables_path(self) -> Path | None:
        wow = self.get("wow_path")
        account = self.get("account_folder")
        if not wow or not account:
            return None
        return (
            Path(wow) / "WTF" / "Account" / account
            / "SavedVariables" / SAVED_VARIABLES_FILENAME
        )

    @property
    def log_level(self) -> str:
        return self.get("log_level", "ERROR").upper()

    @property
    def token_path(self) -> Path:
        return self.app_data_dir / "token.json"

    @property
    def client_secrets_path(self) -> Path:
        # Look next to the package first, then fall back to AppData
        local = Path(__file__).parents[3] / "client_secrets.json"
        if local.exists():
            return local
        return self.app_data_dir / "client_secrets.json"
