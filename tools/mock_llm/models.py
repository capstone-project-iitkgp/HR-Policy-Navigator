"""Model registry: which local model the stand-in serves, and under what name.

The proxy always reports a fixed *alias* to clients, so application code can keep
one ``CHAT_MODEL`` value regardless of which local weights are loaded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    key: str  # value of --model
    label: str  # shown in the interactive menu
    alias: str  # model name reported to clients
    patterns: tuple[str, ...]  # normalised substrings that identify the backend id
    prefer: tuple[str, ...] = ()  # normalised substrings that win ties
    note: str = ""


REGISTRY: dict[str, ModelSpec] = {
    "qwen": ModelSpec(
        key="qwen",
        label="Qwen3-8B (same model as the team API; not allowed on the office laptop)",
        alias="Qwen/Qwen3-8B",
        patterns=("qwen38b",),
        note="Real Qwen3-8B weights. Load them only on a machine where that is permitted.",
    ),
    "llama": ModelSpec(
        key="llama",
        label="Llama 3.1 8B Instruct",
        alias="meta-llama/Llama-3.1-8B-Instruct",
        patterns=("llama318b",),
        prefer=("instruct",),
    ),
}


def normalise(model_id: str) -> str:
    """Lower-case and drop everything except letters and digits.

    ``meta-llama-3.1-8b-instruct`` -> ``metallama318binstruct``; ``llama3.1:8b`` ->
    ``llama318b``; ``qwen/qwen3-8b`` -> ``qwenqwen38b``.
    """
    return re.sub(r"[^a-z0-9]", "", model_id.lower())


def match_backend_model(spec: ModelSpec, backend_ids: list[str]) -> str | None:
    """Pick the backend model id that matches ``spec``, or None.

    Embedding models are ignored. Among several matches, ids containing a
    ``prefer`` token win; otherwise the first match is used.
    """
    matches: list[str] = []
    for model_id in backend_ids:
        norm = normalise(model_id)
        if "embed" in norm:
            continue
        if any(p in norm for p in spec.patterns):
            matches.append(model_id)
    if not matches:
        return None
    for model_id in matches:
        norm = normalise(model_id)
        if any(p in norm for p in spec.prefer):
            return model_id
    return matches[0]
