"""Runs the hand-labeled decision-lineage set against the real pipeline.

Exercises ``service.build_decision_lineage`` end to end: thread matching (the
embedder configured by ``AUTUNE_CONTEXT_EMBEDDER_IMPL``) and change
classification (the NLI model configured by ``AUTUNE_CONTEXT_NLI_IMPL``)
together, not either one in isolation. See ``eval.topic_linking.runner``'s
docstring for what a real (non-``fake``) run needs — the same Postgres and
model-endpoint prerequisites apply here.

Each case's ``past_meetings`` are processed first, oldest or not — thread
matching goes by each meeting's real ``started_at``, not insertion order —
then the current meeting, mirroring ``build_decision_lineage``'s own
production call pattern (one meeting's decisions at a time, as B reports
them). Each case gets its own team, deleted (cascading through every ``ctx_*``
row) once scored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from autune_context import service
from autune_context.config import get_settings
from autune_context.eval.decision_lineage.dataset import EvalCase, load_cases
from autune_context.eval.metrics import accuracy_line, by_category, ratio, sweep_points
from autune_context.models import CtxDecisionVersion
from autune_context.pipeline import get_embedder
from autune_contracts import ExtractionResult
from autune_contracts.extraction import Decision
from autune_core import Meeting, Team, session_scope

# No PRD row names this KPI (see package docstring); tracked ad hoc against
# the same six-week bar topic-linking accuracy uses.
_TARGET_ACCURACY = 0.75

_CHANGE_TYPES = ("unchanged", "modified", "reversed", "new")


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    category: str
    expected_change_type: str
    actual_change_type: str
    threaded_correctly: bool
    """Landed on the expected past meeting's thread, or correctly opened a new
    one when no past meeting was expected to match."""
    head_similarity: dict[str, float]
    """Thread id -> cosine similarity between the current statement and that
    thread's head (its most recent past statement) -- exactly what
    ``lineage_match_threshold`` is compared against."""
    expected_thread_id: str | None

    @property
    def correct(self) -> bool:
        return self.threaded_correctly and self.actual_change_type == self.expected_change_type

    def threaded_correctly_at(self, threshold: float) -> bool:
        """Whether the current decision would have threaded correctly with
        ``lineage_match_threshold`` at ``threshold``, holding the past
        meetings' own threading fixed as this run produced it."""
        above = {t: sim for t, sim in self.head_similarity.items() if sim >= threshold}
        picked = max(above, key=lambda t: above[t]) if above else None
        return picked == self.expected_thread_id


def run_case(case: EvalCase) -> CaseResult:
    with session_scope() as s:
        team = Team(name=f"eval-{case.id}")
        s.add(team)
        s.flush()
        team_id = team.id

    try:
        past_thread_ids = [
            _process_meeting(team_id, meeting.days_ago, meeting.decision)[1]
            for meeting in case.past_meetings
        ]

        current_id, _ = _process_meeting(
            team_id, case.current_meeting.days_ago, case.current_meeting.decision
        )
        with session_scope() as s:
            actual_thread_id, actual_previous_version_id, actual_change_type = s.execute(
                select(
                    CtxDecisionVersion.thread_id,
                    CtxDecisionVersion.previous_version_id,
                    CtxDecisionVersion.change_type,
                ).where(CtxDecisionVersion.meeting_id == current_id)
            ).one()

        if case.expected_thread_source is None:
            expected_thread_id = None
            threaded_correctly = actual_previous_version_id is None
        else:
            expected_thread_id = past_thread_ids[case.expected_thread_source]
            threaded_correctly = actual_thread_id == expected_thread_id

        return CaseResult(
            case_id=case.id,
            category=case.category,
            expected_change_type=case.expected_change_type,
            actual_change_type=actual_change_type,
            threaded_correctly=threaded_correctly,
            head_similarity=_head_similarity(case, past_thread_ids),
            expected_thread_id=expected_thread_id,
        )
    finally:
        with session_scope() as s:
            s.execute(delete(Team).where(Team.id == team_id))


def _process_meeting(team_id: str, days_ago: int, statement: str) -> tuple[str, str]:
    """Seeds one meeting with one decision, runs lineage, returns
    ``(meeting_id, thread_id)``."""
    with session_scope() as s:
        row = Meeting(
            team_id=team_id,
            title=f"eval meeting (days_ago={days_ago})",
            status="analyzing",
            started_at=datetime.now(tz=UTC) - timedelta(days=days_ago),
        )
        s.add(row)
        s.flush()
        meeting_id = row.id

    service.build_decision_lineage(
        ExtractionResult(
            meeting_id=meeting_id,
            decisions=[Decision(id=f"dec_eval_{meeting_id}", statement=statement, confidence=0.9)],
        )
    )
    with session_scope() as s:
        thread_id = s.scalars(
            select(CtxDecisionVersion.thread_id).where(CtxDecisionVersion.meeting_id == meeting_id)
        ).one()
    return meeting_id, thread_id


def _head_similarity(case: EvalCase, past_thread_ids: list[str]) -> dict[str, float]:
    """Cosine similarity of the current statement to each past thread's head.

    A thread's head is its most recent past meeting -- the one
    ``service._thread_heads`` would offer -- so the current statement is
    scored against that meeting's wording, not the thread's first.
    """
    heads: dict[str, int] = {}
    for index, thread_id in enumerate(past_thread_ids):
        latest = heads.get(thread_id)
        if latest is None or (
            case.past_meetings[index].days_ago < case.past_meetings[latest].days_ago
        ):
            heads[thread_id] = index
    current, *head_vectors = get_embedder().embed(
        [case.current_meeting.decision, *(case.past_meetings[i].decision for i in heads.values())]
    )
    return {
        thread_id: _cosine(current, vector)
        for thread_id, vector in zip(heads, head_vectors, strict=True)
    }


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def run_all(cases: list[EvalCase] | None = None) -> list[CaseResult]:
    return [run_case(case) for case in (cases if cases is not None else load_cases())]


