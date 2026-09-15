# Vettercode — Design

Status: **implemented & tested** (v0.1.0). All components below are in
`src/vettercode/`, covered by an offline test suite in `tests/`.

## Goals

- Run a nightly pass over the user's GitHub repos, acting only on issues that
  mention `@vettercode`.
- Use a local LM Studio model via mini-swe-agent (v2 library API) — no cloud
  inference, no API keys.
- Three escalating modes (observe → comment → pr-draft) with per-repo
  downgrade policy from `AGENTS.md`.
- Never modify the user's working directories; temp clones live in `$TMPDIR`.
- Single config/state home: `~/.config/vettercode/`.
- Auth via `gh` CLI; if `gh` is missing or unauthenticated, fail fast with a
  setup hint (CLI exit code 2).

## Architecture

```
cli.py        argparse, exit codes, logging setup
  └─> runner.py   run_once(): window guard → repo loop → issue loop
        ├─ auth.py      gh auth check / token (single source of truth)
        ├─ repos.py     resolve_repos(): live GitHub API list + exclude_repos filter
        ├─ github.py    GithubClient (requests, pagination, since-filter)
        ├─ filtering.py @vettercode mention (body + comments, case-insensitive)
        ├─ db.py        SQLite: repos/issues/reviews (cooldowns, last-checked)
        ├─ policy.py    AGENTS.md markers → mode downgrade
        ├─ workspace.py Workdir (TMPDIR clone; failure → ~/.cache/vettercode)
        └─ agent.py     mini-swe-agent wiring (DefaultAgent/LitellmModel/LocalEnvironment)
config.py     flat config.yaml + dataclass; null ≠ override
logsetup.py   daily log files, 30-day retention
```

### Pass pipeline (runner.py)

1. **Window guard** — local time (config timezone) within `start_time`–
   `stop_time` (wraps midnight); `--ignore-window` bypasses.
2. **Auth** — `gh` token; on failure `AuthError` → CLI prints hint, exit 2.
3. **Repos** — `resolve_repos()` lists the profile's repos live from the
   GitHub API every run (`include_forks`, `exclude_repos` from config
   applied); no local cache file. `--repo` filters the resolved list.
4. **Per repo** — `list_issues(state=open, since=last_checked)`; PRs excluded;
   per-issue:
   - mention filter (body or any comment),
   - cooldown skip (last review within `review_cooldown_days`),
   - `--dry-run` stop (records intent, no agent),
   - `max_issues_per_night` cap.
5. **Per issue** — clone workdir → load `AGENTS.md` policy → downgrade mode →
   build task prompt (issue + up to `max_comment_context` comments) → run agent.
6. **Post-run** — `state.db` updated (last-checked, review rows); logs rotated.

### Modes & policy

- `observe` (rank 0): read-only investigation; summary returned to the log.
- `comment` (rank 1): agent runs read-only inside the clone; **vettercode**
  posts one finding as an issue comment via the GitHub API (the agent never
  writes to GitHub directly).
- `pr-draft` (rank 2): agent may implement, commit to `vettercode/*`, push,
  `gh pr create --draft`. PR URL extracted from the submission text, with a
  `gh pr list --draft` fallback. **Never merges.**
- `AGENTS.md` markers (case-insensitive): `allow_pr: false` / `no-pr`,
  `allow_comment: false` / `no comments` → downgrade one step at a time.

### Agent wiring (agent.py)

- `DefaultAgent(model=LitellmModel, env=LocalEnvironment(cwd=workdir),
  system_template, instance_template, step_limit, wall_time_limit_seconds)`.
- Model name is prefixed `openai/` for the LM Studio OpenAI-compatible
  endpoint; `GH_TOKEN` is injected into the agent shell env.
- Thinking budget via extra-body params `thinking: true` +
  `thinking_budget: N` (low for observe/comment, high for pr-draft). The
  `reasoning_effort` param is intentionally avoided (unreliable upstream).
- Submission contract: agent emits a heredoc whose first line is
  `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`; lines after become the submission
  (mini-swe-agent v2 `LocalEnvironment._check_finished`).
- `run_agent` never raises for agent-level failures: crashes, limit hits, and
  model errors are captured in `AgentOutcome`.

### Workspace hygiene (workspace.py)

- `Workdir(owner, name)` shallow-clones into
  `$TMPDIR/vettercode/<owner>__<name>-<ts>/` (depth 1, 600 s timeout).
- Clean exit → deleted. Exception → copied to
  `~/.cache/vettercode/failures/<same-name>/` then deleted.
- Removal uses a read-only-tolerant `_rmtree` (`onerror` re-chmods before
  retrying) since git marks packed objects read-only, which otherwise makes
  `shutil.rmtree(..., ignore_errors=True)` silently leave the clone behind
  on Windows.
- `sweep_stale()` removes entries older than 24 h (crashed-run leftovers).

### State (db.py)

SQLite `state.db`:
- `repos(owner, name, last_checked_at)` — drives the `since` filter.
- `issues(owner, name, number, last_reviewed_at, last_status)` — cooldown.
- `reviews(run_id, ..., action_taken, summary, pr_url)` — audit trail.

### Configuration (config.py)

- Flat `~/.config/vettercode/config.yaml`, seeded on first run from
  `CONFIG_TEMPLATE` (canonical defaults — no second copy in the repo).
- `null` values never override built-in defaults (fixes a legacy bug).
- `VETTERCODE_HOME` env (or `--config-home`) relocates everything (tests use
  a tmp home, so the real `~/.config/vettercode` is never touched).

## Testing strategy

- Fully offline: `responses` for GitHub HTTP, `FakeGithub` / `FakeAgentRun` /
  `FakeWorkdir` for runner tests, monkeypatched mini-swe-agent classes for
  agent wiring tests, real local git fixtures where clone logic matters.
- `uv run pytest --cov` — coverage enforced at ≥90% (`fail_under` in
  `pyproject.toml`); CI fails the build below that threshold.

## Known limits / future work

- Sequential mode rollout: all three modes are implemented and unit-tested;
  a live end-to-end `pr-draft` run against a real repo is the remaining
  integration milestone.
- No concurrency yet (one issue at a time) — intentional for a nightly job.
- Mode restrictions (e.g. "no writes" in observe mode) are enforced at the
  system-prompt level only; nothing currently sandboxes or blocks the agent
  from attempting a disallowed shell command inside its throwaway clone.
- No Docker or other container-based execution path is provided or planned;
  isolation relies entirely on the disposable git clone described above.
- On Windows, `tests/test_workspace.py`'s real-`git`-subprocess tests are
  intermittently flaky (`OSError: [WinError 6]`/`[WinError 50]` from
  CPython's `subprocess`/`_winapi` handle duplication under load) when run
  as part of the full suite, though they pass reliably in isolation. This
  has not been observed on the Linux CI runners (`ci.yml` runs on
  `ubuntu-latest`); if it reproduces on Linux too it would warrant deeper
  investigation, but for now it's a known Windows-local dev-loop quirk.
