"""The evaluation runners' plumbing against a real PostgreSQL, with ``fake`` models.

Synthetic data only: a held-out set must not be run while a change is being
built, and ``fake`` models say nothing about accuracy anyway. What is pinned is
that a series runs one team oldest first and scores every meeting but the
first, and that a lineage case's ``context`` reaches the decision as its source
utterances -- and that neither leaves a row behind.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import select

from autune_context.config import get_settings
from autune_context.eval.decision_lineage import runner as lineage_runner
from autune_context.eval.decision_lineage.dataset import EvalCase as LineageCase
from autune_context.eval.decision_lineage.dataset import EvalMeeting as LineageMeeting
from autune_context.eval.topic_linking import runner as topic_runner
from autune_context.eval.topic_linking.dataset import EvalSeries, SeriesBlock, SeriesMeeting
from autune_context.models import CtxDecisionVersion
from autune_context.pipeline import reset_cache
from autune_context.service import LineageOutcome
from autune_contracts import ExtractionResult
from autune_core import Team, session_scope


@pytest.fixture(autouse=True)
def _fake_models(monkeypatch: pytest.MonkeyPatch, db_engine: object) -> Iterator[None]:
    for knob in ("EMBEDDER", "RERANKER", "NLI", "LLM"):
        monkeypatch.setenv(f"AUTUNE_CONTEXT_{knob}_IMPL", "fake")
    get_settings.cache_clear()
    reset_cache()
    yield
    get_settings.cache_clear()
    reset_cache()


def _eval_teams() -> int:
    with session_scope() as s:
        return len(s.scalars(select(Team.id).where(Team.name.startswith("eval-"))).all())


def _meeting(days_ago: int, *blocks: tuple[str | None, list[str]]) -> SeriesMeeting:
    return SeriesMeeting(
        days_ago=days_ago, blocks=[SeriesBlock(topic=t, lines=lines) for t, lines in blocks]
    )


def test_a_series_scores_every_meeting_after_the_first() -> None:
    budget = ["예산 집행 현황을 봤다.", "예산 잔액이 부족하다.", "예산을 다시 짜기로 했다."]
    hiring = ["채용 공고를 냈다.", "채용 지원자가 많다.", "채용 면접을 잡았다."]
    opening = ["시작하겠습니다.", "다들 오셨죠."]
    series = EvalSeries(
        id="harness-test",
        topics={"budget": ["예산"], "hiring": ["채용"]},
        meetings=[
            _meeting(30, (None, opening), ("budget", budget)),
            _meeting(20, (None, opening), ("hiring", hiring)),
            _meeting(10, (None, opening), ("budget", budget)),
        ],
    )
    before = _eval_teams()

    results = topic_runner.run_series(series)

    assert [r.case_id for r in results] == ["harness-test#1", "harness-test#2"]
    assert [r.category for r in results] == ["no_link", "some_history"]
    assert len(results[1].expected) == 1
    assert results[1].past_ids[0] in results[1].expected
    assert all(r.segments for r in results)
    assert all(seg.topic in {None, "budget", "hiring"} for r in results for seg in r.segments)
    assert "labels naming their topic" in topic_runner.headline(results)
    assert "read against the block tags" in topic_runner.report(results)
    assert _eval_teams() == before


def test_a_lineage_case_with_context_cites_it_as_source_utterances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cited: list[list[str]] = []
    original = lineage_runner.service.build_decision_lineage

    def spy(result: ExtractionResult) -> LineageOutcome:
        cited.append([u for d in result.decisions for u in d.source_utterance_ids])
        outcome = original(result)
        with session_scope() as s:
            stored = s.scalars(
                select(CtxDecisionVersion.source_utterance_ids).where(
                    CtxDecisionVersion.meeting_id == result.meeting_id
                )
            ).one()
        assert stored == cited[-1]
        return outcome

    monkeypatch.setattr(lineage_runner.service, "build_decision_lineage", spy)
    case = LineageCase(
        id="harness-test-context",
        past_meetings=[
            LineageMeeting(
                days_ago=10, decision="배너는 세 개로 한다.", context=["배너 세 개로 하죠."]
            )
        ],
        current_meeting=LineageMeeting(
            days_ago=0, decision="B안으로 확정한다.", context=["B안은 두 개예요.", "B안으로 하죠."]
        ),
        expected_thread_source=0,
        expected_change_type="modified",
    )
    before = _eval_teams()

    lineage_runner.run_case(case)

    assert [len(ids) for ids in cited] == [1, 2]
    assert all(i.startswith("utt_") for ids in cited for i in ids)
    assert _eval_teams() == before
