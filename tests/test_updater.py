"""Tests for pure functions in fgc_sync.services.updater."""

from fgc_sync.models.update import InstallMode
from fgc_sync.services.updater import _parse_version, detect_install_mode


class TestParseVersion:
    def test_simple_version(self):
        assert _parse_version("1.0.0") == (1, 0, 0)

    def test_multi_digit(self):
        assert _parse_version("2.10.3") == (2, 10, 3)

    def test_comparison_newer(self):
        assert _parse_version("2.0.0") > _parse_version("1.9.9")

    def test_comparison_same(self):
        assert _parse_version("1.2.3") == _parse_version("1.2.3")

    def test_comparison_patch(self):
        assert _parse_version("1.0.1") > _parse_version("1.0.0")

    def test_comparison_minor(self):
        assert _parse_version("1.1.0") > _parse_version("1.0.99")


class TestDetectInstallMode:
    def test_returns_pip_in_normal_python(self):
        # When not frozen (normal Python), should return PIP
        assert detect_install_mode() == InstallMode.PIP
