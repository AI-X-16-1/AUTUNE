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

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from autune_context import service
from autune_context.eval.decision_lineage.dataset import EvalCase, load_cases
from autune_context.models import CtxDecisionVersion
from autune_contracts import ExtractionResult
from autune_contracts.extraction import Decision
from autune_core import Meeting, Team, session_scope

# No PRD row names this KPI (see package docstring); tracked ad hoc against
# the same six-week bar topic-linking accuracy uses.
_TARGET_ACCURACY = 0.75


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    expected_change_type: str
    actual_change_type: str
    threaded_correctly: bool
    """Landed on the expected past meeting's thread, or correctly opened a new
    one when no past meeting was expected to match."""

    @property
    def correct(self) -> bool:
        return self.threaded_correctly and self.actual_change_type == self.expected_change_type


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
            threaded_correctly = actual_previous_version_id is None
        else:
            threaded_correctly = actual_thread_id == past_thread_ids[case.expected_thread_source]

        return CaseResult(
            case_id=case.id,
            expected_change_type=case.expected_change_type,
            actual_change_type=actual_change_type,
            threaded_correctly=threaded_correctly,
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


def run_all(cases: list[EvalCase] | None = None) -> list[CaseResult]:
    return [run_case(case) for case in (cases if cases is not None else load_cases())]


def report(results: list[CaseResult]) -> str:
    lines = [
        f"[{'OK' if r.correct else 'FAIL'}] {r.case_id}: "
        f"change_type expected={r.expected_change_type!r} actual={r.actual_change_type!r}, "
        f"threaded_correctly={r.threaded_correctly}"
        for r in results
    ]
    correct = sum(r.correct for r in results)
    total = len(results)
    accuracy = correct / total if total else 0.0
    verdict = "PASS" if accuracy >= _TARGET_ACCURACY else "BELOW TARGET"
    lines.append("")
    lines.append(
        f"decision lineage accuracy: {correct}/{total} = {accuracy:.2f} "
        f"(target {_TARGET_ACCURACY}) -- {verdict}"
    )
    return "\n".join(lines)
