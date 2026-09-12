from datetime import time
from zoneinfo import ZoneInfo

import pytest

from vettercode.config import ConfigError, load_config, override


def test_defaults_are_seeded(home):
    cfg = load_config(home)
    assert (home / "config.yaml").exists()
    assert cfg.agent_mode == "pr-draft"
    assert cfg.lmstudio_model == "qwen/qwen3.8-27b"
    assert cfg.lmstudio_base_url == "http://localhost:1234/v1"
    assert cfg.start_time == time(20, 0)
    assert cfg.stop_time == time(6, 0)
    assert cfg.timezone == ZoneInfo("America/Toronto")
    assert cfg.max_issues_per_night == 50
    assert cfg.review_cooldown_days == 7


def test_paths_derived_from_home(home):
    cfg = load_config(home)
    assert cfg.home == home
    assert cfg.log_dir == home / "logs"
    assert cfg.repos_path == home / "repos.txt"
    assert cfg.db_path == home / "state.db"


def test_null_values_never_clobber_defaults(home):
    # Regression: old main.py merged top-level `null` values over defaults.
    (home / "config.yaml").write_text(
        "github_username: null\nlmstudio_model: null\nagent_mode: null\n"
    )
    cfg = load_config(home)
    assert cfg.lmstudio_model == "qwen/qwen3.8-27b"
    assert cfg.agent_mode == "pr-draft"


def test_custom_values_are_honored(home):
    (home / "config.yaml").write_text(
        "agent_mode: observe\n"
        "max_issues_per_night: 3\n"
        "start_time: '23:00'\n"
        "stop_time: '07:00'\n"
        "lmstudio_model: other/model\n"
    )
    cfg = load_config(home)
    assert cfg.agent_mode == "observe"
    assert cfg.max_issues_per_night == 3
    assert cfg.start_time == time(23, 0)
    assert cfg.stop_time == time(7, 0)
    assert cfg.lmstudio_model == "other/model"


def test_unknown_keys_are_ignored(home):
    (home / "config.yaml").write_text("some_future_key: 42\nagent_mode: observe\n")
    cfg = load_config(home)
    assert cfg.agent_mode == "observe"


def test_invalid_mode_raises(home):
    (home / "config.yaml").write_text("agent_mode: teleport\n")
    with pytest.raises(ConfigError, match="agent_mode"):
        load_config(home)


def test_invalid_time_raises(home):
    (home / "config.yaml").write_text("start_time: '25:99'\n")
    with pytest.raises(ConfigError, match="start_time"):
        load_config(home)


def test_invalid_yaml_raises(home):
    (home / "config.yaml").write_text("[: not: valid: yaml: [")
    with pytest.raises(ConfigError, match="YAML"):
        load_config(home)


def test_non_mapping_yaml_raises(home):
    (home / "config.yaml").write_text("- a\n- b\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(home)


def test_override_replaces_fields(home):
    cfg = load_config(home)
    cfg2 = override(cfg, agent_mode="observe", max_issues_per_night=1)
    assert cfg2.agent_mode == "observe"
    assert cfg2.max_issues_per_night == 1
    assert cfg.agent_mode == "pr-draft"  # original untouched


def test_home_env_override(tmp_path, monkeypatch):
    custom = tmp_path / "custom-home"
    monkeypatch.setenv("VETTERCODE_HOME", str(custom))
    from vettercode.config import default_home

    assert default_home() == custom
