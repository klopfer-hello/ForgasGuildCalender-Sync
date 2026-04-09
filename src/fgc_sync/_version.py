"""Single source of truth for version and license metadata."""

from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "FGC Sync"
GITHUB_REPO = "klopfer-hello/ForgasGuildCalender-Sync"
PROJECT_URL = f"https://github.com/{GITHUB_REPO}"


def _read_version_from_pyproject(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("version"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None


def _resolve_version() -> str:
    # 1. Source tree (dev / editable install)
    v = _read_version_from_pyproject(Path(__file__).parents[2] / "pyproject.toml")
    if v:
        return v
    # 2. PyInstaller bundle (_MEIPASS)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        v = _read_version_from_pyproject(Path(meipass) / "pyproject.toml")
        if v:
            return v
    # 3. Package metadata (pip install from wheel/git without source)
    try:
        from importlib.metadata import metadata

        return metadata("fgc-sync")["Version"]
    except Exception:
        pass
    return "dev"


__version__ = _resolve_version()

LICENSE_TEXT = (
    "MIT License\n"
    "Copyright (c) 2026 klopfer-hello\n"
    "\n"
    "This software uses the following open-source libraries:\n"
    "  PySide6 (Qt for Python) - LGPL-3.0\n"
    "  Pillow - HPND\n"
    "  google-api-python-client - Apache-2.0\n"
    "  requests - Apache-2.0\n"
    "  watchdog - Apache-2.0\n"
    "  slpp - MIT\n"
    "\n"
    "PySide6 is distributed under the LGPL-3.0. You may rebuild the\n"
    "executables from source to use a different version of PySide6.\n"
    "See the project README for build instructions."
)


def about_text() -> str:
    return f"{APP_NAME} v{__version__}\n{PROJECT_URL}\n\n{LICENSE_TEXT}"
