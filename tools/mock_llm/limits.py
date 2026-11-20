"""Emulation of the shared course API's limits.

Known (reported by the course, 16 Nov 2026): 1 concurrent request per key, 60
requests/min, 8192 tokens input+output, 1024 max output tokens.

max_tokens above 1024 is rejected with HTTP 400 (verified 20 Nov). UNVERIFIED: whether a second
concurrent request gets 429 or is queued. The README documents this. Token counts here are an
estimate (characters / 4), not the model's tokenizer.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

MAX_CONTEXT_TOKENS = 8192
MAX_OUTPUT_TOKENS = 1024
REQUESTS_PER_MINUTE = 60
WINDOW_SECONDS = 60.0
CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class Rejection:
    status: int
    code: str
    message: str
    retry_after: int | None = None


def estimate_prompt_tokens(body: dict[str, Any]) -> int:
    """Rough prompt size: JSON length of messages (and tools) / 4."""
    payload = {"messages": body.get("messages", []), "tools": body.get("tools", [])}
    return math.ceil(len(json.dumps(payload, ensure_ascii=False)) / CHARS_PER_TOKEN)


def apply_token_budget(body: dict[str, Any]) -> tuple[dict[str, Any], list[str], Rejection | None]:
    """Clamp output tokens and reject requests that cannot fit in the context.

    Returns (new_body, notes, rejection). ``notes`` describe clamping applied.
    """
    body = dict(body)
    notes: list[str] = []
    requested = body.get("max_tokens", body.get("max_completion_tokens"))
    if requested is None:
        out_tokens = MAX_OUTPUT_TOKENS
        notes.append(f"max_tokens defaulted to {MAX_OUTPUT_TOKENS}")
    elif isinstance(requested, int) and requested > MAX_OUTPUT_TOKENS:
        # Verified on the real API (probe, 20 Nov): above the cap it answers HTTP 400, it does not clamp.
        return body, notes, Rejection(400, "invalid_request_error",
                                      f"output token limit must be between 1 and {MAX_OUTPUT_TOKENS}")
    else:
        out_tokens = requested
    if not isinstance(out_tokens, int) or out_tokens < 1:
        return body, notes, Rejection(400, "invalid_request_error", "max_tokens must be a positive integer")

    prompt = estimate_prompt_tokens(body)
    if prompt + out_tokens > MAX_CONTEXT_TOKENS:
        return body, notes, Rejection(
            400,
            "context_length_exceeded",
            f"Estimated {prompt} prompt tokens + {out_tokens} output tokens exceeds the "
            f"{MAX_CONTEXT_TOKENS}-token limit (estimate = characters / {CHARS_PER_TOKEN}).",
        )
    body.pop("max_completion_tokens", None)
    body["max_tokens"] = out_tokens
    return body, notes, None


class Limiter:
    """Sliding-window rate limit plus a single-request concurrency gate."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        queue: bool = False,
        clock: Callable[[], float] = time.monotonic,
        per_minute: int = REQUESTS_PER_MINUTE,
    ) -> None:
        self.enabled = enabled
        self.queue = queue
        self._clock = clock
        self._per_minute = per_minute
        self._stamps: deque[float] = deque()
        self._busy = False
        self._lock = asyncio.Lock()

    def check_rate(self) -> Rejection | None:
        if not self.enabled:
            return None
        now = self._clock()
        while self._stamps and now - self._stamps[0] >= WINDOW_SECONDS:
            self._stamps.popleft()
        if len(self._stamps) >= self._per_minute:
            wait = max(1, math.ceil(WINDOW_SECONDS - (now - self._stamps[0])))
            return Rejection(
                429, "rate_limit_exceeded",
                f"Rate limit of {self._per_minute} requests/min exceeded.", retry_after=wait,
            )
        self._stamps.append(now)
        return None

    async def acquire(self) -> Rejection | None:
        """Take the single request slot. Returns a rejection if busy (reject mode)."""
        if not self.enabled:
            return None
        if self.queue:
            await self._lock.acquire()
            self._busy = True
            return None
        if self._busy:
            return Rejection(
                429, "rate_limit_exceeded",
                "Only 1 concurrent request per key is allowed.", retry_after=1,
            )
        self._busy = True
        return None

    def release(self) -> None:
        if not self.enabled:
            return
        self._busy = False
        if self.queue and self._lock.locked():
            self._lock.release()
