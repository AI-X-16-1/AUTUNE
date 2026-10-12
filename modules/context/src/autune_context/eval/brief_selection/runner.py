"""Runs the brief-selection set against the real pipeline.

For each case: seed the team's past meetings and analyze them with
``service.run_topic_linking`` (which marks them ``topic_linking_done``, the
mark ``briefs`` looks for), seed the upcoming meeting as ``scheduled`` ten
minutes ahead, then ask ``briefs.choose_previous_meeting`` which one the brief
would recap. The agenda is passed in directly: what is under test is the
choice, not ``store_team_agenda``.

The topic step's retriever and re-ranker are wrapped, not replaced, to record
the score each candidate got -- what the case lines print and the threshold
sweep re-decides on. The sweep re-implements the topic step's rule
(``briefs._topic_match``); the report says so if its answer at the configured
thresholds ever disagrees with the real one.

Needs what ``eval.topic_linking.runner`` needs: a migrated Postgres and the
configured embedder and re-ranker (``*_local`` impls to run without the
team's endpoints). Each case gets its own ``eval-`` team, deleted once scored.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock

from sqlalchemy import delete

from autune_context import briefs, service
from autune_context.config import get_settings
from autune_context.eval.brief_selection.dataset import BriefCase, PastMeeting, load_cases
from autune_context.eval.metrics import accuracy_line, by_category, ratio, sweep_points
from autune_context.eval.topic_linking.runner import _transcript, _utterance_id
from autune_context.notify import AgendaItem
from autune_context.pipeline.retrieval import Candidate, HybridRetriever
from autune_core import Meeting, Participant, Team, session_scope
from autune_core import Utterance as UtteranceRow

_LEAD = timedelta(minutes=10)

RIGHT = "right"
UNRELATED_RECAPPED = "unrelated meeting recapped"
"""Nothing was related, yet the brief asserted a meeting and would post its
topics and decisions -- the error that sends a third party content the
feature had no need to send (privacy.md sections 6-7)."""
WRONG_SERIES_MEMBER = "right subject, not the meeting this one follows"
WRONG_MEETING = "unrelated meeting chosen over the related one"
MISSED = "related meeting missed (fell back to latest)"
_OUTCOMES = (RIGHT, UNRELATED_RECAPPED, WRONG_SERIES_MEMBER, WRONG_MEETING, MISSED)


@dataclass(frozen=True)
class Scored:
    meeting_id: str
    similarity: float
    rerank: float


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    category: str
    past_ids: tuple[str, ...]
    """In dataset order, to print as ``#index``."""
    expected: str | None
    related: frozenset[str]
    latest: str
    chosen: str | None
    reason: str | None
    scored: tuple[Scored, ...] = field(default=())
    """Every candidate the topic step re-ranked, in its order; empty when the
    series step answered first."""

    def outcome_of(self, chosen: str | None, reason: str | None) -> str:
        related_asserted = reason in (briefs.SERIES, briefs.TOPIC)
        if self.expected is None:
            return UNRELATED_RECAPPED if related_asserted else RIGHT
        if not related_asserted:
            return MISSED
        if chosen == self.expected:
            return RIGHT
        return WRONG_SERIES_MEMBER if chosen in self.related else WRONG_MEETING

    @property
    def outcome(self) -> str:
        return self.outcome_of(self.chosen, self.reason)

    @property
    def correct(self) -> bool:
        return self.outcome == RIGHT

    @property
    def lenient(self) -> bool:
        """Right, or another meeting on the same subject."""
        return self.outcome in (RIGHT, WRONG_SERIES_MEMBER)

    def choice_at(self, similarity: float, rerank: float) -> tuple[str | None, str | None]:
        """What the choice would have been at these thresholds -- the topic
        step's rule over the recorded scores. A series answer does not depend
        on them."""
        if self.reason == briefs.SERIES:
            return self.chosen, self.reason
        confident = [
            (max(s.similarity, s.rerank), s.meeting_id)
            for s in self.scored
            if s.similarity >= similarity or s.rerank >= rerank
        ]
        if confident:
            return max(confident)[1], briefs.TOPIC
        return self.latest, briefs.LATEST

    def index(self, meeting_id: str | None) -> str:
        return f"#{self.past_ids.index(meeting_id)}" if meeting_id in self.past_ids else "-"


@dataclass
class _Recording:
    retrieved: list[Candidate] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)


