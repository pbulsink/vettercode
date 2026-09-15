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
- Never modify the user's working directories; temp clones live in the OS temp
  directory.
- Single config/state home, at a platform-appropriate location
  (`~/.config/vettercode/` on macOS/Linux, `%APPDATA%\vettercode` on Windows).
- Auth via `gh` CLI; if `gh` is missing or unauthenticated, fail fast with a
  setup hint (CLI exit code 2).
- Run on macOS, Linux and Windows from one codebase, with platform differences
  confined to a single module (`platforms.py`) rather than scattered
  `sys.platform` checks.

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
        ├─ workspace.py Workdir (temp clone; failure → platform cache dir)
        └─ agent.py     mini-swe-agent wiring (DefaultAgent/LitellmModel/LocalEnvironment)
config.py     flat config.yaml + dataclass; null ≠ override
logsetup.py   daily log files, 30-day retention, config-driven timezone
platforms.py  OS-specific dirs + shell discovery (imported by config/workspace/agent/auth)
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

### Platform abstraction (platforms.py)

Vettercode supports macOS, Linux and Windows. Rather than sprinkle
`sys.platform` checks through the codebase, every OS-dependent decision is
made in `platforms.py` and imported by the four modules that need one
(`config.py`, `workspace.py`, `agent.py`, `auth.py`). The module is pure
(no I/O beyond `shutil.which` and `Path.is_file`), which keeps it trivially
testable: `tests/test_platforms.py` monkeypatches the `IS_WINDOWS` flag to
exercise both branches on any host.

| Helper | Unix | Windows |
|---|---|---|
| `config_home()` | `$XDG_CONFIG_HOME/vettercode`, else `~/.config/vettercode` | `%APPDATA%\vettercode` |
| `cache_home()` | `$XDG_CACHE_HOME/vettercode`, else `~/.cache/vettercode` | `%LOCALAPPDATA%\vettercode\cache` |
| `find_bash()` | `bash` from `PATH` | `PATH`, else the Git for Windows bundled `bash.exe` |
| `gh_install_hint()` | `brew install gh` (macOS) / distro instructions | `winget install --id GitHub.cli` |
| `supports_symlink_copy()` | `True` | `False` (needs Developer Mode/elevation) |

`IS_WINDOWS` is a module-level constant rather than a function call so that
tests can monkeypatch it, and so the branch is readable at each call site.

#### The agent shell problem

This is the one genuinely load-bearing piece of the abstraction, and the
reason Windows support is more than a path-handling exercise.

mini-swe-agent's `LocalEnvironment` executes model-issued commands with
`subprocess.Popen(command, shell=True)`. On Unix that is `/bin/sh`; on Windows
it is `cmd.exe`. But two things in this design are POSIX shell by
construction:

1. vettercode's system prompts, which instruct the model in `git`/`gh`
   pipelines and here-docs; and
2. mini-swe-agent's **own** submission contract — the agent signals completion
   by emitting a here-doc whose first line is
   `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`. `cmd.exe` has no here-doc syntax,
   so under `cmd.exe` an agent literally cannot submit a result.

The two options were to maintain a second, PowerShell-dialect set of prompts
(doubling the prompt-engineering surface and the ways a local model can go
wrong), or to guarantee a POSIX shell on every platform. We chose the latter:
`git` is already a hard requirement, and Git for Windows bundles `bash`, so in
practice the shell is already present on any machine that can run vettercode
at all.

`agent.make_environment()` therefore returns a plain `LocalEnvironment` on
Unix, and on Windows a subclass that rewrites each command through
`bash -lc` before delegating:

```python
def execute(self, action, cwd="", *, timeout=None):
    wrapped = dict(action)
    wrapped["command"] = _wrap_for_bash(action.get("command", ""), bash)
    return super().execute(wrapped, cwd, timeout=timeout)
```

Because the wrapped string is consumed by `cmd.exe` as a double-quoted
argument, `_wrap_for_bash` escapes only `"` and `%` (the latter being
`cmd.exe` variable expansion). The prompts additionally state that commands
run under POSIX `bash` on every platform, so the model is never tempted into
PowerShell syntax.

Failure mode: if no bash can be found, `make_environment` raises
`BashUnavailableError`, which `run_agent` catches and converts into a
`Crashed` `AgentOutcome` carrying an actionable message. One unusable machine
configuration degrades a single issue, consistent with the rest of
`run_agent`'s "agent problems never kill the run" contract.

