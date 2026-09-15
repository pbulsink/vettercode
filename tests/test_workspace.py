import os
import stat
import subprocess
from datetime import timedelta

import pytest

from vettercode.workspace import Workdir, WorkdirError, sweep_stale


def _run(cmd: list[str], cwd=None, env=None):
    # stdin=DEVNULL: under pytest's capture the parent's stdin handle is not
    # inheritable on Windows, which breaks process spawning.
    return subprocess.run(
        cmd, cwd=cwd, env=env, check=True, capture_output=True, text=True,
        stdin=subprocess.DEVNULL,
    )


@pytest.fixture
def source_repo(tmp_path):
    """A tiny local git repo to clone from (no network needed)."""
    src = tmp_path / "source"
    src.mkdir()
    # Merge rather than replace: on Windows a bare env without SystemRoot/PATH
    # breaks git.
    env = {
        **os.environ,
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


def test_rmtree_removes_read_only_files(tmp_path):
    """Git marks pack files read-only; on Windows that blocks deletion."""
    from vettercode.workspace import _rmtree

    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    locked = tree / "sub" / "locked.txt"
    locked.write_text("x")
    os.chmod(locked, stat.S_IREAD)

    _rmtree(tree)
    assert not tree.exists()


def test_default_base_dir_follows_tempdir(monkeypatch, tmp_path):
    from vettercode import workspace as workspace_mod

    monkeypatch.setattr(workspace_mod.tempfile, "gettempdir", lambda: str(tmp_path))
    assert workspace_mod.default_base_dir() == tmp_path / "vettercode"


def test_default_failures_dir_uses_platform_cache(monkeypatch, tmp_path):
    from vettercode import workspace as workspace_mod

    monkeypatch.setattr(workspace_mod, "cache_home", lambda: tmp_path / "cache")
    assert workspace_mod.default_failures_dir() == tmp_path / "cache" / "failures"


def test_preserve_failure_skips_symlinks_on_windows(tmp_path, source_repo, monkeypatch):
    """copytree(symlinks=True) fails on Windows without Developer Mode."""
    from vettercode import workspace as workspace_mod

    seen: dict = {}
    real_copytree = workspace_mod.shutil.copytree

    def spy(src, dst, **kwargs):
        seen.update(kwargs)
        return real_copytree(src, dst, **kwargs)

    monkeypatch.setattr(workspace_mod, "supports_symlink_copy", lambda: False)
    monkeypatch.setattr(workspace_mod.shutil, "copytree", spy)

    wd = Workdir(
        "o", "n", base_dir=tmp_path / "base", failures_dir=tmp_path / "failures",
        clone_url=str(source_repo),
    )
    with pytest.raises(RuntimeError):
        with wd:
            raise RuntimeError("boom")
    assert seen["symlinks"] is False
