"""Single source of truth for version and license metadata."""

from importlib.metadata import metadata, PackageNotFoundError

APP_NAME = "FGC Calendar Sync"
PROJECT_URL = "https://github.com/klopfer-hello/ForgasGuildCalender-Sync"

try:
    _meta = metadata("fgc-sync")
    __version__ = _meta["Version"]
except PackageNotFoundError:
    __version__ = "dev"

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
    return (
        f"{APP_NAME} v{__version__}\n"
        f"{PROJECT_URL}\n"
        f"\n"
        f"{LICENSE_TEXT}"
    )
