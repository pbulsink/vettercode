"""GitHub authentication via the `gh` CLI.

`gh` is the single source of auth: it provides the API token for vettercode
and the `gh` commands the agent uses inside workdirs.
"""

from __future__ import annotations

import shutil
import subprocess

from .platforms import gh_install_hint


class AuthError(RuntimeError):
    """Raised when gh is missing or not authenticated."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


def _run_gh(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        stdin=subprocess.DEVNULL,  # never let gh block on an interactive prompt
    )


def gh_installed() -> bool:
    return shutil.which("gh") is not None


def auth_status() -> tuple[bool, str]:
    """Return (ok, message) for the current gh authentication state."""
    if not gh_installed():
        return False, "gh CLI is not installed"
    result = _run_gh(["auth", "status"])
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, output.strip()


def get_token() -> str:
    """Return the active gh token, or raise AuthError with setup guidance."""
    ok, message = auth_status()
    if not ok:
        hint = "Run: gh auth login" if "not installed" not in message else gh_install_hint()
        raise AuthError(f"GitHub auth not ready: {message}", hint=hint)
    result = _run_gh(["auth", "token"])
    if result.returncode != 0:
        raise AuthError(
            f"`gh auth token` failed: {(result.stderr or result.stdout).strip()}",
            hint="Run: gh auth login",
        )
    token = result.stdout.strip()
    if not token:
        raise AuthError("`gh auth token` returned an empty token", hint="Run: gh auth login")
    return token
