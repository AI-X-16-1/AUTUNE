"""Loads the hand-labeled topic-linking evaluation set.

Kept as data, not code, next to ``topic_linking_v*.json`` so a change to the
labels shows up as a data diff rather than a Python diff. Bump the filename's
version suffix on a change that shifts what counts as correct — a metric that
moves because the set changed underneath it is not a metric (see
docs/engineering/testing.md, "Evaluation"). Fixing a typo in a Korean sentence
without changing the expected outcome does not need a bump.

Mirrors ``autune_contracts.fixtures``'s ``importlib.resources`` pattern.

Two file versions. ``version: 1`` is a list of independent cases, each a
handful of short past meetings and one current meeting. ``version: 2`` is a
list of *series*: one team's meetings in order, each a run of blocks tagged
with the topic they are about (or ``null`` for openings, closings and small
talk). A series is scored the way production sees it -- every meeting from the
second on is a current meeting with all the earlier ones as its history -- and
what each one should link to is derived from the tags, not written by hand: the
earlier meetings that share at least one topic with it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Any

_DATA_DIR = files(__package__) / "fixtures"
_DEFAULT_DATASET = "topic_linking_v2.json"


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
    category: str = "uncategorized"
    """What the case is testing, for the runner's per-category breakdown. v2's
    categories: ``follow_up`` (same topic, shared wording), ``paraphrase``
    (same topic, little shared wording), ``recurring`` (several past meetings
    all match), ``distractor`` (only some of several past meetings match),
    ``multi_topic`` (a six-utterance meeting carrying two topics, so
    segmentation is in play), ``unrelated``, ``shared_keyword`` (a shared word
    with a different meaning) and ``same_domain`` (same area, different
    subject) — the last three expect no link. long_meeting and
    large_history (six to nine past meetings, so a shortlist of candidates is a
    real restriction) were added by the v3 held-out set."""


@dataclass(frozen=True)
class SeriesBlock:
    topic: str | None
    """A key of ``EvalSeries.topics``, or ``None`` for an opening, a closing,
    small talk or anything else no meeting should be linked by."""
    lines: list[str]


@dataclass(frozen=True)
class SeriesMeeting:
    days_ago: int
    blocks: list[SeriesBlock]

    @property
    def lines(self) -> list[str]:
        return [line for block in self.blocks for line in block.lines]

    @property
    def line_topics(self) -> list[str | None]:
        """The topic of each line in ``lines``, by position."""
        return [block.topic for block in self.blocks for _ in block.lines]

    @property
    def topic_set(self) -> frozenset[str]:
        return frozenset(block.topic for block in self.blocks if block.topic is not None)


@dataclass(frozen=True)
class EvalSeries:
    id: str
    topics: dict[str, list[str]]
    """Topic key -> words a good label for it would contain. A segment's label
    is counted as naming its topic when it contains one of them."""
    meetings: list[SeriesMeeting]
    """Oldest first, ``days_ago`` strictly decreasing."""

    def expected_linked_indices(self, index: int) -> frozenset[int]:
        """The earlier meetings that share a topic with meeting ``index``."""
        mine = self.meetings[index].topic_set
        return frozenset(j for j in range(index) if self.meetings[j].topic_set & mine)


def load_cases(name: str = _DEFAULT_DATASET) -> list[EvalCase | EvalSeries]:
    raw = json.loads((_DATA_DIR / name).read_text(encoding="utf-8"))
    if raw["version"] == 1:
        return [_parse_case(case) for case in raw["cases"]]
    if raw["version"] == 2:
        return [_parse_series(series) for series in raw["series"]]
    raise ValueError(f"{name}: unknown eval set version {raw['version']!r}")


def _parse_series(raw: dict[str, Any]) -> EvalSeries:
    topics = {key: list(words) for key, words in raw["topics"].items()}
    meetings = [
        SeriesMeeting(
            days_ago=m["days_ago"],
            blocks=[SeriesBlock(topic=b["topic"], lines=list(b["lines"])) for b in m["blocks"]],
        )
        for m in raw["meetings"]
    ]
    for meeting in meetings:
        unknown = meeting.topic_set - topics.keys()
        if unknown:
            raise ValueError(f"{raw['id']}: blocks tagged with undeclared topics {sorted(unknown)}")
    days = [m.days_ago for m in meetings]
    if any(a <= b for a, b in zip(days, days[1:], strict=False)):
        raise ValueError(f"{raw['id']}: meetings must be oldest first, days_ago {days}")
    return EvalSeries(id=raw["id"], topics=topics, meetings=meetings)


def _parse_case(raw: dict[str, Any]) -> EvalCase:
    return EvalCase(
        id=raw["id"],
        past_meetings=[_parse_meeting(m) for m in raw["past_meetings"]],
        current_meeting=_parse_meeting(raw["current_meeting"]),
        expected_linked_indices=frozenset(raw.get("expected_linked_indices", [])),
        category=raw.get("category", "uncategorized"),
    )


def _parse_meeting(raw: dict[str, Any]) -> EvalMeeting:
    return EvalMeeting(days_ago=raw["days_ago"], lines=list(raw["lines"]))
