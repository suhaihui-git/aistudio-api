"""Timeout helpers for AI Studio completion requests."""

from __future__ import annotations

TOKEN_TIMEOUT_RATE = 30
TOKEN_TIMEOUT_GRACE_SECONDS = 60


def completion_timeout_seconds(*, max_tokens: int | None, base_seconds: int) -> int:
    """Scale total request timeout for large requested completions."""
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        return base_seconds

    token_seconds = (max_tokens + TOKEN_TIMEOUT_RATE - 1) // TOKEN_TIMEOUT_RATE
    return max(base_seconds, token_seconds + TOKEN_TIMEOUT_GRACE_SECONDS)
