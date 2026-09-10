# Vettercode - Nightly GitHub Issue Agent

## Implementation status
- Auto-populate repos from GitHub profile on first run
- Daily log rotation, 30 day retention
- `@vettercode` mention filter on issue body + comments
- Issue fetch with `since` based on last_checked_at
- Per-repo policy via AGENTS.md, default allow PR
- Mini-swe-agent invocation with LM Studio OpenAI-compatible endpoint
- Agent task includes issue context and instruction to create draft PR via `gh` CLI, never merge
- State tracked in SQLite: repos, issues, reviews
- Window guard 23:00-07:00 America/Toronto

## Files
- `.env` – GITHUB_TOKEN, GITHUB_USERNAME
- `repos.txt` – auto-populated
- `config.yaml` – defaults at top
- `state.db` – SQLite
- `vettercode-YYYY-MM-DD.log` – daily logs

Run: `python main.py`
