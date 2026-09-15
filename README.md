# Vettercode

[![CI](https://github.com/pbulsink/vettercode/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/pbulsink/vettercode/actions/workflows/ci.yml)
[![Coverage](https://coveralls.io/repos/github/pbulsink/vettercode/badge.svg?branch=main)](https://coveralls.io/github/pbulsink/vettercode?branch=main)

Local, nightly GitHub issue agent. It watches your repos for issues (or
comments) mentioning **@vettercode**, clones each repo into a throw-away
temp directory, runs an **LM Studio** model through
[mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) (v2 library API)
to investigate — and optionally comment or open a **draft** PR — then
cleans up. It **never merges**.

- 100% local: no cloud API keys, no SaaS; the model runs on your machine.
- Auth comes from the `gh` CLI (single source of truth) — no `.env` file,
  no raw tokens stored anywhere.
- All state lives in `~/.config/vettercode/`; temp clones never touch your
  working directories.
- The repo list is resolved live from the GitHub API on every run — no
  static file to keep in sync; use `exclude_repos` in `config.yaml` to opt
  specific repos out.

## How it works

```mermaid
flowchart TD
    A[launchd fires nightly] --> B["vettercode CLI (runner.run_once)"]
    B --> C{In time window?}
    C -- no --> Z[exit 0]
    C -- yes --> D["gh auth token (auth.py)"]
    D --> E["List repos (GitHub API, live — repos.py)"]
    E --> F["Per repo: list open issues since last check (github.py)"]
    F --> G{"@vettercode mention\nin body or comments?"}
    G -- no --> F
    G -- yes --> H{Reviewed recently?\n(cooldown)}
    H -- yes --> F
    H -- no --> I["Clone into $TMPDIR (workspace.py)"]
    I --> J["Read AGENTS.md policy, downgrade mode if needed (policy.py)"]
    J --> K["Run mini-swe-agent in-process against LM Studio (agent.py)"]
    K --> L{Mode}
    L -- observe --> M[Log summary only]
    L -- comment --> N["vettercode posts one comment via GitHub API"]
    L -- pr-draft --> O["Agent pushes branch + gh pr create --draft"]
    M & N & O --> P["Record state.db (db.py): last-checked, review, audit row"]
    P --> F
```

Vettercode intentionally does **not** use Docker or any external sandbox
container. Each issue is handled by mini-swe-agent's `DefaultAgent` running
**in-process**, against a `LocalEnvironment` pointed at a shallow, throwaway
git clone (see [Temp directories](#temp-directories-clean-workspace-guarantee)
below). This keeps the dependency surface small (no Docker daemon, no image
pulls) while still guaranteeing your real working directories are never
touched — the isolation boundary is the temp clone, not a container.

## Requirements

- macOS (or any Unix) with **Python 3.10+**, **uv**, **git**, and the **gh** CLI
- `gh auth login` completed (a token with `repo` scope)
- **LM Studio** serving an OpenAI-compatible endpoint (default
  `http://localhost:1234/v1`) with the model loaded
  (default model id: `qwen/qwen3.8-27b`)

## Install

```bash
git clone https://github.com/pbulsink/vettercode.git
cd vettercode
uv sync --extra dev     # create .venv, install runtime + dev deps
```

Verify:

```bash
uv run vettercode --version
uv run pytest -q        # offline test suite
```

Or install the CLI globally in your user site (optional):

```bash
uv tool install --from . .
```

## Quick start

```bash
# Safe dry run: fetch + filter only, no agent, no writes to GitHub
uv run vettercode --dry-run --ignore-window

# Real run, single repo, observe mode
uv run vettercode --repo pbulsink/vettercode --mode observe --ignore-window
```

To have an issue picked up, mention the agent in the issue body or a comment:
`@vettercode please investigate`.

## Modes

| Mode       | What it does                                                        | Thinking budget |
|------------|---------------------------------------------------------------------|-----------------|
| `observe`  | Read-only investigation; produces a summary. No writes anywhere.     | low  |
| `comment`  | Investigates read-only, then **vettercode itself** posts one finding comment on the issue via the GitHub API. | low |
| `pr-draft` | Investigates, may implement a fix, pushes a `vettercode/*` branch, opens a **DRAFT** PR via `gh`. Never merges. | high |

Per-repo policy: if the repo's `AGENTS.md` contains `allow_pr: false` (or
`no-pr`) / `allow_comment: false` (or `no comments`), vettercode downgrades
the mode one step at a time (`pr-draft → comment → observe`).

## Repo list

Vettercode does **not** maintain a `repos.txt` file. Every run it asks the
GitHub API for the configured (or auto-detected) user's repos
(`GET /users/{username}/repos`, paginated) and processes that live list.
This means newly created repos are picked up automatically and deleted/
renamed repos disappear on their own — nothing to hand-edit.

To keep specific repos out of scope permanently, add them to
`exclude_repos` in `config.yaml` (matches either the full `owner/name` or
the bare repo name):

```yaml
include_forks: true
exclude_repos:
  - me/archived-experiment
  - some-other-repo-name
```

Use `--repo OWNER/NAME` on the CLI for a one-off single-repo run instead of
processing the whole list.

## Configuration

Everything lives in one directory: **`~/.config/vettercode/`**
(override location with `VETTERCODE_HOME`):

```
~/.config/vettercode/
├── config.yaml   # seeded with defaults on first run
├── state.db      # SQLite: last-checked timestamps, cooldowns, review history
└── logs/         # vettercode-YYYY-MM-DD.log (30-day retention)
```

`config.yaml` is created automatically on first run with working defaults;
edit it to taste. Explicit `null` values are treated as "unset" and never
clobber defaults.

| Key | Default | Meaning |
|---|---|---|
| `github_username` | `null` | `null` = resolve via `gh auth status` |
| `lmstudio_base_url` | `http://localhost:1234/v1` | OpenAI-compatible endpoint |
| `lmstudio_model` | `qwen/qwen3.8-27b` | LM Studio model id |
| `agent_mode` | `pr-draft` | `observe` \| `comment` \| `pr-draft` |
| `start_time` / `stop_time` | `20:00` / `06:00` | nightly window (wraps midnight) |
| `timezone` | `America/Toronto` | IANA zone for the window |
| `pr_branch_prefix` | `vettercode/` | branch prefix for draft PRs |
| `max_issues_per_night` | `50` | hard cap per run |
| `review_cooldown_days` | `7` | don't re-review an issue within N days |
| `include_forks` | `true` | include forked repos when resolving the repo list |
| `exclude_repos` | `[]` | `owner/name` or bare repo names to skip |
| `thinking_budget_low` | `1024` | LM Studio `thinking_budget` for observe/comment |
| `thinking_budget_high` | `32768` | LM Studio `thinking_budget` for pr-draft |
| `step_limit` | `60` | max agent steps per issue |
| `wall_time_limit_seconds` | `1800` | max wall time per issue |
| `command_timeout_seconds` | `300` | per-command timeout in the agent shell |
| `max_comment_context` | `20` | issue comments included in the task prompt |

### CLI

```
vettercode [--version] [--config-home DIR] [--mode observe|comment|pr-draft]
           [--repo OWNER/NAME] [--dry-run] [--ignore-window] [--verbose]
```

Exit codes: `0` ok (including out-of-window), `2` `gh` authentication failure.

## Temp directories (clean workspace guarantee)

- Each repo is cloned into `$TMPDIR/vettercode/<owner>__<name>-<ts>/`
  (shallow clone, depth 1).
- On success the clone is **deleted**; on failure it is **copied to**
  `~/.cache/vettercode/failures/` (24 h retention sweep available) and
  then removed. Your working directories are never modified.
- Cleanup tolerates read-only files left behind by git (common on Windows);
  see `workspace._rmtree`.

## Nightly automation (launchd, macOS)

The repo ships `com.pbulsink.vettercode.plist` (runs daily at 23:00).
Install it:

```bash
mkdir -p ~/Library/LaunchAgents
cp com.pbulsink.vettercode.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.pbulsink.vettercode.plist
```

Uninstall / one-off run:

```bash
launchctl unload ~/Library/LaunchAgents/com.pbulsink.vettercode.plist
launchctl start com.pbulsink.vettercode        # trigger immediately
```

The plist invokes the project venv (`<repo>/.venv/bin/vettercode`);
after updating the code run `uv sync` so the venv is current.

### Other platforms

There is no bundled unit/cron file for Linux or Windows yet — a `systemd`
timer or `cron` entry calling the installed `vettercode` executable is the
straightforward equivalent on Linux; on Windows, Task Scheduler running
`vettercode.exe` from the venv's `Scripts/` directory works the same way.
`--ignore-window` is useful for manually triggered/one-off runs outside the
configured nightly window.

## Security & permissions

- **Auth**: entirely via the `gh` CLI (`gh auth login`); vettercode never
  reads a raw `GITHUB_TOKEN` environment variable or `.env` file. The token
  `gh auth token` returns is passed to the agent's shell environment as
  `GH_TOKEN` for the duration of a single issue's run only.
- **Scope needed**: a `gh` token with `repo` scope (read issues/comments,
  write comments, create branches/PRs) is sufficient. Vettercode never
  requests or exercises admin/merge permissions.
- **PRs are always drafts**: `pr-draft` mode never merges, force-pushes, or
  pushes to the default branch — see `SYSTEM_TEMPLATES["pr-draft"]` in
  `agent.py` for the exact rules given to the model.
- **Per-repo opt-out**: repo maintainers who don't want the agent writing
  to their repo can add `allow_pr: false` and/or `allow_comment: false` to
  `AGENTS.md`; vettercode downgrades its mode accordingly before doing
  anything (see [Modes](#modes)).
- **LLM output is not trusted blindly**: the agent runs against a shallow,
  disposable clone; write actions are limited to `git`/`gh` commands inside
  that clone, and observe/comment modes are enforced at the prompt level
  (no code beyond the system template currently prevents a misbehaving
  model from attempting a write command it wasn't supposed to — see
  [Known limits](DESIGN.md#known-limits--future-work)).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Exit code `2` / "GitHub auth not ready" | `gh` not installed or not logged in | `gh auth login`, or `brew install gh` first |
| `RuntimeError: no repos found for ...` | Wrong `github_username`, all repos excluded, or an empty GitHub profile | Check `config.yaml`'s `github_username`/`exclude_repos`; confirm `gh api /user` returns the expected account |
| Agent outcome `exit_status="Crashed"` | LM Studio not running, wrong `lmstudio_base_url`/`lmstudio_model`, or a model error | Confirm LM Studio is serving the configured model at the configured URL; check `~/.config/vettercode/logs/` |
| Issue never gets picked up | No `@vettercode` mention detected, or it's within `review_cooldown_days` of a prior review | Confirm the mention text and casing match `@vettercode`; check `state.db`'s `issues` table for `last_reviewed_at` |
| Draft PR never appears after a `pr-draft` run | Model decided not to open one (see its summary in `reviews.summary`), or `AGENTS.md` downgraded the mode | Check the run's log line (`mode ... downgraded to ...`) and the recorded review summary |
| Nightly run silently does nothing | Outside the configured `start_time`/`stop_time` window | Use `--ignore-window` to confirm the rest of the pipeline works, then check `timezone`/window config |

## Development

```bash
uv sync --extra dev
uv run pytest --cov          # fully offline: fake GitHub API, fake agent
```

Coverage is enforced in CI (`fail_under = 90` in `pyproject.toml`); a run
below that threshold fails the build.

Layout: `src/vettercode/` (12 modules, see [DESIGN.md](DESIGN.md)),
`tests/` (mirrors each module 1:1).

