"""The parts of the harness's runner that do not need a database.

The runner itself seeds Postgres and runs the pipeline; what is testable without
either is the guard that decides whether its own reading of the rows can be
trusted. That guard defends this harness's headline claim, so it is worth a test
that does not require the stack to be up.
"""

from __future__ import annotations

import pytest

from autune_gap.eval.runner import HarnessInconsistencyError, _check_coverage_agrees
from autune_gap.models import GapGap
from autune_gap.template import get_template

TEMPLATE = get_template("general")
ITEM = next(item for item in TEMPLATE.items if item.key == "risk")


def gap(title: str, gap_id: str = "gap_1") -> GapGap:
    """A `gap_gaps` row, unattached to any session — only the four fields the
    guard reads."""
    return GapGap(
        id=gap_id,
        meeting_id="mtg_1",
        template_item_key=ITEM.key,
        title=title,
        category=ITEM.category,
        severity="high",
        risk_score=0.8,
    )


def test_a_missing_title_with_no_linked_topic_agrees() -> None:
    """The ordinary case: nothing matched the item, so the gap points at no
    topic and its title says so."""
    _check_coverage_agrees([gap("리스크·예외 처리 — 논의되지 않았습니다")], set(), TEMPLATE)


def test_a_partial_title_with_a_linked_topic_agrees() -> None:
    _check_coverage_agrees(
        [gap("리스크·예외 처리 — 충분히 다뤄지지 않았습니다")], {"gap_1"}, TEMPLATE
    )


def test_a_partial_title_with_no_linked_topic_stops_the_run() -> None:
    """The failure this guard exists for.

    `build_topic_graph` deletes the meeting's topics before `detect_gaps` runs
    and the cascade takes the link rows with it, so an empty link table reads as
    "every gap is missing" — which is the shape of this harness's headline
    claim. A wrong cause split would look exactly like a real result, so the run
    stops instead of printing one.
    """
    with pytest.raises(HarnessInconsistencyError, match="would be wrong"):
        _check_coverage_agrees(
            [gap("리스크·예외 처리 — 충분히 다뤄지지 않았습니다")], set(), TEMPLATE
        )


def test_a_missing_title_with_a_linked_topic_stops_the_run() -> None:
    """The other direction. Less likely and no less wrong."""
    with pytest.raises(HarnessInconsistencyError):
        _check_coverage_agrees([gap("리스크·예외 처리 — 논의되지 않았습니다")], {"gap_1"}, TEMPLATE)


def test_the_error_names_the_item_and_the_meeting() -> None:
    """Whoever reads the failure has to know which gap to go and look at. Item
    keys and ids only — a gap title carries a template item's wording, and the
    guard does not print the row."""
    with pytest.raises(HarnessInconsistencyError) as raised:
        _check_coverage_agrees(
            [gap("리스크·예외 처리 — 충분히 다뤄지지 않았습니다")], set(), TEMPLATE
        )

    assert "mtg_1" in str(raised.value)
    assert "risk" in str(raised.value)


def test_a_gap_no_template_item_raised_is_left_alone() -> None:
    """A gap found from the graph alone carries no template item key, and this
    harness has nothing to say about it. Nothing writes one yet; the guard must
    not start failing on the day something does."""
    stray = GapGap(
        id="gap_2",
        meeting_id="mtg_1",
        template_item_key=None,
        title="어딘가에서 온 갭",
        category="risk",
        severity="high",
        risk_score=0.8,
    )

    _check_coverage_agrees([stray], set(), TEMPLATE)


def test_a_title_neither_wording_produced_is_left_alone() -> None:
    """#35 rewrites the question and may reword the title. A title the guard
    cannot attribute is skipped rather than treated as a disagreement."""
    _check_coverage_agrees([gap("리스크·예외 처리 — 누군가 다시 적은 제목")], set(), TEMPLATE)
