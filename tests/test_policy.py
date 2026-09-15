from vettercode.policy import (
    DEFAULT_POLICY,
    Policy,
    downgrade_mode,
    load_policy,
    mode_rank,
)


def test_default_when_no_workdir():
    assert load_policy(None) == Policy(True, True, "default")
    assert load_policy(None) == DEFAULT_POLICY


def test_default_when_no_agents_md(tmp_path):
    assert load_policy(tmp_path) == DEFAULT_POLICY


def test_default_when_agents_md_unreadable(tmp_path, monkeypatch):
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("allow_pr: false\n")

    from pathlib import Path

    real_read_text = Path.read_text

    def boom(self, *a, **k):
        if self == agents_md:
            raise OSError("permission denied")
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", boom)
    assert load_policy(tmp_path) == DEFAULT_POLICY


def test_disallow_pr_via_flag(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Rules\nallow_pr: false\n")
    policy = load_policy(tmp_path)
    assert policy.allow_pr is False
    assert policy.allow_comment is True
    assert policy.source == "AGENTS.md"


def test_disallow_pr_via_marker(tmp_path):
    (tmp_path / "AGENTS.md").write_text("vettercode: no-pr please\n")
    assert load_policy(tmp_path).allow_pr is False


def test_disallow_comments(tmp_path):
    (tmp_path / "AGENTS.md").write_text("allow_comment: false\n")
    policy = load_policy(tmp_path)
    assert policy.allow_comment is False
    assert policy.allow_pr is True


def test_case_insensitive(tmp_path):
    (tmp_path / "AGENTS.md").write_text("ALLOW_PR: FALSE\n")
    assert load_policy(tmp_path).allow_pr is False


def test_downgrade_pr_draft_to_comment():
    mode, reason = downgrade_mode("pr-draft", Policy(False, False, "AGENTS.md"))
    assert mode == "comment"
    assert "disallows PRs" in (reason or "")


def test_downgrade_comment_to_observe():
    mode, reason = downgrade_mode("comment", Policy(False, True, "AGENTS.md"))
    assert mode == "observe"
    assert reason is not None


def test_downgrade_is_one_step_at_a_time():
    # pr-draft is downgraded one level; a caller applying the policy again
    # reaches observe (see next test).
    mode, _ = downgrade_mode("pr-draft", Policy(False, False, "AGENTS.md"))
    assert mode == "comment"


def test_pr_draft_to_comment_then_comment_respects_comments_policy():
    # Second application models what a caller would do if comment also blocked:
    policy = Policy(False, False, "AGENTS.md")
    first, _ = downgrade_mode("pr-draft", policy)
    second, _ = downgrade_mode(first, policy)
    assert second == "observe"


def test_no_downgrade_needed():
    assert downgrade_mode("observe", DEFAULT_POLICY) == ("observe", None)
    assert downgrade_mode("pr-draft", DEFAULT_POLICY) == ("pr-draft", None)


def test_mode_rank_ordering():
    assert mode_rank("observe") < mode_rank("comment") < mode_rank("pr-draft")
    assert mode_rank("unknown") == 0
