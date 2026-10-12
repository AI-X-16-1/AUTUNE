"""The evaluation sets parse, and the series format derives what it should.

No database and no model: these pin the loaders and the scoring helpers that
read a result against the block tags, not any accuracy number.
"""

from __future__ import annotations

import pytest

from autune_context.eval.decision_lineage import dataset as lineage_dataset
from autune_context.eval.topic_linking import dataset as topic_dataset
from autune_context.eval.topic_linking import runner as topic_runner
from autune_context.eval.topic_linking.dataset import (
    EvalCase,
    EvalSeries,
    SeriesBlock,
    SeriesMeeting,
)
from autune_context.eval.topic_linking.runner import SegmentOutcome


def _series(*topic_sets: tuple[str, ...]) -> EvalSeries:
    meetings = [
        SeriesMeeting(
            days_ago=10 * (len(topic_sets) - i),
            blocks=[
                SeriesBlock(topic=None, lines=["시작할게요."]),
                *(SeriesBlock(topic=t, lines=[f"{t} 얘기", f"{t} 정리"]) for t in topics),
            ],
        )
        for i, topics in enumerate(topic_sets)
    ]
    return EvalSeries(id="s", topics={"a": ["에이"], "b": ["비"], "c": ["씨"]}, meetings=meetings)


def test_expected_links_are_the_earlier_meetings_sharing_a_topic() -> None:
    series = _series(("a",), ("b",), ("a", "c"), ("c",))

    assert series.expected_linked_indices(1) == frozenset()
    assert series.expected_linked_indices(2) == {0}
    assert series.expected_linked_indices(3) == {2}


def test_line_topics_follow_the_blocks() -> None:
    meeting = _series(("a", "b")).meetings[0]

    assert meeting.lines[0] == "시작할게요."
    assert meeting.line_topics == [None, "a", "a", "b", "b"]
    assert meeting.topic_set == {"a", "b"}


def test_a_series_meeting_tagged_with_an_undeclared_topic_is_refused() -> None:
    raw = {
        "id": "s",
        "topics": {"a": ["에이"]},
        "meetings": [{"days_ago": 1, "blocks": [{"topic": "z", "lines": ["x"]}]}],
    }

    with pytest.raises(ValueError, match="undeclared"):
        topic_dataset._parse_series(raw)


def test_series_meetings_out_of_order_are_refused() -> None:
    block = {"topic": "a", "lines": ["x"]}
    raw = {
        "id": "s",
        "topics": {"a": ["에이"]},
        "meetings": [{"days_ago": 1, "blocks": [block]}, {"days_ago": 5, "blocks": [block]}],
    }

    with pytest.raises(ValueError, match="oldest first"):
        topic_dataset._parse_series(raw)


def test_topic_heldout_v4_is_a_series_set_with_the_counts_its_about_states() -> None:
    sets = topic_dataset.load_cases("topic_linking_heldout_v4.json")

    assert all(isinstance(s, EvalSeries) for s in sets)
    series = [s for s in sets if isinstance(s, EvalSeries)]
    scored = [(s, i) for s in series for i in range(1, len(s.meetings))]
    assert len(series) == 6
    assert len(scored) == 36
    assert sum(i for _, i in scored) == 126
    assert sum(len(s.expected_linked_indices(i)) for s, i in scored) == 44
    assert sum(not s.expected_linked_indices(i) for s, i in scored) == 12


def test_the_earlier_topic_sets_still_load_as_cases() -> None:
    for name in ("topic_linking_v2.json", "topic_linking_heldout_v3.json"):
        assert all(isinstance(c, EvalCase) for c in topic_dataset.load_cases(name))


def test_lineage_heldout_v6_carries_context_on_every_meeting() -> None:
    cases = lineage_dataset.load_cases("decision_lineage_heldout_v6.json")

    assert len(cases) == 60
    assert sum(c.category.startswith("terse_") for c in cases) == 22
    meetings = [m for c in cases for m in (*c.past_meetings, c.current_meeting)]
    assert all(m.context for m in meetings)


def test_the_earlier_lineage_sets_load_with_no_context() -> None:
    cases = lineage_dataset.load_cases("decision_lineage_heldout_v5.json")

    assert all(not m.context for c in cases for m in (*c.past_meetings, c.current_meeting))


def test_series_categories() -> None:
    assert topic_runner._series_category(frozenset(), 3) == "no_link"
    assert topic_runner._series_category(frozenset({0, 1, 2}), 3) == "all_history"
    assert topic_runner._series_category(frozenset({1}), 3) == "some_history"


def test_line_index_inverts_the_utterance_id() -> None:
    assert topic_runner._line_index(topic_runner._utterance_id("mtg_ab_12", 7)) == 7


def test_a_link_is_on_topic_when_its_segment_is_about_a_topic_the_linked_meeting_had() -> None:
    series = _series(("a",), ("b",), ("a", "b"))
    segments = (
        SegmentOutcome(label="에이 일정", topic="a", names_topic=True),
        SegmentOutcome(label="다음 회의", topic=None, names_topic=False),
    )

    on = topic_runner._link_outcome("에이 일정", segments, series, linked_index=0)
    off = topic_runner._link_outcome("에이 일정", segments, series, linked_index=1)
    closing = topic_runner._link_outcome("다음 회의", segments, series, linked_index=1)

    assert (on.on_topic, on.from_untagged) == (True, False)
    assert (off.on_topic, off.from_untagged) == (False, False)
    assert (closing.on_topic, closing.from_untagged) == (False, True)


def test_the_development_series_loads() -> None:
    series = topic_dataset.load_cases("topic_linking_series_dev_v1.json")
    assert len(series) == 3
    assert all(isinstance(s, EvalSeries) and len(s.meetings) == 6 for s in series)
