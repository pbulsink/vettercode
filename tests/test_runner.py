from datetime import datetime, time, timezone

import pytest

from conftest import FakeAgentRun, FakeGithub, FakeWorkdir, make_issue
from vettercode import runner as runner_mod
from vettercode.config import override
from vettercode.db import Database
from vettercode.runner import in_window, recently_reviewed, run_once

# 2026-09-11 01:00 UTC == 21:00 America/Toronto -> inside the default 20:00-06:00 window
INSIDE_WINDOW = datetime(2026, 9, 11, 1, 0, tzinfo=timezone.utc)
# 2026-09-11 16:00 UTC == 12:00 America/Toronto -> outside
OUTSIDE_WINDOW = datetime(2026, 9, 11, 16, 0, tzinfo=timezone.utc)


@pytest.fixture
def cfg(cfg):
    return override(cfg, agent_mode="observe")


@pytest.fixture
def fake_workdir(tmp_path, monkeypatch):
    wd = tmp_path / "wd"
    wd.mkdir()
    FakeWorkdir.path = wd
    monkeypatch.setattr(runner_mod, "Workdir", FakeWorkdir)
    return wd


def test_in_window_inside_and_outside(cfg):
    assert in_window(INSIDE_WINDOW, cfg.timezone, cfg.start_time, cfg.stop_time)
    assert not in_window(OUTSIDE_WINDOW, cfg.timezone, cfg.start_time, cfg.stop_time)


def test_in_window_wraparound():
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Toronto")
    start, stop = time(23, 0), time(7, 0)
    night = datetime(2026, 9, 11, 5, 0, tzinfo=timezone.utc)  # 01:00 EDT
    day = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)  # 08:00 EDT
    assert in_window(night, tz, start, stop)
    assert not in_window(day, tz, start, stop)


def test_recently_reviewed():
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    assert recently_reviewed("2026-09-10T00:00:00+00:00", 7, now)
    assert not recently_reviewed("2026-08-01T00:00:00+00:00", 7, now)
    assert not recently_reviewed(None, 7, now)
    assert not recently_reviewed("garbage", 7, now)
    # naive timestamps treated as UTC
    assert recently_reviewed("2026-09-10T00:00:00", 7, now)


def test_out_of_window_returns_early(cfg, fake_workdir):
    result = run_once(cfg, now=OUTSIDE_WINDOW, client=FakeGithub(repos=["me/a"]))
    assert result.out_of_window
    assert result.repos_checked == 0
    assert result.outcomes == []


def test_ignore_window_forces_run(cfg, fake_workdir):
    fake = FakeGithub(repos=["me/a"], issues={("me", "a"): [make_issue()]})
    agent = FakeAgentRun()
    result = run_once(
        cfg, now=OUTSIDE_WINDOW, ignore_window=True, client=fake, agent_run=agent
    )
    assert not result.out_of_window
    assert len(result.processed) == 1
    assert len(agent.calls) == 1


def test_issues_without_mention_are_skipped(cfg, fake_workdir):
    fake = FakeGithub(
        repos=["me/a"],
        issues={("me", "a"): [make_issue(body="no mention here")]},
    )
    agent = FakeAgentRun()
    result = run_once(cfg, now=INSIDE_WINDOW, client=fake, agent_run=agent)
    assert result.outcomes[0].status == "skipped-no-mention"
    assert agent.calls == []


def test_mention_in_comment_triggers_processing(cfg, fake_workdir):
    fake = FakeGithub(
        repos=["me/a"],
        issues={("me", "a"): [make_issue(number=1, body="plain body")]},
        comments={("me", "a", 1): [{"user": {"login": "x"}, "body": "can you try @VetterCode?"}]},
    )
    agent = FakeAgentRun()
    result = run_once(cfg, now=INSIDE_WINDOW, client=fake, agent_run=agent)
    assert len(result.processed) == 1
    assert "plain body" in agent.calls[0]["task"]


