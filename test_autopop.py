#!/usr/bin/env python3
import os
from pathlib import Path

BASE = Path("/Users/pbulsink/Documents/config/github_night_agent")
REPOS_PATH = BASE / "repos.txt"

# Simulate empty repos.txt
REPOS_PATH.write_text("# empty\n")

print("repos.txt cleared")
print("Testing auto-populate logic with mock")

# Mock github response
mock_repos = [
    {"owner": {"login": "pbulsink"}, "name": "repo1"},
    {"owner": {"login": "pbulsink"}, "name": "repo2"},
]

def mock_autopopulate():
    repos = []
    if REPOS_PATH.exists():
        for line in REPOS_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                repos.append(line)
    if repos:
        return repos
    # mock fetch
    new_repos = [f"{r['owner']['login']}/{r['name']}" for r in mock_repos]
    REPOS_PATH.write_text("\n".join(sorted(set(new_repos))) + "\n")
    return new_repos

result = mock_autopopulate()
print("Result:", result)
print("repos.txt now:")
print(REPOS_PATH.read_text())
