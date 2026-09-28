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
from autune_context.config import get_settings
from autune_context.eval.metrics import accuracy_line, by_category, ratio, sweep_points
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
from autune_core import Utterance as UtteranceRow

# The six-week target from docs/modules/context.md, "Metric" / "Phased
# delivery". The three-month target (0.85+) is out of the six-week scope
# (docs/modules/context.md, "Out of the six-week scope") and not checked here.
_TARGET_ACCURACY = 0.75


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    category: str
    expected: frozenset[str]
    """Past-meeting ids the current meeting should have asserted a link to."""
    asserted: frozenset[str]
    pending: frozenset[str]
    best_similarity: dict[str, float]
    """Past-meeting id -> the highest dense ``similarity`` any of the current
    meeting's topics had to it. A past meeting missing here was never a
    candidate (not retrieved, or cut by ``rerank_top_k``), so it could not be
    asserted at any threshold."""
    best_rerank: dict[str, float]
    """Same, for ``rerank_score``."""
    past_ids: tuple[str, ...]
    """The case's past-meeting ids in dataset order, to print as indices."""

    @property
    def correct(self) -> bool:
        """An asserted link is a decision the system made on its own; a
        ``pending`` one asked the user instead. Only ``asserted`` counts as
        "linked" here, matching ``link_confidence_threshold``'s own meaning."""
        return self.asserted == self.expected

    def asserted_at(self, threshold: float) -> frozenset[str]:
        """What ``asserted`` would have been with ``link_confidence_threshold``
        at ``threshold`` — every candidate row is already written, pending or
        not, so this needs no re-run."""
        return frozenset(m for m, score in self.best_rerank.items() if score >= threshold)

    def index(self, meeting_id: str) -> int:
        return self.past_ids.index(meeting_id)


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
            best_similarity: dict[str, float] = {}
            best_rerank: dict[str, float] = {}
            for link in links:
                if link.linked_meeting_id is not None:
                    m = link.linked_meeting_id
                    best_similarity[m] = max(link.similarity, best_similarity.get(m, 0.0))
                    best_rerank[m] = max(link.rerank_score, best_rerank.get(m, 0.0))

        expected = frozenset(past_ids[i] for i in case.expected_linked_indices)
        return CaseResult(
            case_id=case.id,
            category=case.category,
            expected=expected,
            asserted=asserted,
            pending=pending,
            best_similarity=best_similarity,
            best_rerank=best_rerank,
            past_ids=tuple(past_ids),
        )
    finally:
        with session_scope() as s:
            s.execute(delete(Team).where(Team.id == team_id))


def _seed_meeting(team_id: str, meeting: EvalMeeting) -> str:
    """The meeting row plus its ``utterances``, as module A leaves them before
    publishing ``TranscriptReady`` -- the re-ranker reads a past topic's text
    back from ``utterances``, so a meeting seeded without them would be scored
    against its label alone. Deleted with the team, by cascade."""
    with session_scope() as s:
        row = Meeting(
            team_id=team_id,
            title=f"eval meeting (days_ago={meeting.days_ago})",
            status="analyzing",
            started_at=datetime.now(tz=UTC) - timedelta(days=meeting.days_ago),
        )
        s.add(row)
        s.flush()
        s.add_all(
            UtteranceRow(
                id=_utterance_id(row.id, i),
                meeting_id=row.id,
                speaker_label="화자",
                start_sec=float(i),
                end_sec=float(i) + 1,
                text=line,
                confidence=0.9,
            )
            for i, line in enumerate(meeting.lines)
        )
        return row.id


def _utterance_id(meeting_id: str, index: int) -> str:
    return f"utt_{meeting_id}_{index}"


def _transcript(meeting_id: str, lines: list[str]) -> TranscriptReady:
    return TranscriptReady(
        meeting_id=meeting_id,
        utterances=[
            Utterance(
                id=_utterance_id(meeting_id, i),
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
    """Per-case lines, the KPI, then the numbers that explain it.

    Past meetings print as their index in the case (``#0``, ``#1``), with the
    best dense similarity (``s``) and rerank score (``r``) each got, so a FAIL
    line reads against the fixture directly instead of against throwaway
    ``mtg_`` ids.
    """
    settings = get_settings()
    lines = [_case_line(r) for r in results]
    correct = sum(r.correct for r in results)
    total = len(results)
    accuracy = correct / total if total else 0.0
    verdict = "PASS" if accuracy >= _TARGET_ACCURACY else "BELOW TARGET"

    tp = sum(len(r.asserted & r.expected) for r in results)
    fp = sum(len(r.asserted - r.expected) for r in results)
    fn = sum(len(r.expected - r.asserted) for r in results)
    surfaced = sum(len((r.asserted | r.pending) & r.expected) for r in results)
    negatives = [r for r in results if not r.expected]
    false_linked = sum(bool(r.asserted) for r in negatives)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    lines += [
        "",
        accuracy_line("topic linking accuracy", correct, total)
        + f" (target {_TARGET_ACCURACY}) -- {verdict}",
        f"link_confidence_threshold = {settings.link_confidence_threshold}",
        "",
        "by category:",
        *by_category(results),
        "",
        "asserted links, counted per (current meeting, past meeting) pair:",
        f"  precision {ratio(tp, tp + fp)}",
        f"  recall    {ratio(tp, tp + fn)}",
        f"  f1        {f1:.2f}",
        f"  surfaced  {ratio(surfaced, tp + fn)}  (asserted or pending: reached the user)",
        f"  no-link cases with any asserted link: {ratio(false_linked, len(negatives))}",
        "",
        "threshold sweep (accuracy if link_confidence_threshold were t):",
        *_sweep(results, settings.link_confidence_threshold),
    ]
    return "\n".join(lines)


def _case_line(r: CaseResult) -> str:
    def fmt(ids: frozenset[str]) -> str:
        return "[" + ",".join(f"#{i}" for i in sorted(r.index(m) for m in ids)) + "]"

    scores = " ".join(
        f"#{r.index(m)}=s{r.best_similarity[m]:.3f}/r{r.best_rerank[m]:.3f}"
        for m in sorted(r.best_similarity, key=r.index)
    )
    return (
        f"[{'OK' if r.correct else 'FAIL'}] {r.category}/{r.case_id}: "
        f"expected={fmt(r.expected)} asserted={fmt(r.asserted)} "
        f"pending={fmt(r.pending)} scores: {scores or '-'}"
    )


def _sweep(results: list[CaseResult], current: float) -> list[str]:
    if not results:
        return []
    points = sweep_points()

    def correct_at(t: float) -> int:
        return sum(r.asserted_at(t) == r.expected for r in results)

    rows = []
    best = max(points, key=correct_at)
    for t in points:
        ok = correct_at(t)
        marks = ("  <- current" if abs(t - current) < 1e-9 else "") + (
            "  <- best" if t == best else ""
        )
        rows.append(f"  t={t:.3f}  {ok:>2}/{len(results)} = {ok / len(results):.2f}{marks}")
    return rows
