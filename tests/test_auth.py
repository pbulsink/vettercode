import subprocess

import pytest

from vettercode import auth


def _completed(rc: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args="gh", returncode=rc, stdout=stdout, stderr=stderr)


def test_get_token_ok(monkeypatch):
    monkeypatch.setattr(auth.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        auth.subprocess, "run", lambda *a, **k: _completed(0, stdout="gho_abc123\n")
    )
    assert auth.get_token() == "gho_abc123"


def test_get_token_not_authenticated(monkeypatch):
    monkeypatch.setattr(auth.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        auth.subprocess, "run", lambda *a, **k: _completed(1, stderr="gh is not authenticated\n")
    )
    with pytest.raises(auth.AuthError) as excinfo:
        auth.get_token()
    assert "gh auth login" in excinfo.value.hint


def test_get_token_gh_missing(monkeypatch):
    monkeypatch.setattr(auth.shutil, "which", lambda _: None)
    with pytest.raises(auth.AuthError) as excinfo:
        auth.get_token()
    assert "brew install gh" in excinfo.value.hint


def test_get_token_empty_output(monkeypatch):
    monkeypatch.setattr(auth.shutil, "which", lambda _: "/usr/bin/gh")

    def fake_run(*a, **k):
        args = a[0] if a else k.get("args", [])
        if "token" in args:
            return _completed(0, stdout="\n")
        return _completed(0)

    monkeypatch.setattr(auth.subprocess, "run", fake_run)
    with pytest.raises(auth.AuthError):
        auth.get_token()


def test_auth_status_reports(monkeypatch):
    monkeypatch.setattr(auth.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        auth.subprocess, "run", lambda *a, **k: _completed(0, stdout="Logged in\n")
    )
    ok, message = auth.auth_status()
    assert ok and "Logged in" in message


def test_get_token_token_command_fails(monkeypatch):
    monkeypatch.setattr(auth.shutil, "which", lambda _: "/usr/bin/gh")

    def fake_run(*a, **k):
        args = a[0] if a else k.get("args", [])
        if "token" in args:
            return _completed(1, stderr="token command failed\n")
        return _completed(0, stdout="Logged in\n")

    monkeypatch.setattr(auth.subprocess, "run", fake_run)
    with pytest.raises(auth.AuthError, match="gh auth token.*failed"):
        auth.get_token()


def test_auth_status_gh_missing(monkeypatch):
    monkeypatch.setattr(auth.shutil, "which", lambda _: None)
    ok, message = auth.auth_status()
    assert not ok
    assert "not installed" in message
