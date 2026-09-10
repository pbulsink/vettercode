# Vettercode

Local nightly GitHub issue agent using LM Studio models + mini-swe-agent + GitHub MCP.

## Setup

1. Copy config and env
```bash
cp config.yaml.example config.yaml
cp .env.example .env
```
Edit `.env` for `GITHUB_TOKEN`, `GITHUB_USERNAME`.
`repos.txt` is auto-populated on first run from your GitHub profile.

2. Install
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pyyaml python-dotenv requests
```

3. Configure model defaults in `config.yaml`.

4. Run
```bash
python main.py
```

## Behavior
- Filters issues containing `@vettercode` mention in body or comments
- Daily logs `vettercode-YYYY-MM-DD.log`, auto-delete >30 days
- Run window 23:00-07:00 America/Toronto, state checkpointed
- PRs opened but never merged
- PR policy per repo via `AGENTS.md`, default allow PR
- First run auto-populates `repos.txt` from GitHub profile
