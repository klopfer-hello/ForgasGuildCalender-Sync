"""Update-related data models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class InstallMode(Enum):
    EXE = "exe"
    PIP = "pip"


@dataclass
class UpdateInfo:
    current_version: str
    latest_version: str
    is_newer: bool
    download_url: str | None = None
    release_notes: str = ""
