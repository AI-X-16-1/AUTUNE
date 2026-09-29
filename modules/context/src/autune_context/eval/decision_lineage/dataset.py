"""Loads the hand-labeled decision-lineage evaluation set.

Same shape and same rules as ``eval.topic_linking.dataset``: data, not code,
versioned next to the code it scores; bump the filename's version suffix only
when a change would make an old accuracy number and a new one not comparable.

How ``expected_change_type`` is labeled, from the product's point of view
rather than from what NLI would say (see ``pipeline.change`` for how NLI output
becomes a change type — the whole point of the set is to measure where the two
disagree):

- ``unchanged`` — the current statement restates the earlier one, in the same
  or different words; nothing anyone would act on differs.
- ``modified`` — the decision still stands, but a parameter moved (a date, an
  amount, a headcount, an owner) or its scope narrowed, widened, or gained a
  condition. A date pushed from 3/15 to 3/29 is ``modified``, even though the
  two sentences cannot both be literally true.
- ``reversed`` — the earlier decision is withdrawn (cancelled, shelved,
  abolished) or replaced by an alternative that excludes it (MySQL ->
  PostgreSQL, outsourced -> in-house).
- ``new`` — a different decision, even when it sits in the same area as an
  earlier one (a marketing *channel* decision is not the marketing *budget*
  decision).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

_DATA_DIR = files(__package__) / "fixtures"
_DEFAULT_DATASET = "decision_lineage_v2.json"


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
    category: str = "uncategorized"
    """What the case is testing, for the runner's per-category breakdown. v2
    splits each change type into how it is phrased (``unchanged_restated`` /
    ``unchanged_paraphrase``, ``modified_param`` / ``modified_scope``,
    ``reversed_cancel`` / ``reversed_alternative``, ``new_unrelated`` /
    ``new_same_domain``), plus ``distractor`` (several past decisions, one
    right thread) and ``chain`` (the past decisions already form a thread)."""


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
        category=raw.get("category", "uncategorized"),
    )


def _parse_meeting(raw: dict[str, Any]) -> EvalMeeting:
    return EvalMeeting(days_ago=raw["days_ago"], decision=raw["decision"])