def test_recently_reviewed_issues_are_skipped(cfg, fake_workdir):
    fake = FakeGithub(repos=["me/a"], issues={("me", "a"): [make_issue()]})
    agent = FakeAgentRun()
    with Database(cfg.db_path) as db:
        db.record_issue("me", "a", 1, None, "reviewed")
    result = run_once(cfg, now=INSIDE_WINDOW, client=fake, agent_run=agent)
    assert result.outcomes[0].status == "skipped-cooldown"
    assert agent.calls == []


def test_max_issues_per_night_caps_processing(cfg, fake_workdir):
    cfg = override(cfg, max_issues_per_night=2)
    issues = [make_issue(number=i) for i in range(1, 5)]
    fake = FakeGithub(repos=["me/a"], issues={("me", "a"): issues})
    agent = FakeAgentRun()
    result = run_once(cfg, now=INSIDE_WINDOW, client=fake, agent_run=agent)
    assert len(result.processed) == 2
    assert len(result.outcomes) == 2  # third never reached


def test_repo_failure_is_isolated(cfg, fake_workdir):
    fake = FakeGithub(
        repos=["me/good", "me/bad"],
        issues={("me", "good"): [make_issue()]},
        fail_repos={("me", "bad")},
    )
    agent = FakeAgentRun()
    result = run_once(cfg, now=INSIDE_WINDOW, client=fake, agent_run=agent)
    assert len(result.processed) == 1
    assert any("me/bad" in e for e in result.errors)