def _classified(results: list[CaseResult]) -> tuple[int, int]:
    """(right, total) change types among correctly threaded non-new cases."""
    classifiable = [r for r in results if r.threaded_correctly and r.expected_change_type != "new"]
    return sum(r.actual_change_type == r.expected_change_type for r in classifiable), len(
        classifiable
    )


def headline(results: list[CaseResult]) -> dict[str, float]:
    """The numbers ``--mode both`` puts side by side."""
    right, of = _classified(results)
    return {
        "accuracy": sum(r.correct for r in results) / len(results) if results else 0.0,
        "threading": sum(r.threaded_correctly for r in results) / len(results) if results else 0.0,
        "change type (threaded, non-new)": right / of if of else 0.0,
    }


def case_outcomes(results: list[CaseResult]) -> dict[str, bool]:
    return {f"{r.category}/{r.case_id}": r.correct for r in results}


def report(results: list[CaseResult]) -> str:
    """Per-case lines, the headline number, then threading and change-type
    classification scored separately -- a case fails on either, and the two
    are tuned by different knobs (``lineage_match_threshold`` vs the NLI
    model). Under ``engine_mode="llm"`` one model makes both calls and the
    cosine threshold decides nothing, so the sweep over it is left out."""
    settings = get_settings()
    threshold = settings.lineage_match_threshold
    llm_mode = settings.engine_mode == "llm"
    lines = [_case_line(r) for r in results]
    correct = sum(r.correct for r in results)
    total = len(results)
    accuracy = correct / total if total else 0.0
    verdict = "PASS" if accuracy >= _TARGET_ACCURACY else "BELOW TARGET"

    threaded = sum(r.threaded_correctly for r in results)
    classified, classifiable = _classified(results)

    lines += [
        "",
        accuracy_line("decision lineage accuracy", correct, total)
        + f" (target {_TARGET_ACCURACY}) -- {verdict}",
        (
            f"engine_mode = llm ({settings.llm_impl}:{settings.llm_model_name}): "
            f"llm_match_threshold = "
            f"{settings.llm_match_threshold}, asked about the top "
            f"{settings.llm_thread_candidates} threads per decision"
            if llm_mode
            else f"lineage_match_threshold = {threshold}"
            + (
                " (engine_mode = hybrid: decision lineage runs classic)"
                if settings.engine_mode == "hybrid"
                else ""
            )
        ),
        "",
        "by category:",
        *by_category(results),
        "",
        f"threading (right thread, or a new one when expected): {ratio(threaded, total)}",
        "change type among correctly threaded non-new cases "
        + ("(the LLM's verdict): " if llm_mode else "(the NLI step alone): ")
        + ratio(classified, classifiable),
        "",
        "change type confusion (rows expected, columns actual):",
        *_confusion(results),
    ]
    if not llm_mode:
        lines += [
            "",
            "threshold sweep (threading accuracy if lineage_match_threshold were t,",
            "holding the past meetings' own threading as this run produced it):",
            *_sweep(results, threshold),
        ]
    return "\n".join(lines)


def _case_line(r: CaseResult) -> str:
    expected_sim = (
        f"{r.head_similarity[r.expected_thread_id]:.3f}"
        if r.expected_thread_id is not None
        else "-"
    )
    best_sim = f"{max(r.head_similarity.values()):.3f}" if r.head_similarity else "-"
    return (
        f"[{'OK' if r.correct else 'FAIL'}] {r.category}/{r.case_id}: "
        f"change_type expected={r.expected_change_type!r} actual={r.actual_change_type!r}, "
        f"threaded_correctly={r.threaded_correctly}, "
        f"sim expected_head={expected_sim} best_head={best_sim}"
    )


def _confusion(results: list[CaseResult]) -> list[str]:
    counts = dict.fromkeys(((e, a) for e in _CHANGE_TYPES for a in _CHANGE_TYPES), 0)
    for r in results:
        counts[(r.expected_change_type, r.actual_change_type)] += 1
    width = max(len(c) for c in _CHANGE_TYPES) + 2
    header = " " * (width + 2) + "".join(f"{a:>{width}}" for a in _CHANGE_TYPES)
    rows = [
        f"  {e:<{width}}" + "".join(f"{counts[(e, a)]:>{width}}" for a in _CHANGE_TYPES)
        for e in _CHANGE_TYPES
    ]
    return [header, *rows]


def _sweep(results: list[CaseResult], current: float) -> list[str]:
    if not results:
        return []
    points = sweep_points(0.30, 0.90)
    best = max(points, key=lambda t: sum(r.threaded_correctly_at(t) for r in results))
    rows = []
    for t in points:
        ok = sum(r.threaded_correctly_at(t) for r in results)
        marks = ("  <- current" if abs(t - current) < 1e-9 else "") + (
            "  <- best" if t == best else ""
        )
        rows.append(f"  t={t:.2f}  {ok:>2}/{len(results)} = {ok / len(results):.2f}{marks}")
    return rows
