"""Loads the hand-labeled topic-linking evaluation set.

Kept as data, not code, next to ``topic_linking_v1.json`` so a change to the
labels shows up as a data diff rather than a Python diff. Bump the filename's
version suffix on a change that shifts what counts as correct — a metric that
moves because the set changed underneath it is not a metric (see
docs/engineering/testing.md, "Evaluation"). Fixing a typo in a Korean sentence
without changing the expected outcome does not need a bump.

Mirrors ``autune_contracts.fixtures``'s ``importlib.resources`` pattern.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Any

_DATA_DIR = files(__package__) / "fixtures"
_DEFAULT_DATASET = "topic_linking_v1.json"


@dataclass(frozen=True)
class EvalMeeting:
    days_ago: int
    lines: list[str]
    """One utterance's text per line. Short and repetitive on purpose — this
    harness scores retrieval and re-ranking, not topic segmentation, so each
    meeting is written to land in a single TextTiling segment."""


@dataclass(frozen=True)
class EvalCase:
    id: str
    past_meetings: list[EvalMeeting]
    current_meeting: EvalMeeting
    expected_linked_indices: frozenset[int] = field(default_factory=frozenset)
    """Indices into ``past_meetings`` the current meeting's topics should
    *assert* a link to. Empty means: no past meeting is a real match, so any
    asserted link here is a false positive."""


def load_cases(name: str = _DEFAULT_DATASET) -> list[EvalCase]:
    raw = json.loads((_DATA_DIR / name).read_text(encoding="utf-8"))
    if raw["version"] != 1:
        raise ValueError(f"{name}: unknown eval set version {raw['version']!r}")
    return [_parse_case(case) for case in raw["cases"]]


def _parse_case(raw: dict[str, Any]) -> EvalCase:
    return EvalCase(
        id=raw["id"],
        past_meetings=[_parse_meeting(m) for m in raw["past_meetings"]],
        current_meeting=_parse_meeting(raw["current_meeting"]),
        expected_linked_indices=frozenset(raw.get("expected_linked_indices", [])),
    )


def _parse_meeting(raw: dict[str, Any]) -> EvalMeeting:
    return EvalMeeting(days_ago=raw["days_ago"], lines=list(raw["lines"]))
