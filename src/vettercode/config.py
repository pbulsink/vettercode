"""Configuration loading for vettercode.

All state lives under a single home directory (default ``~/.config/vettercode``):
``config.yaml`` and ``state.db`` (the repo list is resolved live from
the GitHub API each run, not cached to disk). Override the
location with the ``VETTERCODE_HOME`` environment variable (used by tests).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

AGENT_MODES = ("observe", "comment", "pr-draft")

DEFAULTS: dict = {
    "github_username": None,  # None -> resolve via `gh` / GitHub API
    "lmstudio_base_url": "http://localhost:1234/v1",
    "lmstudio_model": "qwen/qwen3.8-27b",
    "agent_mode": "pr-draft",
    "start_time": "20:00",
    "stop_time": "06:00",
    "timezone": "America/Toronto",
    "pr_branch_prefix": "vettercode/",
    "max_issues_per_night": 50,
    "review_cooldown_days": 7,
    "include_forks": True,
    "exclude_repos": [],  # owner/name or bare repo names to skip
    # Thinking budgets (LM Studio `thinking_budget` extra-body param, in tokens).
    "thinking_budget_low": 1024,    # observe / comment: simple tasks, think briefly
    "thinking_budget_high": 32768,  # pr-draft: code work, think deeply
    # Agent limits
    "step_limit": 60,
    "wall_time_limit_seconds": 1800,
    "command_timeout_seconds": 300,
    "max_comment_context": 20,
}

CONFIG_TEMPLATE = """\
# Vettercode configuration (seeded automatically on first run)
github_username: null            # null = resolve from `gh auth`
lmstudio_base_url: http://localhost:1234/v1
lmstudio_model: qwen/qwen3.8-27b
agent_mode: pr-draft             # observe | comment | pr-draft
start_time: "20:00"
stop_time: "06:00"
timezone: America/Toronto
pr_branch_prefix: "vettercode/"
max_issues_per_night: 50
review_cooldown_days: 7
include_forks: true
exclude_repos: []          # e.g. ["me/archived-repo", "some-other-repo"]
thinking_budget_low: 1024
thinking_budget_high: 32768
step_limit: 60
wall_time_limit_seconds: 1800
command_timeout_seconds: 300
max_comment_context: 20
"""


class ConfigError(ValueError):
    """Raised when configuration is missing or invalid."""


def default_home() -> Path:
    return Path(os.environ.get("VETTERCODE_HOME") or "~/.config/vettercode").expanduser()


@dataclass(frozen=True)
class Config:
    # Resolved locations
    home: Path
    log_dir: Path
    db_path: Path
    # Settings (flat, matching config.yaml keys)
    github_username: str | None
    lmstudio_base_url: str
    lmstudio_model: str
    agent_mode: str
    start_time: time
    stop_time: time
    timezone: ZoneInfo
    pr_branch_prefix: str
    max_issues_per_night: int
    review_cooldown_days: int
    include_forks: bool
    exclude_repos: list[str]
    thinking_budget_low: int | None
    thinking_budget_high: int | None
    step_limit: int
    wall_time_limit_seconds: int
    command_timeout_seconds: int
    max_comment_context: int


def _parse_time(value: str, key: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as e:
        raise ConfigError(f"{key} must be HH:MM, got {value!r}") from e


def _parse_int(value, key: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{key} must be an integer, got {value!r}") from e


def load_config(home: Path | None = None, *, seed: bool = True) -> Config:
    """Load config from ``<home>/config.yaml``.

    Missing file is seeded with ``CONFIG_TEMPLATE`` when ``seed`` is true.
    Explicit ``null`` values in the file never override built-in defaults
    (they are treated as "unset").
    """
    home = (home or default_home()).expanduser()
    config_path = home / "config.yaml"

    file_values: dict = {}
    if config_path.exists():
        try:
            raw = yaml.safe_load(config_path.read_text()) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"invalid YAML in {config_path}: {e}") from e
        if not isinstance(raw, dict):
            raise ConfigError(f"top level of {config_path} must be a mapping")
        file_values = raw
    elif seed:
        home.mkdir(parents=True, exist_ok=True)
        config_path.write_text(CONFIG_TEMPLATE)

    merged = dict(DEFAULTS)
    for key, value in file_values.items():
        if value is None:
            continue  # explicit null means "unset", never clobbers defaults
        if key not in DEFAULTS:
            continue  # ignore unknown keys
        merged[key] = value

    try:
        tz = ZoneInfo(merged["timezone"])
    except Exception as e:
        raise ConfigError(f"unknown timezone {merged['timezone']!r}") from e

    if merged["agent_mode"] not in AGENT_MODES:
        raise ConfigError(
            f"agent_mode must be one of {AGENT_MODES}, got {merged['agent_mode']!r}"
        )

    if not isinstance(merged["exclude_repos"], list) or not all(
        isinstance(item, str) for item in merged["exclude_repos"]
    ):
        raise ConfigError(f"exclude_repos must be a list of strings, got {merged['exclude_repos']!r}")

    merged["include_forks"] = bool(merged["include_forks"])

    for key in (
        "max_issues_per_night",
        "review_cooldown_days",
        "thinking_budget_low",
        "thinking_budget_high",
        "step_limit",
        "wall_time_limit_seconds",
        "command_timeout_seconds",
        "max_comment_context",
    ):
        merged[key] = _parse_int(merged[key], key) if merged[key] is not None else None

    settings = {
        "github_username": merged["github_username"],
        "lmstudio_base_url": merged["lmstudio_base_url"],
        "lmstudio_model": merged["lmstudio_model"],
        "agent_mode": merged["agent_mode"],
        "start_time": _parse_time(merged["start_time"], "start_time"),
        "stop_time": _parse_time(merged["stop_time"], "stop_time"),
        "timezone": tz,
        "pr_branch_prefix": merged["pr_branch_prefix"],
        "max_issues_per_night": merged["max_issues_per_night"],
        "review_cooldown_days": merged["review_cooldown_days"],
        "include_forks": merged["include_forks"],
        "exclude_repos": list(merged["exclude_repos"]),
        "thinking_budget_low": merged["thinking_budget_low"],
        "thinking_budget_high": merged["thinking_budget_high"],
        "step_limit": merged["step_limit"],
        "wall_time_limit_seconds": merged["wall_time_limit_seconds"],
        "command_timeout_seconds": merged["command_timeout_seconds"],
        "max_comment_context": merged["max_comment_context"],
    }

    return Config(
        home=home,
        log_dir=home / "logs",
        db_path=home / "state.db",
        **settings,
    )


def override(cfg: Config, **changes) -> Config:
    """Return a copy of cfg with the given fields replaced (CLI overrides)."""
    return replace(cfg, **changes)
