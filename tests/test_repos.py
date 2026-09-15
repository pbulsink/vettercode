import pytest

from conftest import FakeGithub
from vettercode.repos import resolve_repos


def test_resolve_repos_sorts_dedupes():
    fake = FakeGithub(repos=["me/b", "me/a", "me/a"])
    assert resolve_repos(fake, "me") == ["me/a", "me/b"]


def test_resolve_repos_calls_client_each_time():
    fake = FakeGithub(repos=["me/a"])
    resolve_repos(fake, "me")
    resolve_repos(fake, "me")
    assert fake.calls.count(("list_repos", "me")) == 2  # always re-fetched, no cache


def test_resolve_repos_passes_include_forks_through():
    fake = FakeGithub(repos=["me/a"])
    resolve_repos(fake, "me", include_forks=False)
    assert fake.include_forks_calls == [False]


def test_resolve_repos_excludes_full_name():
    fake = FakeGithub(repos=["me/a", "me/b"])
    assert resolve_repos(fake, "me", exclude=["me/b"]) == ["me/a"]


def test_resolve_repos_excludes_bare_name():
    fake = FakeGithub(repos=["me/a", "me/b"])
    assert resolve_repos(fake, "me", exclude=["b"]) == ["me/a"]


def test_resolve_repos_no_repos_raises():
    fake = FakeGithub(repos=[])
    with pytest.raises(RuntimeError, match="no repos"):
        resolve_repos(fake, "me")


def test_resolve_repos_all_excluded_raises():
    fake = FakeGithub(repos=["me/a"])
    with pytest.raises(RuntimeError, match="no repos"):
        resolve_repos(fake, "me", exclude=["me/a"])
