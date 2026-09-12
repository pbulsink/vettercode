"""Minimal GitHub REST API client (requests-based, injectable for tests)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

DEFAULT_BASE_URL = "https://api.github.com"
PER_PAGE = 100
MAX_PAGES = 10  # hard safety cap: 1000 items per list call


class GithubError(RuntimeError):
    """GitHub API failure with the HTTP status (when known)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def parse_github_time(value: str | None) -> datetime | None:
    """Parse GitHub ISO-8601 timestamps (with trailing Z) to aware datetimes."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class GithubClient:
    def __init__(
        self,
        token: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30,
        session: requests.Session | None = None,
    ):
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    # --- low level ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "vettercode",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        response = self.session.request(
            method,
            url,
            headers=self._headers(),
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise GithubError(
                f"GitHub API {method} {path} -> {response.status_code}: {response.text[:300]}",
                status=response.status_code,
            )
        if response.content:
            return response.json()
        return None

    def _get_pages(self, path: str) -> list[dict]:
        items: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            separator = "&" if "?" in path else "?"
            batch = self._request("GET", f"{path}{separator}per_page={PER_PAGE}&page={page}")
            if not isinstance(batch, list):
                raise GithubError(f"expected a list from {path}, got {type(batch).__name__}")
            items.extend(batch)
            if len(batch) < PER_PAGE:
                break
        return items

    # --- endpoints ---------------------------------------------------------
    def get_user_login(self) -> str:
        data = self._request("GET", "/user")
        return data["login"]

    def list_repos(self, username: str, include_forks: bool = True) -> list[str]:
        """List `owner/name` repos for a user, paginated. Forks included by default."""
        repos = self._get_pages(f"/users/{username}/repos")
        result = []
        for repo in repos:
            if not include_forks and repo.get("fork"):
                continue
            owner = repo.get("owner", {}).get("login") or username
            name = repo.get("name")
            if name:
                result.append(f"{owner}/{name}")
        return result

    def list_issues(self, owner: str, name: str, since_iso: str | None = None) -> list[dict]:
        """Open issues (PRs excluded). When `since_iso` is given, only issues
        updated at/after that instant are returned (client-side filter, since
        the Issues API has no `since` parameter)."""
        items = self._get_pages(f"/repos/{owner}/{name}/issues?state=open")
        since = parse_github_time(since_iso)
        result = []
        for item in items:
            if "pull_request" in item:
                continue
            if since is not None:
                updated = parse_github_time(item.get("updated_at"))
                if updated is None or updated < since:
                    continue
            result.append(item)
        return result

    def list_comments(self, owner: str, name: str, number: int, limit: int | None = None) -> list[dict]:
        items = self._get_pages(f"/repos/{owner}/{name}/issues/{number}/comments")
        return items[:limit] if limit else items

    def post_comment(self, owner: str, name: str, number: int, body: str) -> dict:
        return self._request(
            "POST", f"/repos/{owner}/{name}/issues/{number}/comments", {"body": body}
        ) or {}

    def utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()
