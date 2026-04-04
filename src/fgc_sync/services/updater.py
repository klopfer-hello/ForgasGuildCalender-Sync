"""Check for updates and perform self-update from GitHub releases."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

from fgc_sync._version import GITHUB_REPO, __version__
from fgc_sync.models.update import InstallMode, UpdateInfo

log = logging.getLogger(__name__)

_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_PIP_URL = f"git+https://github.com/{GITHUB_REPO}.git"
_HEADERS = {"User-Agent": f"FGC-Sync/{__version__}"}


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a version string like '1.3.2' into a comparable tuple."""
    return tuple(int(x) for x in v.split("."))


def detect_install_mode() -> InstallMode:
    if getattr(sys, "frozen", False):
        return InstallMode.EXE
    return InstallMode.PIP


def check_for_update() -> UpdateInfo | None:
    """Query GitHub for the latest release. Returns None on network error."""
    if __version__ == "dev":
        return None

    try:
        resp = requests.get(_API_URL, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("Update check failed: %s", e)
        return None

    data = resp.json()
    latest = data.get("tag_name", "").lstrip("v")
    if not latest:
        return None

    try:
        is_newer = _parse_version(latest) > _parse_version(__version__)
    except (ValueError, TypeError):
        return None

    download_url = None
    if detect_install_mode() == InstallMode.EXE:
        for asset in data.get("assets", []):
            if asset.get("name") == "FGC-Sync.exe":
                download_url = asset["browser_download_url"]
                break

    return UpdateInfo(
        current_version=__version__,
        latest_version=latest,
        is_newer=is_newer,
        download_url=download_url,
        release_notes=data.get("body", ""),
    )


def perform_update(info: UpdateInfo) -> str:
    """Execute the update. Returns a status message.

    For exe mode: downloads new exe, writes a swap script, and returns
    a message indicating the app should exit so the script can replace it.
    For pip mode: runs pip install --upgrade in a subprocess.
    """
    mode = detect_install_mode()

    if mode == InstallMode.PIP:
        return _update_pip()
    else:
        return _update_exe(info)


def _update_pip() -> str:
    """Upgrade via pip."""
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", _PIP_URL]
    log.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        log.error("pip upgrade failed: %s", result.stderr)
        raise RuntimeError(f"pip upgrade failed:\n{result.stderr}")
    return "Updated via pip. Restart to use the new version."


def _update_exe(info: UpdateInfo) -> str:
    """Download new exe and write a swap script to replace it after exit."""
    if not info.download_url:
        raise RuntimeError("No download URL for this release.")

    exe_path = Path(sys.executable)
    update_path = exe_path.with_suffix(".exe.update")

    # Download with progress
    log.info("Downloading %s", info.download_url)
    resp = requests.get(info.download_url, headers=_HEADERS, stream=True, timeout=60)
    resp.raise_for_status()

    with open(update_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)

    # Verify download size matches
    expected_size = None
    for header in ("content-length",):
        if header in resp.headers:
            expected_size = int(resp.headers[header])
    actual_size = update_path.stat().st_size
    if expected_size and actual_size != expected_size:
        update_path.unlink()
        raise RuntimeError(
            f"Download size mismatch: expected {expected_size}, got {actual_size}"
        )

    # Write the swap script
    script_path = Path(tempfile.gettempdir()) / "fgc_sync_update.cmd"
    script = (
        '@echo off\r\n'
        'timeout /t 2 /nobreak >nul\r\n'
        f'del "{exe_path}"\r\n'
        f'move "{update_path}" "{exe_path}"\r\n'
        f'start "" "{exe_path}"\r\n'
        f'del "%~f0"\r\n'
    )
    script_path.write_text(script, encoding="ascii")

    # Launch the script detached
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    DETACHED_PROCESS = 0x00000008
    subprocess.Popen(
        ["cmd", "/c", str(script_path)],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )

    return "exit"
