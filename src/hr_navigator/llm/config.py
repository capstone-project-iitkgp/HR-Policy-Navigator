"""Endpoint settings from the environment (and a local ``.env``).

The key is only ever read here and passed to the HTTP layer. It is never logged,
cached or included in an exception message.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines into os.environ without overriding existing values."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass(frozen=True)
class Endpoint:
    base_url: str
    model: str
    api_key: str = field(repr=False)  # repr=False: never shows up in logs or tracebacks
    ca_cert: str | None = None  # path to the endpoint's own CA file, or None for system CAs


def _require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return value


def chat_endpoint() -> Endpoint:
    load_dotenv(Path(".env"))
    return Endpoint(
        base_url=_require("CHAT_BASE_URL").rstrip("/"),
        model=_require("CHAT_MODEL"),
        api_key=_require("API_KEY"),
        ca_cert=os.environ.get("CAPSTONE_CA_CERT") or None,
    )


def embed_endpoint() -> Endpoint:
    """Embeddings use their own URL, model, key and CA (a different VM)."""
    load_dotenv(Path(".env"))
    return Endpoint(
        base_url=_require("EMBED_BASE_URL").rstrip("/"),
        model=_require("EMBED_MODEL"),
        api_key=os.environ.get("EMBED_API_KEY") or _require("API_KEY"),
        ca_cert=os.environ.get("EMBED_CA_CERT") or None,
    )
