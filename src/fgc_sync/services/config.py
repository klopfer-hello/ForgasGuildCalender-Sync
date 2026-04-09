"""Configuration persistence — JSON file in %APPDATA%."""

from __future__ import annotations

import base64
import json
import os
import zlib
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


_SETUP_CODE_PREFIX = "fgc1-"
_SETUP_CODE_KEYS = ("discord_bot_token", "discord_guild_id", "discord_forum_id")


def encode_setup_code(config_data: dict) -> str:
    """Encode Discord config values into a compact, obfuscated setup code."""
    payload = {k: config_data.get(k, "") for k in _SETUP_CODE_KEYS}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    compressed = zlib.compress(raw, level=9)
    return _SETUP_CODE_PREFIX + base64.urlsafe_b64encode(compressed).decode().rstrip(
        "="
    )


def decode_setup_code(code: str) -> dict | None:
    """Decode a setup code back into config key/value pairs.

    Returns ``None`` if the code is invalid or corrupted.
    """
    code = code.strip()
    if not code.startswith(_SETUP_CODE_PREFIX):
        return None
    b64 = code[len(_SETUP_CODE_PREFIX) :]
    # Restore base64 padding
    b64 += "=" * (-len(b64) % 4)
    try:
        compressed = base64.urlsafe_b64decode(b64)
        raw = zlib.decompress(compressed)
        data = json.loads(raw)
    except Exception:
        return None
    # Validate that the expected keys are present and non-empty
    if not all(data.get(k) for k in _SETUP_CODE_KEYS):
        return None
    return {k: data[k] for k in _SETUP_CODE_KEYS}


class Config:
    """Simple key-value config backed by a JSON file."""

    def __init__(self, path: Path | None = None):
        self._path = path or (_app_data_dir() / "config.json")
        self._data: dict = {}
        self._snapshot: dict | None = None
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def app_data_dir(self) -> Path:
        return self._path.parent

    def load(self):
        if self._path.exists():
            with open(self._path, encoding="utf-8") as f:
                self._data = json.load(f)
        else:
            self._data = {}

    def save(self):
        if self._snapshot is not None:
            # Inside a transaction — defer writing to disk
            return
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value
        self.save()

    def begin_transaction(self):
        """Snapshot current state so changes can be rolled back."""
        self._snapshot = json.loads(json.dumps(self._data))

    def commit_transaction(self):
        """Flush buffered changes to disk."""
        self._snapshot = None
        self.save()

    def rollback_transaction(self):
        """Discard all changes made since begin_transaction."""
        if self._snapshot is not None:
            self._data = self._snapshot
            self._snapshot = None

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
            Path(wow)
            / "WTF"
            / "Account"
            / account
            / "SavedVariables"
            / SAVED_VARIABLES_FILENAME
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