@contextmanager
def _recording() -> Iterator[_Recording]:
    """Wrap the topic step's retriever and re-ranker to keep what they returned."""
    recording = _Recording()
    reranker = briefs.get_reranker()

    class RecordingRetriever(HybridRetriever):
        def retrieve(self, *args: Any, **kwargs: Any) -> list[Candidate]:
            candidates = super().retrieve(*args, **kwargs)
            recording.retrieved = list(candidates)
            return candidates

    class RecordingReranker:
        def score(self, query: str, passages: list[str]) -> list[float]:
            scores = reranker.score(query, passages)
            recording.scores = [float(score) for score in scores]
            return scores

    with (
        mock.patch.object(briefs, "HybridRetriever", RecordingRetriever),
        mock.patch.object(briefs, "get_reranker", RecordingReranker),
    ):
        yield recording


def run_case(case: BriefCase) -> CaseResult:
    with session_scope() as s:
        team = Team(name=f"eval-{case.id}")
        s.add(team)
        s.flush()
        team_id = team.id

    try:
        now = datetime.now(tz=UTC)
        past_ids = [_seed_past(team_id, meeting, now) for meeting in case.past_meetings]
        for meeting_id, meeting in zip(past_ids, case.past_meetings, strict=True):
            service.run_topic_linking(_transcript(meeting_id, meeting.lines))

        with session_scope() as s:
            upcoming = Meeting(
                team_id=team_id,
                title=case.upcoming.title,
                status="scheduled",
                started_at=now + _LEAD,
            )
            s.add(upcoming)
            s.flush()
            agenda = [AgendaItem(title=title) for title in case.upcoming.agenda]
            with _recording() as recording:
                chosen, reason = briefs.choose_previous_meeting(s, upcoming, agenda, now=now)

        settings = get_settings()
        reranked = [c for c in recording.retrieved if c.linked_meeting_id in past_ids][
            : settings.rerank_top_k
        ]
        scored = tuple(
            Scored(c.linked_meeting_id, c.similarity, score)
            for c, score in zip(reranked, recording.scores, strict=False)
        )
        latest = min(zip(case.past_meetings, past_ids, strict=True), key=lambda p: p[0].days_ago)
        return CaseResult(
            case_id=case.id,
            category=case.category,
            past_ids=tuple(past_ids),
            expected=past_ids[case.expected] if case.expected is not None else None,
            related=frozenset(past_ids[i] for i in case.related),
            latest=latest[1],
            chosen=chosen,
            reason=reason,
            scored=scored,
        )
    finally:
        with session_scope() as s:
            s.execute(delete(Team).where(Team.id == team_id))


def _seed_past(team_id: str, meeting: PastMeeting, now: datetime) -> str:
    """A past meeting with its title and utterances, spoken by one consenting
    participant -- see ``eval.topic_linking.runner._seed_meeting``."""
    with session_scope() as s:
        row = Meeting(
            team_id=team_id,
            title=meeting.title,
            status="analyzing",
            started_at=now - timedelta(days=meeting.days_ago),
        )
        s.add(row)
        s.flush()
        speaker = Participant(meeting_id=row.id, speaker_label="화자", consented=True)
        s.add(speaker)
        s.flush()
        s.add_all(
            UtteranceRow(
                id=_utterance_id(row.id, i),
                meeting_id=row.id,
                participant_id=speaker.id,
                speaker_label="화자",
                start_sec=float(i),
                end_sec=float(i) + 1,
                text=line,
                confidence=0.9,
            )
            for i, line in enumerate(meeting.lines)
        )
        return row.id


def run_all(cases: list[BriefCase] | None = None) -> list[CaseResult]:
    return [run_case(case) for case in (cases if cases is not None else load_cases())]


def _rate(results: list[CaseResult], outcome: str, *, positives: bool) -> float:
    pool = [r for r in results if (r.expected is not None) == positives]
    return sum(r.outcome == outcome for r in pool) / len(pool) if pool else 0.0


def headline(results: list[CaseResult]) -> dict[str, float]:
    """The numbers ``--mode both`` puts side by side."""
    total = len(results)
    return {
        "accuracy": sum(r.correct for r in results) / total if total else 0.0,
        "lenient accuracy": sum(r.lenient for r in results) / total if total else 0.0,
        "no-related cases recapped": _rate(results, UNRELATED_RECAPPED, positives=False),
        "related cases missed": _rate(results, MISSED, positives=True),
    }


