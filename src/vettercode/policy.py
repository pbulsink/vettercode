"""Per-repo policy (AGENTS.md) and mode downgrade rules.

Modes, most to least permissive: pr-draft > comment > observe.
A repo can restrict the agent via AGENTS.md markers (case-insensitive):
  - `allow_pr: false` (or `no-pr`)      -> disable pr-draft
  - `allow_comment: false` (or `no-comments`) -> disable commenting
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

AGENTS_MD_NAME = "AGENTS.md"

_NO_PR = re.compile(r"allow[-_ ]?pr\s*[:=]?\s*(?:no|false|off)|\bno[-_ ]?pr\b", re.IGNORECASE)
_NO_COMMENT = re.compile(
    r"allow[-_ ]?comment(?:s)?\s*[:=]?\s*(?:no|false|off)|\bno[-_ ]?comments?\b", re.IGNORECASE
)


@dataclass(frozen=True)
class Policy:
    allow_comment: bool
    allow_pr: bool
    source: str  # "AGENTS.md" or "default"


DEFAULT_POLICY = Policy(allow_comment=True, allow_pr=True, source="default")

MODE_RANK = {"observe": 0, "comment": 1, "pr-draft": 2}


def load_policy(workdir: Path | None) -> Policy:
    """Read AGENTS.md from the cloned workdir (if any) and derive a policy."""
    if workdir is None:
        return DEFAULT_POLICY
    agents_md = workdir / AGENTS_MD_NAME
    if not agents_md.is_file():
        return DEFAULT_POLICY
    try:
        text = agents_md.read_text(errors="replace")
    except OSError:
        return DEFAULT_POLICY
    return Policy(
        allow_comment=not _NO_COMMENT.search(text),
        allow_pr=not _NO_PR.search(text),
        source=AGENTS_MD_NAME,
    )


def downgrade_mode(mode: str, policy: Policy) -> tuple[str, str | None]:
    """Return (effective_mode, reason). Downgrades pr-draft->comment->observe
    according to the repo policy. `reason` explains a downgrade, else None."""
    if mode == "pr-draft" and not policy.allow_pr:
        return "comment", f"repo {policy.source} disallows PRs"
    if mode in ("comment", "pr-draft") and not policy.allow_comment:
        return "observe", f"repo {policy.source} disallows comments"
    return mode, None


def mode_rank(mode: str) -> int:
    return MODE_RANK.get(mode, 0)
