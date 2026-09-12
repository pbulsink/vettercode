import os
import subprocess
from datetime import timedelta

import pytest

from vettercode.workspace import Workdir, WorkdirError, sweep_stale


def _run(cmd: list[str], cwd=None, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, check=True, capture_output=True, text=True)


@pytest.fixture
def source_repo(tmp_path):
    """A tiny local git repo to clone from (no network needed)."""
    src = tmp_path / "source"
    src.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    _run(["git", "init", "-q", "-b", "main"], cwd=src)
    (src / "README.md").write_text("# source\n")
    _run(["git", "add", "-A"], cwd=src, env=env)
    _run(["git", "commit", "-q", "-m", "init"], cwd=src, env=env)
    return src


def test_clone_success_and_cleanup_on_exit(tmp_path, source_repo):
    base = tmp_path / "base"
    failures = tmp_path / "failures"
    wd = Workdir("o", "n", base_dir=base, failures_dir=failures, clone_url=str(source_repo))
    with wd as entered:
        assert (entered.path / "README.md").is_file()
        assert (entered.path / ".git").is_dir()
        assert entered.path.parent == base
    assert not wd.path.exists()
    assert not failures.exists()


def test_failure_is_preserved_then_original_removed(tmp_path, source_repo):
    base = tmp_path / "base"
    failures = tmp_path / "failures"
    wd = Workdir("o", "n", base_dir=base, failures_dir=failures, clone_url=str(source_repo))
    with pytest.raises(RuntimeError, match="boom"):
        with wd:
            raise RuntimeError("boom")
    assert not wd.path.exists()
    entries = list(failures.iterdir())
    assert len(entries) == 1
    assert (entries[0] / "README.md").is_file()


def test_clone_failure_raises_and_cleans(tmp_path):
    base = tmp_path / "base"
    wd = Workdir(
        "o", "n", base_dir=base, failures_dir=tmp_path / "failures",
        clone_url=str(tmp_path / "does-not-exist"),
    )
    with pytest.raises(WorkdirError):
        with wd:
            pass
    assert not wd.path.exists()


def test_sweep_stale_deletes_only_old(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    old = base / "o__n-old"
    old.mkdir()
    (old / "f.txt").write_text("x")
    import time

    ts = time.time() - 3 * 24 * 3600
    os.utime(old, (ts, ts))

    fresh = base / "o__n-fresh"
    fresh.mkdir()

    deleted = sweep_stale(base, max_age=timedelta(hours=24))
    assert old in deleted
    assert not old.exists()
    assert fresh.exists()


def test_sweep_stale_missing_dir(tmp_path):
    assert sweep_stale(tmp_path / "nope") == []
