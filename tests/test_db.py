import sqlite3

from vettercode.db import Database


def test_repo_last_checked_roundtrip(home):
    with Database(home / "state.db") as db:
        assert db.get_repo_last_checked("o", "n") is None
        db.set_repo_last_checked("o", "n", "2026-01-01T00:00:00+00:00")
        assert db.get_repo_last_checked("o", "n") == "2026-01-01T00:00:00+00:00"


def test_repo_upsert_keeps_single_row(home):
    with Database(home / "state.db") as db:
        db.set_repo_last_checked("o", "n", "2026-01-01T00:00:00+00:00")
        db.set_repo_last_checked("o", "n", "2026-02-01T00:00:00+00:00")
        assert db.get_repo_last_checked("o", "n") == "2026-02-01T00:00:00+00:00"
        count = db.conn.execute("SELECT count(*) FROM repos").fetchone()[0]
        assert count == 1
        added = db.conn.execute("SELECT added_at FROM repos").fetchone()[0]
        assert added == "2026-01-01T00:00:00+00:00"  # added_at preserved


def test_issue_record_and_last_reviewed(home):
    with Database(home / "state.db") as db:
        assert db.get_issue_last_reviewed("o", "n", 5) is None
        db.record_issue("o", "n", 5, "2026-03-01T00:00:00+00:00", "reviewed")
        assert db.get_issue_last_reviewed("o", "n", 5) is not None
        db.record_issue("o", "n", 5, "2026-03-02T00:00:00+00:00", "re-reviewed")
        count = db.conn.execute("SELECT count(*) FROM issues").fetchone()[0]
        assert count == 1


def test_review_insert_and_fetch(home):
    with Database(home / "state.db") as db:
        rowid = db.record_review(
            run_id="run-1",
            owner="o",
            name="n",
            issue_number=7,
            action_taken="pr-draft",
            summary="summary text",
            pr_url="https://github.com/o/n/pull/9",
        )
        assert rowid >= 1
        rows = db.reviews_for_run("run-1")
        assert rows == [("o", "n", 7, "pr-draft", "https://github.com/o/n/pull/9")]
        assert db.reviews_for_run("run-2") == []


def test_context_manager_closes_connection(home):
    db = Database(home / "state.db")
    db.__exit__(None, None, None)
    try:
        db.conn.execute("SELECT 1")
        closed = False
    except sqlite3.ProgrammingError:
        closed = True
    assert closed
