"""Tests for pure helper functions in fgc_sync.cli."""

import os
from unittest.mock import patch

from fgc_sync.cli import _normalize_path


class TestNormalizePath:
    def test_passthrough_on_linux(self):
        with patch.object(os, "name", "posix"):
            assert _normalize_path("/home/user/wow") == "/home/user/wow"

    def test_passthrough_normal_windows_path(self):
        with patch.object(os, "name", "nt"):
            assert _normalize_path("C:/Games/WoW") == "C:/Games/WoW"

    def test_converts_git_bash_path(self):
        with patch.object(os, "name", "nt"):
            assert _normalize_path("/d/Games/WoW") == "D:/Games/WoW"

    def test_converts_lowercase_drive(self):
        with patch.object(os, "name", "nt"):
            assert _normalize_path("/c/Users/test") == "C:/Users/test"

    def test_ignores_non_drive_slash(self):
        with patch.object(os, "name", "nt"):
            # /home/... doesn't match /X/ pattern
            assert _normalize_path("/home/user") == "/home/user"

    def test_empty_string(self):
        result = _normalize_path("")
        assert result == ""
