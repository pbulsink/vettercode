#!/usr/bin/env python3
"""
Vettercode - nightly GitHub issue agent
"""
import os
import re
import sqlite3
import yaml
import requests
from pathlib import Path
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import shutil

BASE = Path(__file__).parent
CONFIG_PATH = BASE / "config.yaml"
ENV_PATH = BASE / ".env"
REPOS_PATH = BASE / "repos.txt"
DB_PATH = BASE / "state.db"

def log_filename():
    return BASE / f"vettercode-{datetime.now(ZoneInfo('America/Toronto')).strftime('%Y-%m-%d')}.log"

def rotate_logs():
    # delete logs older than 30 days
    now = datetime.now(ZoneInfo('America/Toronto'))
    for p in BASE.glob("vettercode-*.log"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=ZoneInfo('America/Toronto'))
            if now - mtime > timedelta(days=30):
                p.unlink()
        except Exception:
            pass

def log(msg):
    rotate_logs()
    path = log_filename()
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line)
    with open(path, "a") as f:
        f.write(line + "\n")

def load_env():
    load_dotenv(ENV_PATH)
    token = os.getenv("GITHUB_TOKEN") or ""
    username = os.getenv("GITHUB_USERNAME")
    # token is optional for public repos, but warn
    if not token:
        log("GITHUB_TOKEN not set, using unauthenticated GitHub API access")
    return token, username or "pbulsink"

def load_config():
    defaults = {
        "github_username": "pbulsink",
        "lmstudio_base_url": "http://localhost:1234/v1",
        "lmstudio_model": "local-model-id",
        "agent_mode": "pr-draft",
        "start_time": "23:00",
        "stop_time": "07:00",
        "timezone": "America/Toronto",
        "pr_branch_prefix": "vettercode/",
        "max_issues_per_night": 50,
    }
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        defaults.update(cfg.get("defaults", {}))
        defaults.update({k:v for k,v in cfg.items() if k not in ("defaults",)})
    return defaults

def load_repos():
    repos = []
    if REPOS_PATH.exists():
        for line in REPOS_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                repos.append(line)
    return repos

def save_repos(repos):
    REPOS_PATH.write_text("\n".join(sorted(set(repos))) + "\n")

def github_get(token, url):
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()

