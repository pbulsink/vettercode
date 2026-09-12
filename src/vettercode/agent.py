"""Agent execution via the mini-swe-agent v2 library.

Modes (permissive -> strict):
  pr-draft: investigate, implement, commit, push `vettercode/*` branch, open DRAFT PR.
  comment:  investigate, then leave a single informative comment on the issue.
  observe:  read-only investigation, produce a summary. No writes, no network writes.

Thinking budget: observe/comment use `thinking_budget_low`, pr-draft uses
`thinking_budget_high` (LM Studio `thinking_budget` extra-body param; the
`reasoning_effort` param is unreliable upstream, so budgets are used instead).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

# Silence mini-swe-agent's startup banner before it is imported.
os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")

PR_URL_PATTERN = re.compile(r"https://github\.com/[\w.\-]+/[\w.\-]+/pull/\d+")

# The agent finishes by emitting a heredoc whose FIRST line is the completion
# marker; every line after it becomes the submission text (mini-swe-agent v2
# LocalEnvironment._check_finished contract).
SUBMIT_HINT = (
    "- To finish, run exactly one command block of this shape (the lines after the marker "
    "become your final submission):\n"
    "      cat <<'VETTERCODE_EOF'\n"
    "      COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n"
    "      <your final summary text>\n"
    "      VETTERCODE_EOF\n"
)

SYSTEM_TEMPLATES: dict[str, str] = {
    "observe": (
        "You are vettercode, an automated GitHub issue triage agent running on the user's machine.\n"
        "You operate inside a throw-away clone of the repository at the current working directory.\n\n"
        "STRICT RULES (observe mode - read only):\n"
        "- You may READ files and inspect the codebase. You may NOT create, edit or delete files.\n"
        "- You may NOT run git commit/push, gh pr create, gh issue comment, or any write command.\n"
        "- Allowed commands: ls, cat, grep, rg, find, git log/status/diff (read-only), etc.\n"
        "- Produce a concise investigation summary: what the issue is about, likely root cause, "
        "relevant files, and a recommended next step.\n"
        + SUBMIT_HINT
    ),
    "comment": (
        "You are vettercode, an automated GitHub issue agent running on the user's machine.\n"
        "You operate inside a throw-away clone of the repository at the current working directory.\n\n"
        "RULES (comment mode):\n"
        "- Investigate the issue by reading the codebase (read-only commands are always allowed).\n"
        "- You may NOT create, edit or delete repository files, and may NOT commit or push code.\n"
        "- When done, leave exactly ONE informative comment on the issue using the `gh` CLI:\n"
        "    gh issue comment <NUMBER> --repo <OWNER/NAME> --body-file /tmp/vettercode-comment.md\n"
        "  (write the comment text to that file first with a here-doc). The comment must be useful:\n"
        "  findings, likely root cause, suggested fix direction. Start it with '🤖 vettercode findings:'.\n"
        "- NEVER open a PR in this mode. NEVER merge anything.\n"
        + SUBMIT_HINT
    ),
    "pr-draft": (
        "You are vettercode, an automated GitHub issue-fixing agent running on the user's machine.\n"
        "You operate inside a throw-away clone of the repository at the current working directory.\n\n"
        "RULES (pr-draft mode):\n"
        "1. Investigate the issue; reproduce/verify against the codebase where feasible.\n"
        "2. If a fix is straightforward and safe: implement it, then\n"
        "     git checkout -b <BRANCH>\n"
        "     git add -A && git commit -m 'fix(<area>): <short description> (vettercode)'\n"
        "     git push -u origin <BRANCH>\n"
        "3. Create a DRAFT pull request with the `gh` CLI:\n"
        "     gh pr create --draft --title '...' --body '...'\n"
        "   The PR body must start with '🤖 Created by vettercode for issue <NUMBER>.' and explain "
        "the change and how it was verified.\n"
        "4. If the issue is unclear, unfixable, or a fix is risky: do NOT open a PR. Instead explain "
        "why in your final summary.\n"
        "ABSOLUTE RULES:\n"
        "- NEVER merge a PR. NEVER force-push. NEVER push to the default branch.\n"
        "- Only touch files relevant to the issue.\n"
        "- When finished (PR opened, or decision not to), submit your final text; "
        "if a PR was created include its full URL.\n"
        + SUBMIT_HINT
    ),
}

INSTANCE_TEMPLATE = """\
Please handle the following GitHub issue according to your mode rules.

<task>
{{task}}
</task>

