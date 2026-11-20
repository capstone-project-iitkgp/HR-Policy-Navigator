"""Versioned prompts. Files are ``<name>_v<N>.md``; ``load`` returns the newest unless told otherwise.

The hash goes into ``RunRecord.prompt_hash`` so every result says which prompt produced it.
Placeholders look like ``{{name}}``; ``Prompt.render`` fills them and refuses to leave one empty.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

DIR = Path(__file__).parent
NAMES = ("planner", "executor", "critic", "answer")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    text: str

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:12]

    def render(self, **values: str) -> str:
        out = self.text
        for key, val in values.items():
            out = out.replace("{{" + key + "}}", str(val))
        left = re.findall(r"\{\{(\w+)\}\}", out)
        if left:
            raise ValueError(f"prompt {self.name} v{self.version}: no value for {sorted(set(left))}")
        return out


def load(name: str, version: int | None = None) -> Prompt:
    if name not in NAMES:
        raise ValueError(f"unknown prompt '{name}'. Known: {', '.join(NAMES)}")
    found = {int(m.group(1)): p for p in DIR.glob(f"{name}_v*.md") if (m := re.fullmatch(rf"{name}_v(\d+)\.md", p.name))}
    if not found:
        raise FileNotFoundError(f"no file {name}_v<N>.md in {DIR}")
    v = version if version is not None else max(found)
    return Prompt(name=name, version=v, text=found[v].read_text(encoding="utf-8").strip())
