"""Shared fixtures, so all five modules test against the same payloads.

Fixtures contain synthetic text only. Never commit a real meeting transcript,
even masked. See docs/engineering/testing.md.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

_DIR = files(__package__) / "fixtures"

NAMES = (
    "transcript_ready.short",
    "transcript_ready.typical",
    "transcript_ready.unidentified",
    "extraction_result",
    "gap_report",
    "context_links",
    "intelligence_snapshot",
)


def load(name: str) -> dict[str, Any]:
    """Return a fixture payload as a plain dict, ready for ``model_validate``."""
    if name not in NAMES:
        raise KeyError(f"unknown fixture {name!r}; available: {', '.join(NAMES)}")
    return json.loads((_DIR / f"{name}.json").read_text(encoding="utf-8"))
