"""Temporary clone workdirs: cleaned on success, preserved on failure.

- Workdir is created under the OS temp directory (``$TMPDIR`` on Unix,
  ``%TEMP%`` on Windows, both via ``tempfile.gettempdir()``), in a
  ``vettercode/`` subdirectory, or under an injected base.
- On clean exit the workdir is removed, so the user's directory stays clean.
- If the body raised, the workdir is copied to the platform cache directory
  (``~/.cache/vettercode/failures`` on Unix, ``%LOCALAPPDATA%`` on Windows)
  for debugging, then removed.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .platforms import cache_home, supports_symlink_copy


class WorkdirError(RuntimeError):
    """Clone (or setup) of the temporary workdir failed."""


def _force_writable(target) -> None:
    """Clear the read-only bit so a delete can proceed (git marks objects
    read-only, which blocks deletion on Windows)."""
    try:
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _rmtree(path: Path) -> None:
    """Remove a directory tree, tolerating read-only files.

    Git marks pack/object files read-only; on Windows that makes a plain
    ``shutil.rmtree`` fail and leave the clone behind. The error handler clears
    the bit and retries. ``onerror`` is deprecated in Python 3.12 in favour of
    ``onexc``, so pick whichever the running interpreter wants.
    """

    def _on_rm_error(func, target, _exc):
        _force_writable(target)
        try:
            func(target)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_on_rm_error)
    else:
        shutil.rmtree(path, onerror=lambda f, t, exc_info: _on_rm_error(f, t, exc_info))


def default_base_dir() -> Path:
    """Temp root for clones.

    ``tempfile.gettempdir()`` already honours ``TMPDIR``/``TEMP``/``TMP`` on the
    respective platforms, so it is the portable single source of truth.
    """
    return Path(tempfile.gettempdir()) / "vettercode"


def default_failures_dir() -> Path:
    return cache_home() / "failures"



def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


class Workdir:
    """Context manager: ``with Workdir(owner, name) as wd: ...`` -> wd.path."""

    def __init__(
        self,
        owner: str,
        name: str,
        *,
        base_dir: Path | None = None,
        failures_dir: Path | None = None,
        clone_url: str | None = None,
        depth: int | None = 1,
        git: str = "git",
    ):
        self.owner = owner
        self.name = name
        self.base_dir = Path(base_dir) if base_dir else default_base_dir()
        self.failures_dir = Path(failures_dir) if failures_dir else default_failures_dir()
        self.clone_url = clone_url or f"https://github.com/{owner}/{name}.git"
        self.depth = depth
        self.git = git
        self.path = self.base_dir / f"{owner}__{name}-{_stamp()}"

    # -- context manager ----------------------------------------------------
    def __enter__(self) -> "Workdir":
        self._clone()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self._cleanup(remove_only=True)
        else:
            self._preserve_failure()
            self._cleanup(remove_only=True)
        return False  # never swallow exceptions

    # -- internals -----------------------------------------------------------
    def _clone(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        cmd = [self.git, "clone", "--quiet"]
        if self.depth:
            cmd += ["--depth", str(self.depth)]
        cmd += [self.clone_url, str(self.path)]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
                # Never inherit stdin: it stops git blocking on a credential
                # prompt, and on Windows the parent's stdin handle is not always
                # inheritable (e.g. under pytest's capture).
                stdin=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            # OSError covers a missing git executable (FileNotFoundError) as well
            # as Windows-specific process-spawn failures.
            self._cleanup(remove_only=True)
            raise WorkdirError(f"git clone failed for {self.clone_url}: {e}") from e
        if result.returncode != 0:
            self._cleanup(remove_only=True)
            raise WorkdirError(
                f"git clone failed (rc={result.returncode}) for {self.clone_url}: "
                f"{(result.stderr or result.stdout).strip()[:500]}"
            )

    def _preserve_failure(self) -> None:
        if not self.path.exists():
            return
        try:
            self.failures_dir.mkdir(parents=True, exist_ok=True)
            dest = self.failures_dir / self.path.name
            # Windows refuses symlink creation without Developer Mode/elevation,
            # so only request symlink preservation where it is supported.
            shutil.copytree(self.path, dest, symlinks=supports_symlink_copy())
        except Exception:  # noqa: BLE001 - preservation is best-effort
            pass

    def _cleanup(self, *, remove_only: bool) -> None:
        if self.path.exists():
            _rmtree(self.path)


def sweep_stale(dir_path: Path, max_age: timedelta = timedelta(hours=24)) -> list[Path]:
    """Delete workdir entries older than max_age (crashed-run leftovers).

    Works for both the active base dir and the failures dir.
    """
    if not dir_path.is_dir():
        return []
    now = datetime.now(timezone.utc)
    deleted: list[Path] = []
    for child in dir_path.iterdir():
        try:
            age = now - datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if age > max_age:
            try:
                if child.is_dir():
                    _rmtree(child)
                else:
                    child.unlink()
                deleted.append(child)
            except OSError:
                pass
    return deleted
