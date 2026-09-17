"""Runs the hand-labeled evaluation set against the real pipeline.

Unlike the integration tests (``tests/integration/test_topic_linking.py``),
which pin every model to ``fake`` for determinism, this calls
``service.run_topic_linking`` with whatever ``AUTUNE_CONTEXT_*_IMPL`` the
environment already has configured — the point of this harness is to measure
the actual KURE + BM25 + reranker stack, not the plumbing around it.

Needs:
- A Postgres reachable via ``ContextSettings``, migrated to ``heads``
  (``uv run alembic -c infra/alembic.ini upgrade heads``). See
  docs/engineering/environments.md for the docker-compose database.
- Whichever embedder/reranker endpoints or local weights the configured
  ``_impl`` values require. ``klue_kornli_http`` and friends are unreachable
  by default outside the team's infra; switch to the ``*_local`` or ``fake``
  impls (docs/modules/context.md, "Model abstraction layer") to run this
  without them, though ``fake`` defeats the purpose of an accuracy number.

Each case gets its own team, deleted (cascading through every ``ctx_*`` row)
once scored, so a run leaves no synthetic data behind in the target database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from autune_context import service
from autune_context.eval.topic_linking.dataset import EvalCase, EvalMeeting, load_cases
from autune_context.models import CtxTopicLink
from autune_contracts import (
    PrivacyFlags,
    TranscriptMetadata,
    TranscriptReady,
    TranscriptSource,
    Utterance,
)
from autune_core import Meeting, Team, session_scope

# The six-week target from docs/modules/context.md, "Metric" / "Phased
# delivery". The three-month target (0.85+) is out of the six-week scope
# (docs/modules/context.md, "Out of the six-week scope") and not checked here.
_TARGET_ACCURACY = 0.75


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    expected: frozenset[str]
    """Past-meeting ids the current meeting should have asserted a link to."""
    asserted: frozenset[str]
    pending: frozenset[str]

    @property
    def correct(self) -> bool:
        """An asserted link is a decision the system made on its own; a
        ``pending`` one asked the user instead. Only ``asserted`` counts as
        "linked" here, matching ``link_confidence_threshold``'s own meaning."""
        return self.asserted == self.expected


def run_case(case: EvalCase) -> CaseResult:
    with session_scope() as s:
        team = Team(name=f"eval-{case.id}")
        s.add(team)
        s.flush()
        team_id = team.id

    try:
        past_ids = [_seed_meeting(team_id, meeting) for meeting in case.past_meetings]
        for meeting_id, meeting in zip(past_ids, case.past_meetings, strict=True):
            service.run_topic_linking(_transcript(meeting_id, meeting.lines))

        current_id = _seed_meeting(team_id, case.current_meeting)
        service.run_topic_linking(_transcript(current_id, case.current_meeting.lines))

        with session_scope() as s:
            links = s.scalars(
                select(CtxTopicLink).where(CtxTopicLink.meeting_id == current_id)
            ).all()
            asserted = frozenset(
                link.linked_meeting_id
                for link in links
                if link.status == "asserted" and link.linked_meeting_id is not None
            )
            pending = frozenset(
                link.linked_meeting_id
                for link in links
                if link.status == "pending" and link.linked_meeting_id is not None
            )

        expected = frozenset(past_ids[i] for i in case.expected_linked_indices)
        return CaseResult(case.id, expected, asserted, pending)
    finally:
        with session_scope() as s:
            s.execute(delete(Team).where(Team.id == team_id))


def _seed_meeting(team_id: str, meeting: EvalMeeting) -> str:
    with session_scope() as s:
        row = Meeting(
            team_id=team_id,
            title=f"eval meeting (days_ago={meeting.days_ago})",
            status="analyzing",
            started_at=datetime.now(tz=UTC) - timedelta(days=meeting.days_ago),
        )
        s.add(row)
        s.flush()
        return row.id


def _transcript(meeting_id: str, lines: list[str]) -> TranscriptReady:
    return TranscriptReady(
        meeting_id=meeting_id,
        utterances=[
            Utterance(
                id=f"utt_{meeting_id}_{i}",
                speaker="화자",
                start=float(i),
                end=float(i) + 1,
                text=line,
                confidence=0.9,
            )
            for i, line in enumerate(lines)
        ],
        metadata=TranscriptMetadata(
            duration=float(len(lines)),
            participants=["화자"],
            source=TranscriptSource.FILE_UPLOAD,
            language="ko",
            privacy=PrivacyFlags(original_audio_deleted=True, pii_masked=True),
        ),
    )


def run_all(cases: list[EvalCase] | None = None) -> list[CaseResult]:
    return [run_case(case) for case in (cases if cases is not None else load_cases())]


def report(results: list[CaseResult]) -> str:
    lines = [
        f"[{'OK' if r.correct else 'FAIL'}] {r.case_id}: "
        f"expected={sorted(r.expected)} asserted={sorted(r.asserted)} pending={sorted(r.pending)}"
        for r in results
    ]
    correct = sum(r.correct for r in results)
    total = len(results)
    accuracy = correct / total if total else 0.0
    verdict = "PASS" if accuracy >= _TARGET_ACCURACY else "BELOW TARGET"
    lines.append("")
    lines.append(
        f"topic linking accuracy: {correct}/{total} = {accuracy:.2f} "
        f"(target {_TARGET_ACCURACY}) -- {verdict}"
    )
    return "\n".join(lines)
