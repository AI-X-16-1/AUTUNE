"""Loads the hand-labeled decision-lineage evaluation set.

Same shape and same rules as ``eval.topic_linking.dataset``: data, not code,
versioned next to the code it scores; bump the filename's version suffix only
when a change would make an old accuracy number and a new one not comparable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

_DATA_DIR = files(__package__) / "fixtures"
_DEFAULT_DATASET = "decision_lineage_v1.json"


@dataclass(frozen=True)
class EvalMeeting:
    days_ago: int
    decision: str
    """One decision statement. One decision per meeting keeps which version
    should match which unambiguous — see ``runner``'s docstring."""


@dataclass(frozen=True)
class EvalCase:
    id: str
    past_meetings: list[EvalMeeting]
    current_meeting: EvalMeeting
    expected_thread_source: int | None
    """Index into ``past_meetings`` the current decision should thread onto,
    or ``None`` if it should open a new thread instead."""
    expected_change_type: str
    """One of ``autune_contracts.ChangeType``'s values. Must be ``"new"`` iff
    ``expected_thread_source`` is ``None`` — enforced in ``_parse_case``."""


def load_cases(name: str = _DEFAULT_DATASET) -> list[EvalCase]:
    raw = json.loads((_DATA_DIR / name).read_text(encoding="utf-8"))
    if raw["version"] != 1:
        raise ValueError(f"{name}: unknown eval set version {raw['version']!r}")
    return [_parse_case(case) for case in raw["cases"]]


def _parse_case(raw: dict[str, Any]) -> EvalCase:
    source = raw["expected_thread_source"]
    change_type = raw["expected_change_type"]
    if (source is None) != (change_type == "new"):
        raise ValueError(
            f"{raw['id']}: expected_thread_source={source!r} and "
            f"expected_change_type={change_type!r} disagree on whether this is a new thread"
        )
    return EvalCase(
        id=raw["id"],
        past_meetings=[_parse_meeting(m) for m in raw["past_meetings"]],
        current_meeting=_parse_meeting(raw["current_meeting"]),
        expected_thread_source=source,
        expected_change_type=change_type,
    )


def _parse_meeting(raw: dict[str, Any]) -> EvalMeeting:
    return EvalMeeting(days_ago=raw["days_ago"], decision=raw["decision"])
