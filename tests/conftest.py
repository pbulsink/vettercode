"""Shared fixtures and fakes (no network, no LLM, no real clones)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """Isolated VETTERCODE_HOME."""
    h = tmp_path / "vc-home"
    h.mkdir()
    monkeypatch.setenv("VETTERCODE_HOME", str(h))
    return h


@pytest.fixture
def cfg(home):
    from vettercode.config import load_config

    return load_config(home)


def make_issue(
    number: int = 1,
    body: str | None = None,
    updated: str = "2026-09-10T00:00:00Z",
    title: str | None = None,
) -> dict:
    if body is None:
        body = "please investigate @vettercode"
    return {
        "number": number,
        "title": title or f"Issue {number}",
        "body": body,
        "updated_at": updated,
        "user": {"login": "me"},
    }


class FakeGithub:
    """Duck-typed stand-in for vettercode.github.GithubClient."""

    def __init__(
        self,
        repos: list[str] | None = None,
        issues: dict | None = None,
        comments: dict | None = None,
        user: str = "me",
        fail_repos: set | None = None,
    ):
        self.repos = repos or []
        self.issues = issues or {}  # (owner, name) -> list[dict]
        self.comments = comments or {}  # (owner, name, number) -> list[dict]
        self.user = user
        self.fail_repos = fail_repos or set()
        self.posted: list[tuple] = []
        self.calls: list[tuple] = []

    def get_user_login(self) -> str:
        return self.user

    def list_repos(self, username: str, include_forks: bool = True) -> list[str]:
        self.calls.append(("list_repos", username))
        return self.repos

    def list_issues(self, owner: str, name: str, since_iso: str | None = None) -> list[dict]:
        from vettercode.github import GithubError, parse_github_time

        self.calls.append(("list_issues", owner, name, since_iso))
        if (owner, name) in self.fail_repos:
            raise GithubError(f"boom {owner}/{name}", status=404)
        items = self.issues.get((owner, name), [])
        since = parse_github_time(since_iso)
        out = []
        for item in items:
            if "pull_request" in item:
                continue
            if since is not None:
                updated = parse_github_time(item.get("updated_at"))
                if updated is None or updated < since:
                    continue
            out.append(item)
        return out

    def list_comments(self, owner: str, name: str, number: int, limit: int | None = None) -> list[dict]:
        items = self.comments.get((owner, name, number), [])
        return items[:limit] if limit else items

    def post_comment(self, owner: str, name: str, number: int, body: str) -> dict:
        self.posted.append((owner, name, number, body))
        return {"id": 1}


class FakeAgentRun:
    """Callable stand-in for vettercode.agent.run_agent."""

    def __init__(self, outcome=None, raise_exc: Exception | None = None):
        from vettercode.agent import AgentOutcome

        self.outcome = outcome or AgentOutcome(
            exit_status="Submitted", summary="did the thing", pr_url=None, cost=0.0, n_steps=3
        )
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_exc:
            raise self.raise_exc
        return self.outcome


class FakeWorkdir:
    """Stand-in for vettercode.workspace.Workdir; path is set per test."""

    path: Path = Path("/nonexistent")

    def __init__(self, owner: str, name: str, **kwargs):
        self.owner = owner
        self.name = name

    def __enter__(self) -> "FakeWorkdir":
        return self

    def __exit__(self, *exc) -> bool:
        return False
