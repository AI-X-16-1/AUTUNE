"""Loads the hand-labeled brief-selection evaluation set.

Same rules as ``eval.topic_linking.dataset``: data, not code, versioned next to
the code that scores it; bump the filename's version suffix on a change that
shifts what counts as correct.

Categories, by which step of ``briefs.choose_previous_meeting`` should answer:

- ``series_exact`` -- the upcoming title equals a past one; the most recent of
  them is the answer even when another meeting is more recent, and even when an
  older one of the series matches the agenda better (the brief recaps the
  meeting this one follows).
- ``series_variant`` -- the same, with titles that differ only in spacing or case.
- ``numbered_series`` -- a series whose titles carry a round, a number or a
  date ("2차" -> "3차", "스프린트 15" -> "16", "10/7" -> "10/14"), so the
  titles never match and the topic step has to find it.
- ``topic_retitled`` -- a different title on the same subject.
- ``generic_title`` -- a title that names nothing ("정기 회의"); only the agenda
  carries the subject.
- ``distractor`` -- several past meetings in the same area, the related one not
  the most recent.
- ``unrelated``, ``same_domain``, ``shared_keyword`` -- nothing is related; the
  answer is ``latest``, under which the brief posts none of that meeting's
  content (``notify.build_pre_meeting_brief``, ``recap_is_related``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

_DATA_DIR = files(__package__) / "fixtures"
_DEFAULT_DATASET = "brief_selection_v1.json"


@dataclass(frozen=True)
class PastMeeting:
    title: str
    days_ago: int
    lines: list[str]
    """One utterance's text per line, as in the topic-linking set."""


@dataclass(frozen=True)
class Upcoming:
    title: str
    agenda: list[str]
    """Issue titles, as module B would publish them in a ``TeamAgenda``."""


@dataclass(frozen=True)
class BriefCase:
    id: str
    category: str
    past_meetings: list[PastMeeting]
    upcoming: Upcoming
    expected: int | None
    """Index into ``past_meetings`` of the meeting the brief should recap, or
    ``None`` when none is related and the brief should fall back to ``latest``."""
    related: frozenset[int]
    """Every past meeting on the same subject; contains ``expected``."""


def load_cases(name: str = _DEFAULT_DATASET) -> list[BriefCase]:
    raw = json.loads((_DATA_DIR / name).read_text(encoding="utf-8"))
    if raw["version"] != 1:
        raise ValueError(f"{name}: unknown eval set version {raw['version']!r}")
    return [_parse_case(case) for case in raw["cases"]]


def _parse_case(raw: dict[str, Any]) -> BriefCase:
    case = BriefCase(
        id=raw["id"],
        category=raw["category"],
        past_meetings=[
            PastMeeting(title=m["title"], days_ago=m["days_ago"], lines=list(m["lines"]))
            for m in raw["past_meetings"]
        ],
        upcoming=Upcoming(
            title=raw["upcoming"]["title"], agenda=list(raw["upcoming"].get("agenda", []))
        ),
        expected=raw["expected"],
        related=frozenset(raw.get("related", [])),
    )
    if case.expected is not None and case.expected not in case.related:
        raise ValueError(f"{case.id}: expected {case.expected} is not in related")
    if case.expected is None and case.related:
        raise ValueError(f"{case.id}: related meetings but no expected one")
    return case
