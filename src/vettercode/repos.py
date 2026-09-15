"""Repo list resolution: always derived live from the GitHub API.

No local repos.txt cache: the profile's repo list (optionally minus forks
and/or an `exclude_repos` config list) is fetched fresh every run, so newly
created or deleted repos are picked up automatically without manual editing.
"""

from __future__ import annotations

from .github import GithubClient


def resolve_repos(
    client: GithubClient,
    username: str,
    *,
    include_forks: bool = True,
    exclude: list[str] | None = None,
) -> list[str]:
    """Return the sorted, deduplicated `owner/name` repo list for `username`.

    `exclude` entries are matched against the full `owner/name` string or the
    bare repo name (case-sensitive), letting users opt specific repos out via
    `config.yaml` without maintaining a separate file.
    """
    exclude_set = set(exclude or [])
    fresh = client.list_repos(username, include_forks=include_forks)
    result = [
        repo
        for repo in fresh
        if repo not in exclude_set and repo.split("/", 1)[-1] not in exclude_set
    ]
    if not result:
        raise RuntimeError(
            f"no repos found for {username!r} (after applying include_forks="
            f"{include_forks!r} and exclude_repos={sorted(exclude_set)!r}); "
            "check the GitHub profile or config.yaml's exclude_repos list"
        )
    return sorted(set(result))
