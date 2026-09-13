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
- Auth comes from the `gh` CLI (single source of truth).
- All state lives in `~/.config/vettercode/`; temp clones never touch your
  working directories.

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

## Configuration

Everything lives in one directory: **`~/.config/vettercode/`**
(override location with `VETTERCODE_HOME`):

```
~/.config/vettercode/
├── config.yaml   # seeded with defaults on first run
├── repos.txt     # owner/name list; auto-populated from your profile on first run
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

## Development

```bash
uv sync --extra dev
uv run pytest --cov          # fully offline: fake GitHub API, fake agent
```

Layout: `src/vettercode/` (12 modules, see [DESIGN.md](DESIGN.md)),
`tests/` (104 tests, ≥90% coverage).