#### Subprocess hygiene
All `git`/`gh` subprocess calls pass `stdin=subprocess.DEVNULL`. This is
primarily correctness (neither tool should ever block a nightly job on an
interactive credential prompt), but it is also required on Windows: the
parent's stdin handle is not always inheritable — notably under pytest's
output capture — which surfaces as `OSError: [WinError 6] The handle is
invalid` at process-spawn time.

### Agent wiring (agent.py)

- `DefaultAgent(model=LitellmModel, env=make_environment(workdir),
  system_template, instance_template, step_limit, wall_time_limit_seconds)`.
  The environment is built behind `make_environment()` so the Windows bash
  wrapper stays invisible to the rest of the wiring (see
  [Platform abstraction](#platform-abstraction-platformspy)).
- Model name is prefixed `openai/` for the LM Studio OpenAI-compatible
  endpoint; `GH_TOKEN` is injected into the agent shell env.
- Thinking budget via extra-body params `thinking: true` +
  `thinking_budget: N` (low for observe/comment, high for pr-draft). The
  `reasoning_effort` param is intentionally avoided (unreliable upstream).
- Submission contract: agent emits a heredoc whose first line is
  `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`; lines after become the submission
  (mini-swe-agent v2 `LocalEnvironment._check_finished`). This is why a POSIX
  shell is guaranteed on every platform.
- The `comment`-mode prompt writes its comment body to a file in the clone
  (not `/tmp`), so the path is valid on every platform and disappears with the
  workdir.
- `run_agent` never raises for agent-level failures: crashes, limit hits,
  model errors, and a missing Windows bash are captured in `AgentOutcome`.

### Workspace hygiene (workspace.py)

- `Workdir(owner, name)` shallow-clones into
  `<tempdir>/vettercode/<owner>__<name>-<ts>/` (depth 1, 600 s timeout), where
  `<tempdir>` comes from `tempfile.gettempdir()` — which already honours
  `TMPDIR` on Unix and `TEMP`/`TMP` on Windows, so it is the portable single
  source of truth.
- Clean exit → deleted. Exception → copied to
  `<cache_home>/failures/<same-name>/` then deleted. The copy requests symlink
  preservation only where `platforms.supports_symlink_copy()` allows it, since
  Windows rejects symlink creation without Developer Mode or elevation.
- Removal uses a read-only-tolerant `_rmtree` that re-chmods before retrying,
  since git marks packed objects read-only, which otherwise makes
  `shutil.rmtree` fail and leave the clone behind on Windows. It passes
  `onexc` on Python 3.12+ and `onerror` below, the former having deprecated
  the latter.
- Clone failures catch `OSError` broadly (not just `FileNotFoundError`), which
  covers both a missing `git` and Windows-specific process-spawn failures.
- `sweep_stale()` removes entries older than 24 h (crashed-run leftovers).

### Logging (logsetup.py)

Daily log files (`vettercode-YYYY-MM-DD.log`) under `<home>/logs`, mirrored to
the console, with 30-day retention. Which calendar day a run lands in — and
therefore which file it writes and which files retention deletes — depends on
a local zone resolved by `resolve_tz()`:

1. the `TZ` environment variable, the POSIX convention, so a Unix user can
   override per-invocation without editing config;
2. `cfg.timezone` from `config.yaml`, passed in by `cli.py`. Windows has no
   `TZ` convention, so this is the setting that actually takes effect there —
   and it is the same zone the nightly `start_time`/`stop_time` window uses,
   so log days and run windows cannot silently disagree;
3. `FALLBACK_TZ` (`America/Toronto`) for callers with no config loaded.

An unparseable value falls through to the next candidate rather than raising:
a typo'd timezone should degrade the log filename, not stop the run from
logging at all. (Note that `config.py` *does* reject an unknown `timezone`
outright, so in practice only the `TZ` env var reaches the fallback path.)

### State (db.py)
SQLite `state.db`:
- `repos(owner, name, last_checked_at)` — drives the `since` filter.
- `issues(owner, name, number, last_reviewed_at, last_status)` — cooldown.
- `reviews(run_id, ..., action_taken, summary, pr_url)` — audit trail.

### Configuration (config.py)

- Flat `config.yaml` under `platforms.config_home()`, seeded on first run from
  `CONFIG_TEMPLATE` (canonical defaults — no second copy in the repo).
- `null` values never override built-in defaults (fixes a legacy bug).
- `VETTERCODE_HOME` env (or `--config-home`) relocates everything and takes
  precedence over the platform default (tests use a tmp home, so the real
  config home is never touched).

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
