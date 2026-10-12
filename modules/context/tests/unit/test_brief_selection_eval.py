"""The brief-selection eval's scoring: what counts as right, and the sweep's rule."""

from __future__ import annotations

import pytest

from autune_context import briefs
from autune_context.eval.brief_selection.dataset import load_cases
from autune_context.eval.brief_selection.runner import (
    MISSED,
    RIGHT,
    UNRELATED_RECAPPED,
    WRONG_MEETING,
    WRONG_SERIES_MEMBER,
    CaseResult,
    Scored,
)


def _result(
    *,
    expected: str | None,
    related: set[str],
    chosen: str | None,
    reason: str | None,
    scored: tuple[Scored, ...] = (),
) -> CaseResult:
    return CaseResult(
        case_id="c",
        category="topic_retitled",
        past_ids=("m0", "m1", "m2"),
        expected=expected,
        related=frozenset(related),
        newest_first=("m2", "m1", "m0"),
        chosen=chosen,
        reason=reason,
        scored=scored,
    )


@pytest.mark.parametrize(
    ("expected", "related", "chosen", "reason", "outcome"),
    [
        ("m1", {"m0", "m1"}, "m1", briefs.TOPIC, RIGHT),
        ("m1", {"m0", "m1"}, "m1", briefs.SERIES, RIGHT),
        ("m1", {"m0", "m1"}, "m0", briefs.TOPIC, WRONG_SERIES_MEMBER),
        ("m1", {"m0", "m1"}, "m2", briefs.TOPIC, WRONG_MEETING),
        # Latest happens to be the expected meeting, but the brief then posts
        # no content: a related meeting was still missed.
        ("m2", {"m2"}, "m2", briefs.LATEST, MISSED),
        (None, set(), "m2", briefs.LATEST, RIGHT),
        (None, set(), "m0", briefs.TOPIC, UNRELATED_RECAPPED),
    ],
)
def test_outcome(
    expected: str | None, related: set[str], chosen: str, reason: str, outcome: str
) -> None:
    result = _result(expected=expected, related=related, chosen=chosen, reason=reason)

    assert result.outcome == outcome
    assert result.correct is (outcome == RIGHT)
    assert result.lenient is (outcome in (RIGHT, WRONG_SERIES_MEMBER))


def test_choice_at_follows_the_topic_step_rule() -> None:
    result = _result(
        expected="m1",
        related={"m0", "m1"},
        chosen="m1",
        reason=briefs.TOPIC,
        scored=(Scored("m0", 0.70, 0.40), Scored("m1", 0.60, 0.80)),
    )

    # Either signal makes a candidate confident; the most recent of them picks.
    assert result.choice_at(0.65, 0.90) == ("m0", briefs.TOPIC)
    assert result.choice_at(0.65, 0.75) == ("m1", briefs.TOPIC)
    assert result.choice_at(0.95, 0.95) == ("m2", briefs.LATEST)


def test_choice_at_leaves_a_series_answer_alone() -> None:
    result = _result(expected="m1", related={"m1"}, chosen="m1", reason=briefs.SERIES)

    assert result.choice_at(0.99, 0.99) == ("m1", briefs.SERIES)


def test_the_set_loads_and_is_labeled_consistently() -> None:
    cases = load_cases()

    assert len({case.id for case in cases}) == len(cases)
    for case in cases:
        assert all(0 <= i < len(case.past_meetings) for i in case.related), case.id
        # A series case's answer is the most recent meeting of its exact title.
        if case.category == "series_exact" and case.expected is not None:
            same = [
                (m.days_ago, i)
                for i, m in enumerate(case.past_meetings)
                if m.title == case.upcoming.title
            ]
            assert min(same)[1] == case.expected, case.id