Working directory: the repository clone (current directory).
Branch prefix (if you create a branch): {{branch_prefix}}
"""


@dataclass(frozen=True)
class AgentOutcome:
    exit_status: str
    summary: str
    pr_url: str | None
    cost: float
    n_steps: int


def build_task_prompt(
    owner: str,
    name: str,
    number: int,
    title: str,
    body: str,
    comments: list[dict],
    mode: str,
    max_comments: int = 20,
) -> str:
    comment_lines = []
    for comment in comments[:max_comments]:
        author = comment.get("user", {}).get("login", "?")
        text = (comment.get("body") or "").strip()
        comment_lines.append(f"[{author}] {text}")
    return (
        f"Repo: {owner}/{name}\n"
        f"Issue #{number}: {title}\n"
        f"Mode: {mode}\n\n"
        f"Issue body:\n{body or '(empty)'}\n\n"
        f"Comments ({len(comments)} total, showing up to {max_comments}):\n"
        + ("\n".join(comment_lines) if comment_lines else "(none)")
        + "\n"
    )


_NON_OPENAI_PROVIDERS = (
    "openai/",
    "anthropic/",
    "gemini/",
    "groq/",
    "mistral/",
    "bedrock/",
    "together_ai/",
    "azure/",
    "vertex_ai/",
    "openrouter/",
    "ollama/",
)


def model_name_for(raw_model: str) -> str:
    """Ensure the litellm model name targets an OpenAI-compatible endpoint
    (e.g. LM Studio): prefix with `openai/` unless already provider-qualified."""
    if raw_model.startswith(_NON_OPENAI_PROVIDERS):
        return raw_model
    return f"openai/{raw_model}"


def model_kwargs_for(base_url: str, thinking_budget: int | None) -> dict:
    """Extra body params for the LM Studio endpoint."""
    kwargs = {"api_base": base_url, "api_key": "lm-studio"}
    if thinking_budget:
        kwargs["thinking"] = True
        kwargs["thinking_budget"] = int(thinking_budget)
    return kwargs


def _select_budget(budget_low: int | None, budget_high: int | None, mode: str) -> int | None:
    if mode == "pr-draft":
        return budget_high
    return budget_low


def make_agent(cfg, workdir: Path, mode: str, token: str | None):
    """Build a configured mini-swe-agent DefaultAgent. Kept separate for testability."""
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    from minisweagent.models.litellm_model import LitellmModel

    model = LitellmModel(
        model_name=model_name_for(cfg.lmstudio_model),
        model_kwargs=model_kwargs_for(
            cfg.lmstudio_base_url,
            _select_budget(cfg.thinking_budget_low, cfg.thinking_budget_high, mode),
        ),
        cost_tracking="ignore_errors",
    )
    env = LocalEnvironment(
        cwd=str(workdir),
        env={"GH_TOKEN": token or "", "GIT_TERMINAL_PROMPT": "0"},
        timeout=cfg.command_timeout_seconds,
    )
    return DefaultAgent(
        model=model,
        env=env,
        system_template=SYSTEM_TEMPLATES[mode],
        instance_template=INSTANCE_TEMPLATE,
        step_limit=cfg.step_limit,
        cost_limit=0,  # 0 = no cost limit
        wall_time_limit_seconds=cfg.wall_time_limit_seconds,
    )


def extract_pr_url(text: str | None) -> str | None:
    if not text:
        return None
    match = PR_URL_PATTERN.search(text)
    return match.group(0) if match else None


def run_agent(
    task: str,
    cfg,
    workdir,
    mode: str,
    token: str | None,
    branch_prefix: str = "vettercode/",
) -> AgentOutcome:
    """Run the agent for one issue; never raises for agent-level failures.

    Agent-level problems (format errors, limits, model errors) are captured in
    the outcome instead of crashing the whole run.
    """
    from .logsetup import get_logger

    log = get_logger()
    agent = make_agent(cfg, workdir, mode, token)
    extra = {"task": task, "branch_prefix": branch_prefix.rstrip("/")}
    try:
        result = agent.run(**extra)
    except Exception as e:  # noqa: BLE001 - agent failures must not kill the run
        log.error("agent crashed: %s: %s", type(e).__name__, e)
        return AgentOutcome(exit_status="Crashed", summary=str(e)[:2000], pr_url=None, cost=0.0, n_steps=agent.n_calls)

    exit_status = str(result.get("exit_status", "Unknown"))
    submission = str(result.get("submission") or "")
    pr_url = extract_pr_url(submission)
    summary = submission.strip() or "(agent produced no submission text)"
    log.info(
        "agent finished: exit_status=%s steps=%s cost=%.4f",
        exit_status, agent.n_calls, getattr(agent, "cost", 0.0),
    )
    return AgentOutcome(
        exit_status=exit_status,
        summary=summary[-4000:],
        pr_url=pr_url,
        cost=float(getattr(agent, "cost", 0.0) or 0.0),
        n_steps=int(getattr(agent, "n_calls", 0) or 0),
    )
