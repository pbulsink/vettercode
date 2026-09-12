import pytest

from conftest import FakeGithub
from vettercode.repos import autopopulate, load_repos, save_repos


def test_load_repos_ignores_comments_and_blanks(tmp_path):
    path = tmp_path / "repos.txt"
    path.write_text("# comment\n\nme/one\n  me/two  \n# another\nme/three\n")
    assert load_repos(path) == ["me/one", "me/two", "me/three"]


def test_load_repos_missing_file(tmp_path):
    assert load_repos(tmp_path / "nope.txt") == []


def test_save_repos_sorts_dedupes(tmp_path):
    path = tmp_path / "repos.txt"
    save_repos(path, ["b/two", "a/one", "a/one"])
    assert path.read_text() == "a/one\nb/two\n"


def test_autopopulate_keeps_existing_list(home):
    (home / "repos.txt").write_text("me/keep\n")
    fake = FakeGithub(repos=["fresh/repo"])
    result = autopopulate(fake, "me", home / "repos.txt")
    assert result == ["me/keep"]  # existing list preserved, never re-fetched
    assert fake.calls == []  # no API call when list exists


def test_autopopulate_fetches_and_saves_when_empty(home):
    fake = FakeGithub(repos=["me/b", "me/a"])
    result = autopopulate(fake, "me", home / "repos.txt")
    assert result == ["me/a", "me/b"]
    assert (home / "repos.txt").read_text() == "me/a\nme/b\n"


def test_autopopulate_no_repos_raises(home):
    fake = FakeGithub(repos=[])
    with pytest.raises(RuntimeError, match="no repos"):
        autopopulate(fake, "me", home / "repos.txt")
