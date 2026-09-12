import pytest

from conftest import FakeGithub, make_issue
from vettercode.auth import AuthError
from vettercode.cli import main
from vettercode.runner import RunResult


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "vettercode 0.1.0" in capsys.readouterr().out


def test_help_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--dry-run" in out and "--mode" in out


def test_flags_are_forwarded_to_run_once(home, monkeypatch):
    captured = {}

    def fake_run_once(cfg, **kwargs):
        captured["cfg"] = cfg
        captured.update(kwargs)
        return RunResult()

    monkeypatch.setattr("vettercode.cli.run_once", fake_run_once)
    rc = main(["--dry-run", "--ignore-window", "--mode", "comment", "--repo", "me/repo"])
    assert rc == 0
    assert captured["dry_run"] is True
    assert captured["ignore_window"] is True
    assert captured["only_repo"] == "me/repo"
    assert captured["cfg"].agent_mode == "comment"
    assert captured["cfg"].home == home


def test_auth_error_returns_2(home, monkeypatch):
    def boom(*a, **k):
        raise AuthError("not authenticated", hint="Run: gh auth login")

    monkeypatch.setattr("vettercode.cli.run_once", boom)
    assert main(["--ignore-window"]) == 2


def test_out_of_window_still_zero(home, monkeypatch):
    monkeypatch.setattr("vettercode.cli.run_once", lambda *a, **k: RunResult(out_of_window=True))
    assert main([]) == 0


def test_full_dry_run_against_fake_api(home, monkeypatch):
    """End-to-end CLI pass with a stubbed client (no network, no agent)."""
    import vettercode.cli as cli_mod

    fake = FakeGithub(
        repos=["me/a"],
        issues={("me", "a"): [make_issue(), make_issue(number=2, body="quiet issue")]},
    )
    monkeypatch.setattr(cli_mod, "run_once", lambda cfg, **kw: None)  # not used; real below

    import vettercode.runner as runner_mod

    real_run_once = runner_mod.run_once

    def patched_run_once(cfg, **kwargs):
        kwargs.setdefault("client", fake)
        return real_run_once(cfg, **kwargs)

    monkeypatch.setattr(cli_mod, "run_once", patched_run_once)
    rc = main(["--dry-run", "--ignore-window"])
    assert rc == 0
