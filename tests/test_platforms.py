import sys
from pathlib import Path

import pytest

from vettercode import platforms


@pytest.fixture(autouse=True)
def _clear_dir_env(monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)


def test_config_home_windows_uses_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(platforms, "IS_WINDOWS", True)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert platforms.config_home() == tmp_path / "vettercode"


def test_config_home_windows_without_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(platforms, "IS_WINDOWS", True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert platforms.config_home() == tmp_path / "AppData" / "Roaming" / "vettercode"


def test_config_home_unix_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setattr(platforms, "IS_WINDOWS", False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert platforms.config_home() == tmp_path / "vettercode"


def test_config_home_unix_default(monkeypatch, tmp_path):
    monkeypatch.setattr(platforms, "IS_WINDOWS", False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert platforms.config_home() == tmp_path / ".config" / "vettercode"


def test_cache_home_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(platforms, "IS_WINDOWS", True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert platforms.cache_home() == tmp_path / "vettercode" / "cache"


def test_cache_home_windows_without_localappdata(monkeypatch, tmp_path):
    monkeypatch.setattr(platforms, "IS_WINDOWS", True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert platforms.cache_home() == tmp_path / "AppData" / "Local" / "vettercode" / "cache"


def test_cache_home_unix(monkeypatch, tmp_path):
    monkeypatch.setattr(platforms, "IS_WINDOWS", False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert platforms.cache_home() == tmp_path / "vettercode"


def test_cache_home_unix_default(monkeypatch, tmp_path):
    monkeypatch.setattr(platforms, "IS_WINDOWS", False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert platforms.cache_home() == tmp_path / ".cache" / "vettercode"


def test_find_bash_prefers_path(monkeypatch):
    monkeypatch.setattr(platforms.shutil, "which", lambda _: "/usr/bin/bash")
    assert platforms.find_bash() == "/usr/bin/bash"


def test_find_bash_falls_back_to_git_for_windows(monkeypatch, tmp_path):
    fake_bash = tmp_path / "bash.exe"
    fake_bash.write_text("")
    monkeypatch.setattr(platforms.shutil, "which", lambda _: None)
    monkeypatch.setattr(platforms, "IS_WINDOWS", True)
    monkeypatch.setattr(platforms, "_WINDOWS_BASH_CANDIDATES", (str(fake_bash),))
    assert platforms.find_bash() == str(fake_bash)


def test_find_bash_missing(monkeypatch):
    monkeypatch.setattr(platforms.shutil, "which", lambda _: None)
    monkeypatch.setattr(platforms, "IS_WINDOWS", True)
    monkeypatch.setattr(platforms, "_WINDOWS_BASH_CANDIDATES", ())
    assert platforms.find_bash() is None


def test_find_bash_missing_on_unix(monkeypatch):
    monkeypatch.setattr(platforms.shutil, "which", lambda _: None)
    monkeypatch.setattr(platforms, "IS_WINDOWS", False)
    assert platforms.find_bash() is None


@pytest.mark.parametrize(
    ("platform_name", "expected"),
    [("win32", "winget"), ("darwin", "brew"), ("linux", "github.com/cli")],
)
def test_gh_install_hint(monkeypatch, platform_name, expected):
    monkeypatch.setattr(platforms, "IS_WINDOWS", platform_name == "win32")
    monkeypatch.setattr(platforms.sys, "platform", platform_name)
    hint = platforms.gh_install_hint()
    assert expected in hint
    assert "gh auth login" in hint


def test_supports_symlink_copy_tracks_platform(monkeypatch):
    monkeypatch.setattr(platforms, "IS_WINDOWS", True)
    assert platforms.supports_symlink_copy() is False
    monkeypatch.setattr(platforms, "IS_WINDOWS", False)
    assert platforms.supports_symlink_copy() is True


def test_is_windows_matches_interpreter():
    assert platforms.IS_WINDOWS == (sys.platform == "win32")
