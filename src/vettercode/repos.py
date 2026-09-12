"""Repo list management (repos.txt) and first-run autopopulation."""

from __future__ import annotations

from pathlib import Path

from .github import GithubClient


def load_repos(path: Path) -> list[str]:
    """Read owner/name lines; ignores blanks and # comments. Order preserved."""
    if not path.exists():
        return []
    repos = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            repos.append(line)
    return repos


def save_repos(path: Path, repos: list[str]) -> None:
    """Persist sorted, deduplicated repo list with a trailing newline."""
    unique = sorted(set(repos))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(unique) + "\n")


def autopopulate(client: GithubClient, username: str, path: Path) -> list[str]:
    """Return the repo list, fetching from the GitHub profile on first run.

    An existing (non-empty) list is always preserved.
    """
    existing = load_repos(path)
    if existing:
        return existing
    fresh = client.list_repos(username)
    if not fresh:
        raise RuntimeError(
            f"autopopulate found no repos for {username!r}; "
            f"add owner/name lines to {path} manually"
        )
    save_repos(path, fresh)
    return load_repos(path)  # sorted, deduplicated
