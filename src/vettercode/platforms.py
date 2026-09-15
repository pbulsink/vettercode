"""Platform abstraction: OS-appropriate directories and shell discovery.

Vettercode runs on macOS, Linux and Windows. The differences it has to care
about are small but real:

- **State directory.** Unix uses ``$XDG_CONFIG_HOME`` / ``~/.config``; Windows
  uses ``%APPDATA%`` (roaming). Cache/failure copies use ``$XDG_CACHE_HOME`` /
  ``~/.cache`` and ``%LOCALAPPDATA%`` respectively.
- **Agent shell.** mini-swe-agent executes model-issued commands with
  ``shell=True``, which is ``/bin/sh`` on Unix but ``cmd.exe`` on Windows. The
  agent prompts (and mini-swe-agent's own submission protocol) are written in
  POSIX shell, so on Windows we wrap commands in a ``bash`` from Git for
  Windows / WSL-less installs. See :func:`find_bash`.
- **Symlinks.** Copying a tree with ``symlinks=True`` raises on Windows unless
  the user has Developer Mode enabled, so the caller degrades gracefully.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

APP_NAME = "vettercode"

# Common install locations for Git for Windows' bundled bash, used when bash is
# not already on PATH.
_WINDOWS_BASH_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
)


def config_home() -> Path:
    """Base directory for ``config.yaml``, ``state.db`` and ``logs/``."""
    if IS_WINDOWS:
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME
        return Path.home() / "AppData" / "Roaming" / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def cache_home() -> Path:
    """Base directory for preserved failure clones."""
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_NAME / "cache"
        return Path.home() / "AppData" / "Local" / APP_NAME / "cache"
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".cache" / APP_NAME


def find_bash() -> str | None:
    """Return a POSIX ``bash`` executable, or None if none can be found.

    On Unix this is whatever ``bash`` is on PATH. On Windows we also probe the
    standard Git for Windows install locations, since ``git`` is already a hard
    requirement and ships one.
    """
    found = shutil.which("bash")
    if found:
        return found
    if IS_WINDOWS:
        for candidate in _WINDOWS_BASH_CANDIDATES:
            if Path(candidate).is_file():
                return candidate
    return None


def gh_install_hint() -> str:
    """Platform-appropriate instructions for installing the `gh` CLI."""
    if IS_WINDOWS:
        return "Install gh: winget install --id GitHub.cli, then run: gh auth login"
    if sys.platform == "darwin":
        return "Install gh: brew install gh, then run: gh auth login"
    return "Install gh (see https://github.com/cli/cli#installation), then run: gh auth login"


def supports_symlink_copy() -> bool:
    """Whether ``shutil.copytree(..., symlinks=True)`` is safe to request.

    Windows only permits symlink creation with Developer Mode or elevation, so
    failures there are common enough to avoid asking for it by default.
    """
    return not IS_WINDOWS
