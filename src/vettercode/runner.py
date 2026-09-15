"""One pass of vettercode: window guard, repo loop, issue loop, agent runs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone

from .auth import get_token
from .config import Config
from .db import Database
from .filtering import has_vettercode_mention
from .github import GithubClient, GithubError, parse_github_time
from .logsetup import get_logger
from .policy import downgrade_mode, load_policy
from .repos import resolve_repos
from .workspace import Workdir, WorkdirError

PR_URL_FALLBACK = re.compile(r"https://github\.com/[\w.\-]+/[\w.\-]+/pull/\d+")


# --- small pure helpers -----------------------------------------------------


def in_window(now: datetime, tz, start: time, stop: time) -> bool:
    """True when local time `now` (converted to tz) is within start..stop.
    Handles windows that wrap midnight (e.g. 23:00 -> 07:00)."""
    t = now.astimezone(tz).time()
    if start <= stop:
        return start <= t <= stop
    return t >= start or t <= stop


def recently_reviewed(last_reviewed_iso: str | None, cooldown_days: int, now: datetime) -> bool:
    if not last_reviewed_iso:
        return False
    reviewed = parse_github_time(last_reviewed_iso)
    if reviewed is None:
        return False
    if reviewed.tzinfo is None:
        reviewed = reviewed.replace(tzinfo=timezone.utc)
    return now - reviewed < timedelta(days=cooldown_days)


def extract_pr_url(text: str | None) -> str | None:
    if not text:
        return None
    match = PR_URL_FALLBACK.search(text)
    return match.group(0) if match else None


# --- result types -----------------------------------------------------------


@dataclass
class IssueOutcome:
    owner: str
    name: str
    number: int
    mode: str
    status: str  # reviewed | skipped-cooldown | skipped-no-mention | dry-run | failed
    summary: str = ""
    pr_url: str | None = None


@dataclass
class RunResult:
    out_of_window: bool = False
    run_id: str = ""
    repos_checked: int = 0
    issues_seen: int = 0
    outcomes: list[IssueOutcome] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def processed(self) -> list[IssueOutcome]:
        return [o for o in self.outcomes if o.status == "reviewed"]


# --- the pass ---------------------------------------------------------------


def run_once(
    cfg: Config,
    *,
    now: datetime | None = None,
    ignore_window: bool = False,
    dry_run: bool = False,
    only_repo: str | None = None,
    token: str | None = None,
    client: GithubClient | None = None,
    agent_run=None,
) -> RunResult:
    """Execute one vettercode pass.

    Injectable for tests: `token`, `client`, and `agent_run` (a callable with
    the same signature as `vettercode.agent.run_agent`).
    """
    log = get_logger()
    now = now or datetime.now(timezone.utc)
    result = RunResult(run_id=now.isoformat())

    if not ignore_window and not in_window(now, cfg.timezone, cfg.start_time, cfg.stop_time):
        log.info(
            "outside window %s-%s (%s); not running",
            cfg.start_time, cfg.stop_time, cfg.timezone,
        )
        result.out_of_window = True
        return result

    if token is None:
        token = get_token()
    if client is None:
        client = GithubClient(token=token)

    username = cfg.github_username
    if not username:
        username = client.get_user_login()

    try:
        repos = resolve_repos(
            client, username, include_forks=cfg.include_forks, exclude=cfg.exclude_repos
        )
    except (GithubError, RuntimeError) as e:
        log.error("failed to resolve repo list for %s: %s", username, e)
        result.errors.append(f"repo list: {e}")
        return result
    if only_repo:
        only = only_repo.strip()
        repos = [r for r in repos if r == only or r.endswith(f"/{only}")]
        if not repos:
            result.errors.append(f"repo {only_repo!r} not found for user {username!r}")

    log.info(
        "vettercode starting: user=%s repos=%d mode=%s dry_run=%s",
        username, len(repos), cfg.agent_mode, dry_run,
    )

    with Database(cfg.db_path) as db:
        for repo_str in repos:
            result.repos_checked += 1
            if "/" not in repo_str:
                result.errors.append(f"skipping malformed repo entry {repo_str!r}")
                continue
            owner, name = repo_str.split("/", 1)

            since = db.get_repo_last_checked(owner, name)
            try:
                issues = client.list_issues(owner, name, since_iso=since)
            except GithubError as e:
                log.error("failed to fetch issues for %s/%s: %s", owner, name, e)
                result.errors.append(f"{owner}/{name}: {e}")
                continue

            result.issues_seen += len(issues)
            for issue in issues:
                if len(result.processed) >= cfg.max_issues_per_night:
                    log.info("max_issues_per_night=%d reached; stopping", cfg.max_issues_per_night)
                    db.set_repo_last_checked(owner, name)
                    return result

                number = issue.get("number")
                body = issue.get("body") or ""
                comments = []
                try:
                    comments = client.list_comments(
                        owner, name, number, limit=cfg.max_comment_context
                    )
                except GithubError as e:
                    log.warning("could not fetch comments for %s/%s#%s: %s", owner, name, number, e)

                if not has_vettercode_mention(body, comments):
                    result.outcomes.append(
                        IssueOutcome(owner, name, number, cfg.agent_mode, "skipped-no-mention")
                    )
                    continue

                last_reviewed = db.get_issue_last_reviewed(owner, name, number)
                if recently_reviewed(last_reviewed, cfg.review_cooldown_days, now):
                    log.info("%s/%s#%s reviewed recently; skipping (cooldown)", owner, name, number)
                    result.outcomes.append(
                        IssueOutcome(owner, name, number, cfg.agent_mode, "skipped-cooldown")
                    )
                    continue

                if dry_run:
                    log.info("[dry-run] would process %s/%s#%s", owner, name, number)
                    result.outcomes.append(
                        IssueOutcome(owner, name, number, cfg.agent_mode, "dry-run")
                    )
                    db.record_issue(owner, name, number, issue.get("updated_at"), "dry-run")
                    continue

                _process_issue(
                    cfg=cfg,
                    db=db,
                    client=client,
                    token=token,
                    agent_run=agent_run,
                    run_id=result.run_id,
                    owner=owner,
                    name=name,
                    issue=issue,
                    comments=comments,
                    result=result,
                )

            db.set_repo_last_checked(owner, name)

    log.info(
        "run complete: repos=%d issues_seen=%d processed=%d errors=%d",
        result.repos_checked, result.issues_seen, len(result.processed), len(result.errors),
    )
    return result


def _process_issue(
    *,
    cfg: Config,
    db: Database,
    client: GithubClient,
    token: str | None,
    agent_run,
    run_id: str,
    owner: str,
    name: str,
    issue: dict,
    comments: list[dict],
    result: RunResult,
) -> None:
    log = get_logger()
    number = issue.get("number")
    title = issue.get("title", "")
    mode = cfg.agent_mode

    if agent_run is None:  # default wiring, lazy import keeps dry-runs fast
        from .agent import run_agent as agent_run

    try:
        with Workdir(owner, name) as workdir:
            policy = load_policy(workdir.path)
            effective_mode, reason = downgrade_mode(mode, policy)
            if reason:
                log.info("%s/%s: mode %s downgraded to %s (%s)", owner, name, mode, effective_mode, reason)

            from .agent import build_task_prompt

            task = build_task_prompt(
                owner, name, number, title,
                issue.get("body") or "", comments,
                effective_mode, max_comments=cfg.max_comment_context,
            )

            if effective_mode == "comment":
                outcome = _run_comment_mode(
                    cfg=cfg, db=db, client=client, token=token, agent_run=agent_run,
                    run_id=run_id, owner=owner, name=name, number=number,
                    task=task, workdir=workdir, result=result,
                )
            else:
                outcome = _run_default_mode(
                    mode=effective_mode,
                    cfg=cfg, db=db, token=token, agent_run=agent_run,
                    run_id=run_id, owner=owner, name=name, number=number,
                    updated_at=issue.get("updated_at"), task=task, workdir=workdir, result=result,
                )

            result.outcomes.append(outcome)
    except WorkdirError as e:
        log.error("workdir failed for %s/%s#%s: %s", owner, name, number, e)
        result.errors.append(f"{owner}/{name}#{number}: workdir: {e}")
        result.outcomes.append(IssueOutcome(owner, name, number, mode, "failed", summary=str(e)[:500]))
        db.record_issue(owner, name, number, issue.get("updated_at"), "failed")
        return

    db.set_repo_last_checked(owner, name)


def _run_default_mode(*, mode, cfg, db, token, agent_run, run_id, owner, name, number, updated_at, task, workdir, result) -> IssueOutcome:
    """observe and pr-draft modes: single agent run in the workdir."""
    log = get_logger()
    outcome = agent_run(
        task=task,
        cfg=cfg,
        workdir=workdir.path,
        mode=mode,
        token=token,
        branch_prefix=cfg.pr_branch_prefix,
    )

    pr_url = (outcome.pr_url or _find_draft_pr(workdir.path)) if mode == "pr-draft" else None

    db.record_issue(owner, name, number, updated_at, f"{mode}:{outcome.exit_status}")
    db.record_review(
        run_id=run_id,
        owner=owner,
        name=name,
        issue_number=number,
        action_taken=mode,
        summary=outcome.summary[:4000],
        pr_url=pr_url,
    )
    log.info(
        "processed %s/%s#%s mode=%s exit=%s pr=%s",
        owner, name, number, mode, outcome.exit_status, pr_url or "-",
    )
    return IssueOutcome(
        owner, name, number, mode, "reviewed",
        summary=outcome.summary[:2000], pr_url=pr_url,
    )


def _run_comment_mode(*, cfg, db, client, token, agent_run, run_id, owner, name, number, task, workdir, result) -> IssueOutcome:
    """comment mode: agent investigates read-only in the clone; vettercode
    posts the finding as an issue comment via the API (never the agent)."""
    log = get_logger()
    outcome = agent_run(
        task=task,
        cfg=cfg,
        workdir=workdir.path,
        mode="observe",
        token=token,
        branch_prefix=cfg.pr_branch_prefix,
    )
    comment_body = f"🤖 vettercode findings:\n\n{outcome.summary[:3500]}"
    try:
        client.post_comment(owner, name, number, comment_body)
        posted = True
    except GithubError as e:
        log.error("failed to post comment on %s/%s#%s: %s", owner, name, number, e)
        result.errors.append(f"{owner}/{name}#{number}: comment: {e}")
        posted = False

    db.record_issue(owner, name, number, None, f"comment:{outcome.exit_status}:{'posted' if posted else 'failed'}")
    db.record_review(
        run_id=run_id,
        owner=owner,
        name=name,
        issue_number=number,
        action_taken="comment" if posted else "comment-failed",
        summary=outcome.summary[:4000],
        pr_url=None,
    )
    log.info("commented %s/%s#%s posted=%s", owner, name, number, posted)
    return IssueOutcome(
        owner, name, number, "comment",
        "reviewed" if posted else "failed",
        summary=outcome.summary[:2000],
    )


def _find_draft_pr(workdir) -> str | None:
    """Best-effort: locate an open draft PR from this workdir's pushed branch."""
    import subprocess

    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--draft", "--json", "url,headRefName"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            return None
        import json

        for pr in json.loads(result.stdout or "[]"):
            url = pr.get("url")
            if url:
                return url
    except Exception:  # noqa: BLE001
        pass
    return None
