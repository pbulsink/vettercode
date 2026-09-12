import pytest

from vettercode import agent as agent_mod
from vettercode.agent import (
    SYSTEM_TEMPLATES,
    build_task_prompt,
    extract_pr_url,
    make_agent,
    model_kwargs_for,
    model_name_for,
    run_agent,
)


def test_build_task_prompt_contains_context():
    comments = [{"user": {"login": "me"}, "body": "any update?"}]
    prompt = build_task_prompt(
        "o", "n", 42, "Bug title", "body text", comments, "observe", max_comments=20
    )
    assert "o/n" in prompt
    assert "#42" in prompt
    assert "Bug title" in prompt
    assert "body text" in prompt
    assert "Mode: observe" in prompt
    assert "[me] any update?" in prompt


def test_build_task_prompt_caps_comments():
    comments = [{"user": {"login": f"u{i}"}, "body": f"c{i}"} for i in range(3)]
    prompt = build_task_prompt("o", "n", 1, "t", "b", comments, "observe", max_comments=1)
    assert "3 total" in prompt
    assert "[u0] c0" in prompt
    assert "[u1] c1" not in prompt
    assert "u2" not in prompt


def test_build_task_prompt_handles_empty_body_and_no_comments():
    prompt = build_task_prompt("o", "n", 1, "t", "", [], "observe")
    assert "(empty)" in prompt
    assert "(none)" in prompt


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("qwen/qwen3.8-27b", "openai/qwen/qwen3.8-27b"),
        ("local-model", "openai/local-model"),
        ("openai/gpt-4o", "openai/gpt-4o"),
        ("anthropic/claude-sonnet", "anthropic/claude-sonnet"),
    ],
)
def test_model_name_for(raw, expected):
    assert model_name_for(raw) == expected


def test_model_kwargs_for_with_budget():
    kw = model_kwargs_for("http://localhost:1234/v1", 1024)
    assert kw["api_base"] == "http://localhost:1234/v1"
    assert kw["thinking"] is True
    assert kw["thinking_budget"] == 1024


def test_model_kwargs_for_without_budget():
    kw = model_kwargs_for("http://localhost:1234/v1", None)
    assert "thinking" not in kw
    assert "thinking_budget" not in kw


@pytest.mark.parametrize(
    ("low", "high", "mode", "expected"),
    [
        (10, 99, "pr-draft", 99),
        (10, 99, "observe", 10),
        (10, 99, "comment", 10),
        (None, None, "observe", None),
    ],
)
def test_budget_selection(low, high, mode, expected):
    assert agent_mod._select_budget(low, high, mode) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("opened https://github.com/o/n/pull/42 thanks", "https://github.com/o/n/pull/42"),
        ("no link here", None),
        ("", None),
        (None, None),
    ],
)
def test_extract_pr_url(text, expected):
    assert extract_pr_url(text) == expected


def test_all_modes_have_templates_and_submit_hint():
    for mode in ("observe", "comment", "pr-draft"):
        template = SYSTEM_TEMPLATES[mode]
        assert "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in template
    assert "NEVER merge" in SYSTEM_TEMPLATES["pr-draft"]
    assert "NEVER open a PR" in SYSTEM_TEMPLATES["comment"]
    assert "read only" in SYSTEM_TEMPLATES["observe"]


def test_make_agent_wiring(cfg, tmp_path, monkeypatch):
    captured: dict = {}

    class FakeModel:
        def __init__(self, **kwargs):
            captured.setdefault("model", []).append(kwargs)

    class FakeEnv:
        def __init__(self, **kwargs):
            captured.setdefault("env", []).append(kwargs)

    class FakeAgent:
        def __init__(self, model, env, **kwargs):
            captured.setdefault("agent", []).append(kwargs)
            self.n_calls = 0
            self.cost = 0.0

    import minisweagent.agents.default as agents_mod
    import minisweagent.environments.local as env_mod
    import minisweagent.models.litellm_model as model_mod

    monkeypatch.setattr(model_mod, "LitellmModel", FakeModel)
    monkeypatch.setattr(env_mod, "LocalEnvironment", FakeEnv)
    monkeypatch.setattr(agents_mod, "DefaultAgent", FakeAgent)

    workdir = tmp_path / "wd"
    make_agent(cfg, workdir, "pr-draft", "sekrit")
    make_agent(cfg, workdir, "observe", None)

    assert captured["model"][0]["model_name"] == "openai/qwen/qwen3.8-27b"
    assert captured["model"][0]["model_kwargs"]["thinking_budget"] == cfg.thinking_budget_high
    assert captured["model"][1]["model_kwargs"]["thinking_budget"] == cfg.thinking_budget_low
    assert captured["env"][0]["cwd"] == str(workdir)
    assert captured["env"][0]["env"]["GH_TOKEN"] == "sekrit"
    assert captured["env"][1]["env"]["GH_TOKEN"] == ""
    assert captured["agent"][0]["step_limit"] == cfg.step_limit
    assert captured["agent"][0]["cost_limit"] == 0
    assert captured["agent"][0]["wall_time_limit_seconds"] == cfg.wall_time_limit_seconds


class _FakeAgent:
    def __init__(self, result=None, exc=None):
        self.result = result or {"exit_status": "Submitted", "submission": ""}
        self.exc = exc
        self.n_calls = 4
        self.cost = 1.25

    def run(self, task, **kwargs):
        if self.exc:
            raise self.exc
        return self.result


def test_run_agent_success_extracts_pr_url(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(
        agent_mod,
        "make_agent",
        lambda *a, **k: _FakeAgent(
            {"exit_status": "Submitted", "submission": "Done. PR: https://github.com/o/n/pull/77"}
        ),
    )
    outcome = agent_mod.run_agent("task", cfg, tmp_path, "pr-draft", "tok")
    assert outcome.exit_status == "Submitted"
    assert outcome.pr_url == "https://github.com/o/n/pull/77"
    assert outcome.n_steps == 4
    assert outcome.cost == 1.25
    assert "77" in outcome.summary


def test_run_agent_crash_is_captured(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(
        agent_mod, "make_agent", lambda *a, **k: _FakeAgent(exc=RuntimeError("model exploded"))
    )
    outcome = agent_mod.run_agent("task", cfg, tmp_path, "observe", None)
    assert outcome.exit_status == "Crashed"
    assert "model exploded" in outcome.summary
    assert outcome.pr_url is None