def test_dry_run_never_invokes_agent(cfg, fake_workdir, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("Workdir must not be used in dry-run")

    monkeypatch.setattr(runner_mod, "Workdir", _boom)
    fake = FakeGithub(repos=["me/a"], issues={("me", "a"): [make_issue()]})
    result = run_once(cfg, now=INSIDE_WINDOW, client=fake, dry_run=True)
    assert result.outcomes[0].status == "dry-run"


def test_repos_resolved_live_every_run(cfg, home, fake_workdir):
    fake = FakeGithub(
        repos=["me/a"],
        issues={("me", "a"): [make_issue()]},
    )
    result = run_once(cfg, now=INSIDE_WINDOW, client=fake, agent_run=FakeAgentRun())
    assert len(result.processed) == 1
    assert fake.calls.count(("list_repos", "me")) == 1


def test_exclude_repos_config_filters_list(cfg, fake_workdir):
    cfg = override(cfg, exclude_repos=["me/b"])
    fake = FakeGithub(repos=["me/a", "me/b"], issues={("me", "a"): [make_issue()]})
    result = run_once(cfg, now=INSIDE_WINDOW, client=fake, agent_run=FakeAgentRun())
    assert result.repos_checked == 1


def test_repo_list_failure_is_reported_not_fatal(cfg, fake_workdir):
    class BoomGithub(FakeGithub):
        def list_repos(self, username, include_forks=True):
            from vettercode.github import GithubError

            raise GithubError("rate limited", status=403)

    result = run_once(cfg, now=INSIDE_WINDOW, client=BoomGithub())
    assert result.repos_checked == 0
    assert any("rate limited" in e for e in result.errors)


def test_comment_fetch_failure_does_not_block_processing(cfg, fake_workdir):
    class FlakyComments(FakeGithub):
        def list_comments(self, owner, name, number, limit=None):
            from vettercode.github import GithubError

            raise GithubError("comments unavailable", status=500)

    fake = FlakyComments(
        repos=["me/a"], issues={("me", "a"): [make_issue(body="please @vettercode help")]}
    )
    agent = FakeAgentRun()
    result = run_once(cfg, now=INSIDE_WINDOW, client=fake, agent_run=agent)
    assert len(result.processed) == 1
    fake = FakeGithub(repos=["me/a", "me/b"])
    result = run_once(
        cfg, now=INSIDE_WINDOW, client=fake, agent_run=FakeAgentRun(), only_repo="me/b"
    )
    assert result.repos_checked == 1
    assert ("list_issues", "me", "a", None) not in fake.calls


def test_only_repo_not_in_list_is_an_error(cfg, fake_workdir):
    fake = FakeGithub(repos=["me/a"])
    result = run_once(cfg, now=INSIDE_WINDOW, client=fake, only_repo="other/repo")
    assert result.repos_checked == 0
    assert any("other/repo" in e for e in result.errors)


def test_malformed_repo_entry_skipped(cfg, fake_workdir):
    fake = FakeGithub(repos=["no-slash-here"])
    result = run_once(cfg, now=INSIDE_WINDOW, client=fake)
    assert result.repos_checked == 1
    assert any("malformed" in e for e in result.errors)


def test_comment_mode_posts_via_api(cfg, fake_workdir):
    cfg = override(cfg, agent_mode="comment")
    fake = FakeGithub(repos=["me/a"], issues={("me", "a"): [make_issue()]})
    agent = FakeAgentRun()
    result = run_once(cfg, now=INSIDE_WINDOW, client=fake, agent_run=agent)
    assert len(result.processed) == 1
    assert agent.calls[0]["mode"] == "observe"  # agent stays read-only
    assert len(fake.posted) == 1
    owner, name, number, body = fake.posted[0]
    assert (owner, name, number) == ("me", "a", 1)
    assert body.startswith("🤖 vettercode findings:")
    assert "did the thing" in body


def test_policy_downgrades_pr_draft_to_comment(cfg, fake_workdir):
    cfg = override(cfg, agent_mode="pr-draft")
    (fake_workdir / "AGENTS.md").write_text("allow_pr: false\n")
    fake = FakeGithub(repos=["me/a"], issues={("me", "a"): [make_issue()]})
    agent = FakeAgentRun()
    result = run_once(cfg, now=INSIDE_WINDOW, client=fake, agent_run=agent)
    # Downgraded: agent runs read-only, finding posted as comment instead of PR.
    assert agent.calls[0]["mode"] == "observe"
    assert len(fake.posted) == 1
    assert result.outcomes[0].mode == "comment"


def test_pr_draft_mode_finds_pr_url_from_agent(cfg, fake_workdir):
    cfg = override(cfg, agent_mode="pr-draft")
    from vettercode.agent import AgentOutcome

    agent = FakeAgentRun(
        AgentOutcome(
            exit_status="Submitted",
            summary="PR opened",
            pr_url="https://github.com/me/a/pull/31",
            cost=0.5,
            n_steps=9,
        )
    )
    fake = FakeGithub(repos=["me/a"], issues={("me", "a"): [make_issue()]})
    result = run_once(cfg, now=INSIDE_WINDOW, client=fake, agent_run=agent)
    assert result.processed[0].pr_url == "https://github.com/me/a/pull/31"
    assert agent.calls[0]["mode"] == "pr-draft"


def test_workdir_failure_recorded_not_fatal(cfg, home, monkeypatch):
    from vettercode.workspace import WorkdirError

    class ExplodingWorkdir:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            raise WorkdirError("clone exploded")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(runner_mod, "Workdir", ExplodingWorkdir)
    fake = FakeGithub(repos=["me/a", "me/b"], issues={("me", "a"): [make_issue()]})
    result = run_once(cfg, now=INSIDE_WINDOW, client=fake, agent_run=FakeAgentRun())
    assert result.outcomes[0].status == "failed"
    assert any("clone exploded" in e for e in result.errors)
    # the second repo still processed (loop continues)
    assert result.repos_checked == 2


def test_state_last_checked_recorded(cfg, fake_workdir):
    fake = FakeGithub(repos=["me/a"], issues={("me", "a"): [make_issue()]})
    run_once(cfg, now=INSIDE_WINDOW, client=fake, agent_run=FakeAgentRun())
    with Database(cfg.db_path) as db:
        assert db.get_repo_last_checked("me", "a") is not None
        assert db.get_issue_last_reviewed("me", "a", 1) is not None
        assert db.reviews_for_run  # table populated below
    with Database(cfg.db_path) as db:
        run_rows = db.conn.execute("SELECT count(*) FROM reviews").fetchone()[0]
        assert run_rows == 1
