"""Shared HTTP plumbing for the chat and embeddings endpoints.

- TLS: each endpoint can have its own CA file.
- Retry: 429/503 are retried after 2, 4, 8 s (or the server's Retry-After).
- Concurrency: the course API allows one request per key at a time, so every
  chat call in this process goes through one lock.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from hr_navigator.llm.config import Endpoint

BACKOFF_SECONDS = (2, 4, 8)
RETRY_STATUS = {429, 503}
TIMEOUT_SECONDS = 120.0

# One lock for the whole process: the shared key allows 1 concurrent request.
CHAT_LOCK = threading.Lock()


class LLMHTTPError(Exception):
    """The endpoint answered with an error we will not retry (4xx, bad body)."""

    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class LLMCapacityError(LLMHTTPError):
    """Still 429/503 after every retry."""


def make_client(endpoint: Endpoint, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """httpx client with the endpoint's CA file and bearer key."""
    verify: Any = endpoint.ca_cert or True
    return httpx.Client(
        base_url=endpoint.base_url,
        headers={"Authorization": f"Bearer {endpoint.api_key}"},
        verify=verify,
        timeout=TIMEOUT_SECONDS,
        transport=transport,
    )


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return None


def post_json(
    client: httpx.Client,
    path: str,
    body: dict[str, Any],
    *,
    lock: threading.Lock | None = None,
    sleep=time.sleep,
) -> dict[str, Any]:
    """POST ``body`` and return the JSON reply, retrying on 429/503.

    Error messages carry the status and the server's text, never the request headers.
    """
    for attempt in range(len(BACKOFF_SECONDS) + 1):
        if lock is not None:
            with lock:
                response = client.post(path, json=body)
        else:
            response = client.post(path, json=body)

        if response.status_code not in RETRY_STATUS:
            if response.status_code >= 400:
                raise LLMHTTPError(response.status_code, response.text[:500])
            try:
                return response.json()
            except ValueError as exc:
                raise LLMHTTPError(response.status_code, "reply was not JSON") from exc

        if attempt == len(BACKOFF_SECONDS):
            raise LLMCapacityError(response.status_code, response.text[:500])
        sleep(_retry_after(response) or BACKOFF_SECONDS[attempt])
    raise AssertionError("unreachable")
