"""Issue filtering: @vettercode mention detection."""

from __future__ import annotations

import re

MENTION_PATTERN = re.compile(r"@vettercode\b", re.IGNORECASE)


def has_vettercode_mention(issue_body: str | None, comments: list[dict] | None) -> bool:
    """True when the issue body or any comment mentions @vettercode."""
    if issue_body and MENTION_PATTERN.search(issue_body):
        return True
    for comment in comments or []:
        body = comment.get("body") or ""
        if MENTION_PATTERN.search(body):
            return True
    return False