def case_outcomes(results: list[CaseResult]) -> dict[str, bool]:
    return {f"{r.category}/{r.case_id}": r.correct for r in results}


def report(results: list[CaseResult]) -> str:
    settings = get_settings()
    total = len(results)
    correct = sum(r.correct for r in results)
    lenient = sum(r.lenient for r in results)
    positives = [r for r in results if r.expected is not None]
    negatives = [r for r in results if r.expected is None]

    lines = [_case_line(r) for r in results]
    lines += [
        "",
        accuracy_line("brief selection accuracy", correct, total),
        accuracy_line("  lenient (any meeting on the subject)", lenient, total),
        f"link_similarity_threshold = {settings.link_similarity_threshold}, "
        f"link_confidence_threshold (rerank) = {settings.link_confidence_threshold}, "
        f"rerank_top_k = {settings.rerank_top_k}",
        "",
        "by category:",
        *by_category(results),
        "",
        "how each case ended:",
        *(
            f"  {outcome:<50} {sum(r.outcome == outcome for r in results):>2}"
            for outcome in _OUTCOMES
        ),
        "",
        "by the step that should answer -> the step that did:",
        *_paths(results),
        "",
        f"  a related meeting exists:  right {_right(positives)}",
        f"  no related meeting:        right {_right(negatives)}"
        f"  (recapped anyway: {sum(r.outcome == UNRELATED_RECAPPED for r in negatives)})",
    ]
    drifted = [
        r.case_id
        for r in results
        if r.choice_at(settings.link_similarity_threshold, settings.link_confidence_threshold)
        != (r.chosen, r.reason)
    ]
    if drifted:
        lines += [
            "",
            f"WARNING: the sweep's rule disagrees with briefs at the configured thresholds "
            f"on {', '.join(drifted)}; the sweep below is not trustworthy.",
        ]
    lines += [
        "",
        "threshold sweep (accuracy if link_similarity_threshold were t,",
        " link_confidence_threshold held as configured; series answers do not move):",
        *_sweep(results, settings.link_similarity_threshold, settings.link_confidence_threshold),
    ]
    return "\n".join(lines)


def _right(results: list[CaseResult]) -> str:
    return ratio(sum(r.correct for r in results), len(results))


def _expected_step(r: CaseResult) -> str:
    if r.expected is None:
        return briefs.LATEST
    return briefs.SERIES if r.category.startswith("series") else briefs.TOPIC


def _paths(results: list[CaseResult]) -> list[str]:
    counts: dict[tuple[str, str], int] = {}
    for r in results:
        key = (_expected_step(r), r.reason or "none")
        counts[key] = counts.get(key, 0) + 1
    return [f"  {want:<6} -> {got:<6} {n:>2}" for (want, got), n in sorted(counts.items())]


def _case_line(r: CaseResult) -> str:
    scores = " ".join(
        f"{r.index(s.meeting_id)}=s{s.similarity:.3f}/r{s.rerank:.3f}" for s in r.scored
    )
    expected = r.index(r.expected) if r.expected is not None else "none"
    return (
        f"[{'OK' if r.correct else 'FAIL'}] {r.category}/{r.case_id}: "
        f"expected={expected} chose={r.index(r.chosen)} ({r.reason}) "
        f"related=[{','.join(sorted(r.index(m) for m in r.related))}] "
        f"scores: {scores or '-'}" + ("" if r.correct else f"  <- {r.outcome}")
    )


def _sweep(results: list[CaseResult], current: float, rerank: float) -> list[str]:
    if not results:
        return []
    points = sweep_points(0.50, 0.95, 0.025)

    def at(t: float) -> tuple[int, int]:
        outcomes = [r.outcome_of(*r.choice_at(t, rerank)) for r in results]
        return sum(o == RIGHT for o in outcomes), sum(o == UNRELATED_RECAPPED for o in outcomes)

    best = max(points, key=lambda t: at(t)[0])
    rows = []
    for t in points:
        ok, recapped = at(t)
        marks = ("  <- current" if abs(t - current) < 1e-9 else "") + (
            "  <- best" if t == best else ""
        )
        rows.append(
            f"  t={t:.3f}  {ok:>2}/{len(results)} = {ok / len(results):.2f}"
            f"  unrelated recapped {recapped:>2}{marks}"
        )
    return rows
