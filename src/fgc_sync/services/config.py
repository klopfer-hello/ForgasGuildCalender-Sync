"""Configuration persistence — JSON file in %APPDATA%."""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import shutil
import sys
import tempfile
import zlib
from pathlib import Path

from fgc_sync import i18n
from fgc_sync.services import config_migrations

log = logging.getLogger(__name__)

APP_NAME = "ForgasGuildCalendar-Sync"
SAVED_VARIABLES_FILENAME = "ForgasGuildCalendar.lua"


def _read_json_dict(path: Path) -> dict | None:
    """Read and parse ``path`` as a JSON object.

    Returns the parsed ``dict`` on success, or ``None`` if the file is missing,
    empty, all-NUL/whitespace (the filesystem-corruption signature), not valid
    JSON, or not a JSON object. Never raises — callers decide how to recover.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    # A truncated/zero-filled file decodes to NUL chars, which json rejects;
    # an empty file fails too. Bail early on anything with no real content.
    if not text.strip("\x00 \t\r\n"):
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _app_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    app_dir = base / APP_NAME
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def default_wow_path() -> str:
    """Best-guess WoW (TBC Anniversary) install directory for first-time setup.

    Points at the ``_anniversary_`` flavour folder (the one containing ``WTF``),
    which is what the rest of setup expects. Returns ``""`` on platforms where we
    have no sensible guess so the field stays blank.
    """
    if sys.platform == "darwin":
        return "/Applications/World of Warcraft/_anniversary_"
    if os.name == "nt":
        return "C:\\Program Files (x86)\\World of Warcraft\\_anniversary_"
    return ""


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
        # Apply forward-only schema migrations on existing configs only —
        # first-time installs have nothing to migrate; the setup wizard
        # writes the canonical shape.
        if self._path.exists() and config_migrations.apply_all(self._data):
            self.save()
        i18n.set_language(self.get("language"))

    @property
    def path(self) -> Path:
        return self._path

    @property
    def app_data_dir(self) -> Path:
        return self._path.parent

    @property
    def _backup_path(self) -> Path:
        return self._path.with_name(self._path.name + ".bak")

    def load(self):
        data = _read_json_dict(self._path)
        if data is not None:
            self._data = data
            return

        if not self._path.exists():
            self._data = {}
            return

        # The file exists but is unreadable/corrupt (e.g. a power loss zero-fills
        # it). Try the last-known-good rolling backup before giving up.
        recovered = _read_json_dict(self._backup_path)
        if recovered is not None:
            log.warning(
                "config.json is corrupt; recovering from %s", self._backup_path.name
            )
            self._preserve_corrupt()
            self._data = recovered
            # Rewrite a clean config.json from the recovered data so the next
            # run loads normally. _atomic_write won't clobber the good backup
            # because the current (corrupt) file is not a valid dict.
            self._atomic_write(self._data)
            return

        # No usable backup — preserve the corrupt file for forensics and start
        # fresh rather than crashing on every launch.
        log.error("config.json is corrupt and no valid backup exists; starting fresh")
        self._preserve_corrupt()
        self._data = {}

    def _preserve_corrupt(self):
        """Copy a corrupt config aside so it isn't lost when we overwrite it."""
        try:
            corrupt = self._path.with_name(self._path.name + ".corrupt")
            shutil.copy2(self._path, corrupt)
        except OSError:
            log.warning("Could not preserve corrupt config", exc_info=True)

    def _atomic_write(self, data: dict):
        """Write ``data`` to disk atomically, keeping a rolling backup.

        Serialize first (so a serialization error can't truncate the file),
        roll the current valid config into ``.bak``, then write to a temp file
        and ``os.replace`` it into place — replace is atomic on Windows and
        POSIX, so a crash mid-write can never leave a half-written config.
        """
        text = json.dumps(data, indent=2, ensure_ascii=False)
        self._path.parent.mkdir(parents=True, exist_ok=True)

        # Roll the last-known-good config into the backup before replacing it.
        # Skip if the current file isn't a valid dict so we never overwrite a
        # good backup with a corrupt source.
        if _read_json_dict(self._path) is not None:
            try:
                shutil.copy2(self._path, self._backup_path)
            except OSError:
                log.warning("Could not update config backup", exc_info=True)

        fd, tmp = tempfile.mkstemp(
            dir=self._path.parent, prefix=".config-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def save(self):
        if self._snapshot is not None:
            # Inside a transaction — defer writing to disk
            return
        self._atomic_write(self._data)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value
        if key == "language":
            i18n.set_language(value)
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
