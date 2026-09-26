"""The parts of the harness's runner that do not need a database.

The runner itself seeds Postgres and runs the pipeline; what is testable without
either is the guard that decides whether its own reading of the rows can be
trusted. That guard defends this harness's headline claim, so it is worth a test
that does not require the stack to be up.
"""

from __future__ import annotations

import pytest

from autune_gap import detect
from autune_gap.config import GapSettings
from autune_gap.eval.runner import HarnessInconsistencyError, _check_coverage_agrees
from autune_gap.models import GapGap
from autune_gap.template import get_template

TEMPLATE = get_template("general")
ITEM = next(item for item in TEMPLATE.items if item.key == "risk")

MISSING_TITLE = detect.MISSING_TITLE.format(item=ITEM.item)
PARTIAL_TITLE = detect.PARTIAL_TITLE.format(item=ITEM.item)


@pytest.fixture
def thresholds() -> detect.Thresholds:
    settings = GapSettings()
    return detect.Thresholds(
        high=settings.risk_threshold,
        medium=settings.medium_threshold,
        partial_centrality=settings.partial_centrality,
        partial_damping=settings.partial_damping,
        weight_template=settings.weight_template,
        weight_coverage=settings.weight_coverage,
        weight_participation=settings.weight_participation,
    )


def _row_from(finding: detect.Finding) -> GapGap:
    """The `gap_gaps` row `service._store_gaps` writes for one finding, in the
    fields this guard reads."""
    return GapGap(
        id="gap_1",
        meeting_id="mtg_1",
        template_item_key=finding.item_key,
        title=finding.title,
        coverage=finding.coverage.value,
        category=finding.category,
        severity=finding.severity,
        risk_score=finding.risk_score,
    )


def gap(title: str, coverage: str | None, gap_id: str = "gap_1") -> GapGap:
    """A `gap_gaps` row, unattached to any session — only the fields the guard
    reads."""
    return GapGap(
        id=gap_id,
        meeting_id="mtg_1",
        template_item_key=ITEM.key,
        title=title,
        coverage=coverage,
        category=ITEM.category,
        severity="high",
        risk_score=0.8,
    )


def test_a_missing_title_stored_missing_agrees() -> None:
    """The ordinary case: nothing matched the item and both readings say so."""
    _check_coverage_agrees([gap(MISSING_TITLE, "missing")], TEMPLATE)


def test_a_partial_title_stored_partial_agrees() -> None:
    _check_coverage_agrees([gap(PARTIAL_TITLE, "partial")], TEMPLATE)


def test_a_partial_gap_pointing_at_no_topic_does_not_stop_the_run(
    thresholds: detect.Thresholds,
) -> None:
    """The regression this file exists for after #303.

    The guard used to read the partial/missing split off `gap_related_topics`,
    where a gap linking to no topic *was* a missing one. #303 made that untrue:
    an item the meeting only said out loud, with no topic behind it, is partial
    and links to nothing. Every such gap read as missing, disagreed with its own
    title, and stopped the whole run — on exactly the case the change was for.

    The row is built from a finding `detect.compare` actually produced, the way
    `service._store_gaps` builds it, so the two halves are checked against each
    other rather than against a hand-written row.
    """
    findings = detect.compare(
        TEMPLATE,
        # The graph is not empty -- an empty one raises nothing at all -- but
        # nothing in it is the risk item.
        [detect.TopicView(id="topic_1", label="검색 개인화", centrality=1.0, silent_share=None)],
        ["롤백 플랜은 준비해두겠습니다"],
        thresholds,
    )

    raised = next(finding for finding in findings if finding.item_key == ITEM.key)
    assert raised.coverage is detect.Coverage.PARTIAL
    assert raised.topic_ids == (), "said, not extracted -- there is no topic to point at"

    _check_coverage_agrees([_row_from(raised)], TEMPLATE)


def test_a_partial_title_stored_missing_stops_the_run() -> None:
    """The two readings disagree, and a wrong cause split looks exactly like a
    real result, so the run stops instead of printing one."""
    with pytest.raises(HarnessInconsistencyError, match="would be wrong"):
        _check_coverage_agrees([gap(PARTIAL_TITLE, "missing")], TEMPLATE)


def test_a_missing_title_stored_partial_stops_the_run() -> None:
    """The other direction. Less likely and no less wrong."""
    with pytest.raises(HarnessInconsistencyError):
        _check_coverage_agrees([gap(MISSING_TITLE, "partial")], TEMPLATE)


def test_a_row_with_no_coverage_stops_the_run() -> None:
    """The column is nullable — rows written before its migration have nothing
    to put there — and `None` read as "not partial" is a wrong number rather
    than a refusal."""
    with pytest.raises(HarnessInconsistencyError, match="would be guessed"):
        _check_coverage_agrees([gap(PARTIAL_TITLE, None)], TEMPLATE)


def test_the_error_names_the_item_and_the_meeting() -> None:
    """Whoever reads the failure has to know which gap to go and look at. Item
    keys and ids only — a gap title carries a template item's wording, and the
    guard does not print the row."""
    with pytest.raises(HarnessInconsistencyError) as raised:
        _check_coverage_agrees([gap(PARTIAL_TITLE, "missing")], TEMPLATE)

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
        coverage=None,
        category="risk",
        severity="high",
        risk_score=0.8,
    )

    _check_coverage_agrees([stray], TEMPLATE)


def test_a_title_neither_wording_produced_is_left_alone() -> None:
    """#35 rewrites the question and may reword the title. A title the guard
    cannot attribute is skipped rather than treated as a disagreement, and that
    holds whether or not the row stores a coverage."""
    _check_coverage_agrees([gap("리스크·예외 처리 — 누군가 다시 적은 제목", None)], TEMPLATE)