def autopopulate_repos(token, username):
    repos = load_repos()
    if repos:
        return repos
    log(f"Auto-populating repos for {username}")
    url = f"https://api.github.com/users/{username}/repos?per_page=100"
    data = github_get(token, url)
    new_repos = [f"{r['owner']['login']}/{r['name']}" for r in data]
    save_repos(new_repos)
    return new_repos

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS repos (
            owner TEXT, name TEXT, added_at TEXT, last_checked_at TEXT,
            PRIMARY KEY(owner, name)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS issues (
            owner TEXT, name TEXT, number INTEGER, last_seen_updated_at TEXT,
            last_reviewed_at TEXT, status TEXT,
            PRIMARY KEY(owner, name, number)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT, owner TEXT, name TEXT, issue_number INTEGER,
            action_taken TEXT, summary TEXT, pr_url TEXT, created_at TEXT
        )
    """)
    conn.commit()
    return conn

def in_window(now, tz, start_t, stop_t):
    t = now.astimezone(tz).time()
    if start_t <= stop_t:
        return start_t <= t <= stop_t
    else:
        return t >= start_t or t <= stop_t

def has_vettercode_mention(issue_body, comments):
    pattern = re.compile(r"@vettercode", re.IGNORECASE)
    if issue_body and pattern.search(issue_body):
        return True
    for c in comments:
        if pattern.search(c.get("body","")):
            return True
    return False

def load_policy(owner, name):
    # Simplified: check AGENTS.md exists, else allow
    # In full implementation, parse AGENTS.md and skills folder
    repo_path = Path.home() / "Documents/GitHub" / owner / name
    agents_md = repo_path / "AGENTS.md"
    if agents_md.exists():
        return {"allow_pr": True, "source": "AGENTS.md"}
    # fallback skills
    return {"allow_pr": True, "source": "default"}

def get_repo_last_checked(conn, owner, name):
    cur = conn.cursor()
    cur.execute("SELECT last_checked_at FROM repos WHERE owner=? AND name=?", (owner, name))
    row = cur.fetchone()
    if row and row[0]:
        return row[0]
    return None

def set_repo_last_checked(conn, owner, name):
    cur = conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()
    cur.execute("INSERT OR REPLACE INTO repos(owner,name,added_at,last_checked_at) VALUES(?,?,COALESCE((SELECT added_at FROM repos WHERE owner=? AND name=?),?),?)",
                (owner, name, owner, name, now_iso, now_iso))
    conn.commit()

def fetch_issues(token, owner, name, since_iso):
    url = f"https://api.github.com/repos/{owner}/{name}/issues?state=open&per_page=100"
    if since_iso:
        url += f"&since={since_iso}"
    issues = []
    while url:
        data = github_get(token, url)
        # filter out pull requests
        for item in data:
            if "pull_request" in item:
                continue
            issues.append(item)
        # pagination
        # simple: stop after first page for now
        url = None
    return issues

def fetch_issue_comments(token, owner, name, number):
    url = f"https://api.github.com/repos/{owner}/{name}/issues/{number}/comments"
    try:
        return github_get(token, url)
    except Exception as e:
        log(f"Failed to fetch comments for {owner}/{name}#{number}: {e}")
        return []

def invoke_mini_agent(task_prompt, cfg):
    import subprocess
    env = os.environ.copy()
    env["OPENAI_API_BASE"] = cfg.get("lmstudio_base_url", "http://localhost:1234/v1")
    env["OPENAI_API_KEY"] = env.get("OPENAI_API_KEY", "")
    env["MSWEA_MODEL_NAME"] = cfg.get("lmstudio_model", "local-model-id")
    # mini-swe-agent CLI: use yolo mode for automation
    # Write task to temp file to avoid shell quoting issues
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tf:
        tf.write(task_prompt)
        task_file = tf.name
    try:
        cmd = ["mini", "-m", cfg.get("lmstudio_model","local-model-id"), "-y", f"@{task_file}"]
        log(f"Invoking mini-swe-agent for task")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
        return result.stdout, result.stderr, result.returncode
    finally:
        try:
            Path(task_file).unlink()
        except Exception:
            pass

def main():
    cfg = load_config()
    token, username = load_env()
    tz = ZoneInfo(cfg["timezone"])
    now = datetime.now(timezone.utc)
    start_t = time.fromisoformat(cfg["start_time"])
    stop_t = time.fromisoformat(cfg["stop_time"])
    if not in_window(now, tz, start_t, stop_t):
        log(f"Outside window {cfg['start_time']}-{cfg['stop_time']}, exiting")
        return
    repos = autopopulate_repos(token, username)
    conn = init_db()
    run_id = datetime.now(timezone.utc).isoformat()
    log(f"Vettercode starting for {username}, {len(repos)} repos")
    processed = 0
    for repo_str in repos:
        owner, name = repo_str.split("/", 1)
        policy = load_policy(owner, name)
        log(f"Repo {owner}/{name} policy allow_pr={policy['allow_pr']}")
        since_iso = get_repo_last_checked(conn, owner, name)
        try:
            issues = fetch_issues(token, owner, name, since_iso)
        except Exception as e:
            log(f"Failed to fetch issues for {owner}/{name}: {e}")
            continue
        for issue in issues:
            number = issue.get("number")
            body = issue.get("body") or ""
            updated_at = issue.get("updated_at")
            # Check @vettercode mention
            comments = fetch_issue_comments(token, owner, name, number)
            if not has_vettercode_mention(body, comments):
                continue
            # Skip if already reviewed recently
            cur = conn.cursor()
            cur.execute("SELECT last_reviewed_at FROM issues WHERE owner=? AND name=? AND number=?", (owner, name, number))
            row = cur.fetchone()
            if row and row[0]:
                # simple skip if reviewed in last 7 days
                try:
                    reviewed = datetime.fromisoformat(row[0].replace("Z","+00:00"))
                    if datetime.now(timezone.utc) - reviewed < timedelta(days=7):
                        continue
                except Exception:
                    pass
            # Build task for agent
            task = f"""You are vettercode agent. Repo {owner}/{name}, issue #{number}: {issue.get('title','')}

Issue body:
{body}

Comments:
{chr(10).join(c.get('body','') for c in comments[:10])}

Task: Investigate the issue, gather information, and if a fix is straightforward, create a draft PR using `gh` CLI. Do not merge. Respect repo policy. Summarize actions taken."""
            stdout, stderr, rc = invoke_mini_agent(task, cfg)
            summary = stdout[-2000:] if stdout else ""
            # Record review
            cur.execute("INSERT OR REPLACE INTO issues(owner,name,number,last_seen_updated_at,last_reviewed_at,status) VALUES(?,?,?,?,?,?)",
                        (owner, name, number, updated_at, datetime.now(timezone.utc).isoformat(), "reviewed"))
            cur.execute("INSERT INTO reviews(run_id,owner,name,issue_number,action_taken,summary,pr_url,created_at) VALUES(?,?,?,?,?,?,?)",
                        (run_id, owner, name, number, "agent_run", summary, None, datetime.now(timezone.utc).isoformat()))
            conn.commit()
            log(f"Processed {owner}/{name}#{number}, rc={rc}")
            processed += 1
            if processed >= cfg["max_issues_per_night"]:
                break
        set_repo_last_checked(conn, owner, name)
        if processed >= cfg["max_issues_per_night"]:
            break
    log("Run complete")

if __name__ == "__main__":
    main()
