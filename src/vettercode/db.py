"""SQLite state: repos (last checked), issues (last reviewed), reviews (audit)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    added_at TEXT NOT NULL,
    last_checked_at TEXT,
    PRIMARY KEY (owner, name)
);
CREATE TABLE IF NOT EXISTS issues (
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    number INTEGER NOT NULL,
    last_seen_updated_at TEXT,
    last_reviewed_at TEXT,
    status TEXT,
    PRIMARY KEY (owner, name, number)
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    action_taken TEXT,
    summary TEXT,
    pr_url TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_run ON reviews(run_id);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Thin wrapper over sqlite3. Use as a context manager."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # context manager
    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # repos
    def get_repo_last_checked(self, owner: str, name: str) -> str | None:
        row = self.conn.execute(
            "SELECT last_checked_at FROM repos WHERE owner = ? AND name = ?",
            (owner, name),
        ).fetchone()
        return row[0] if row and row[0] else None

    def set_repo_last_checked(self, owner: str, name: str, when_iso: str | None = None) -> None:
        when_iso = when_iso or _utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO repos(owner, name, added_at, last_checked_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(owner, name) DO UPDATE SET last_checked_at = excluded.last_checked_at
            """,
            (owner, name, when_iso, when_iso),
        )
        self.conn.commit()

    # issues
    def get_issue_last_reviewed(self, owner: str, name: str, number: int) -> str | None:
        row = self.conn.execute(
            "SELECT last_reviewed_at FROM issues WHERE owner = ? AND name = ? AND number = ?",
            (owner, name, number),
        ).fetchone()
        return row[0] if row and row[0] else None

    def record_issue(
        self,
        owner: str,
        name: str,
        number: int,
        last_seen_updated_at: str | None,
        status: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO issues(owner, name, number, last_seen_updated_at, last_reviewed_at, status)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner, name, number) DO UPDATE SET
                last_seen_updated_at = excluded.last_seen_updated_at,
                last_reviewed_at = excluded.last_reviewed_at,
                status = excluded.status
            """,
            (owner, name, number, last_seen_updated_at, _utc_now_iso(), status),
        )
        self.conn.commit()

    # reviews
    def record_review(
        self,
        run_id: str,
        owner: str,
        name: str,
        issue_number: int,
        action_taken: str,
        summary: str | None,
        pr_url: str | None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO reviews(run_id, owner, name, issue_number, action_taken, summary, pr_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, owner, name, issue_number, action_taken, summary, pr_url, _utc_now_iso()),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def reviews_for_run(self, run_id: str) -> list[tuple]:
        return self.conn.execute(
            "SELECT owner, name, issue_number, action_taken, pr_url FROM reviews WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
