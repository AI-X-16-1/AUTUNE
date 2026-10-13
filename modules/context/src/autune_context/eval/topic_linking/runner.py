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

A ``version: 2`` set (``dataset.EvalSeries``) runs one team per series, its
meetings processed oldest first as production would, and scores every meeting
from the second on as a case of its own. Because its blocks are tagged, it also
scores two things a ``version: 1`` set cannot: whether a segment's label names
the topic the segment is about, and whether an asserted link joins segments
that are about a topic the linked meeting actually had.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from autune_context import service
from autune_context.config import get_settings
from autune_context.eval.metrics import accuracy_line, by_category, ratio, sweep_points
from autune_context.eval.topic_linking.dataset import (
    EvalCase,
    EvalMeeting,
    EvalSeries,
    SeriesMeeting,
    load_cases,
)
from autune_context.models import CtxEmbedding, CtxTopicLink
from autune_contracts import (
    PrivacyFlags,
    TranscriptMetadata,
    TranscriptReady,
    TranscriptSource,
    Utterance,
)
from autune_core import Meeting, Participant, Team, session_scope
from autune_core import Utterance as UtteranceRow

# The six-week target from docs/modules/context.md, "Metric" / "Phased
# delivery". The three-month target (0.85+) is out of the six-week scope
# (docs/modules/context.md, "Out of the six-week scope") and not checked here.
_TARGET_ACCURACY = 0.75


@dataclass(frozen=True)
class SegmentOutcome:
    """One topic segment of a series meeting, read against the block tags."""

    label: str
    topic: str | None
    """The tag most of the segment's utterances carry; ``None`` when that is an
    opening, closing or small-talk block."""
    names_topic: bool
    """The label contains one of ``EvalSeries.topics[topic]``'s words."""


@dataclass(frozen=True)
class LinkOutcome:
    """One asserted link of a series meeting, read against the block tags."""

    label: str
    on_topic: bool
    """A segment carrying this label is about a topic the linked meeting had.
    Labels can repeat within a meeting; any segment under the label counts."""
    from_untagged: bool
    """Every segment under this label is an opening, closing or small talk."""


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

    def asserted_at(self, similarity: float, rerank: float) -> frozenset[str]:
        """What ``asserted`` would have been with ``link_similarity_threshold``
        at ``similarity`` and ``link_confidence_threshold`` at ``rerank`` —
        every candidate row is already written, pending or not, so this needs
        no re-run. A meeting the keyphrase check held back (``held_back``)
        stays out on the similarity side; one under the configured threshold
        was never checked, and counts as passing."""
        return frozenset(
            m
            for m in self.best_similarity
            if (self.best_similarity[m] >= similarity and m not in self.held_back)
            or self.best_rerank[m] >= rerank
        )

    segments: tuple[SegmentOutcome, ...] = field(default=())
    """Series cases only: the current meeting's topic segments."""
    asserted_links: tuple[LinkOutcome, ...] = field(default=())
    """Series cases only: one per asserted link row."""
    held_back: frozenset[str] = field(default=frozenset())
    """Past meetings dense similarity alone would have asserted that were left
    ``pending``: the keyphrase check (``service._subjects_agree``) said no."""

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
            asserted, pending, best_similarity, best_rerank = _link_sets(links)

        expected = frozenset(past_ids[i] for i in case.expected_linked_indices)
        held_back = _held_back(asserted, best_similarity, best_rerank)
        return CaseResult(
            case_id=case.id,
            category=case.category,
            expected=expected,
            asserted=asserted,
            pending=pending,
            best_similarity=best_similarity,
            best_rerank=best_rerank,
            past_ids=tuple(past_ids),
            held_back=held_back,
        )
    finally:
        with session_scope() as s:
            s.execute(delete(Team).where(Team.id == team_id))


def run_series(series: EvalSeries) -> list[CaseResult]:
    """One team, its meetings processed oldest first; a result per meeting from
    the second on, with every earlier meeting as its history."""
    with session_scope() as s:
        team = Team(name=f"eval-{series.id}")
        s.add(team)
        s.flush()
        team_id = team.id

    results: list[CaseResult] = []
    try:
        ids: list[str] = []
        for index, meeting in enumerate(series.meetings):
            meeting_id = _seed_meeting(team_id, meeting)
            service.run_topic_linking(_transcript(meeting_id, meeting.lines))
            ids.append(meeting_id)
            if index == 0:
                continue

            with session_scope() as s:
                links = s.scalars(
                    select(CtxTopicLink).where(CtxTopicLink.meeting_id == meeting_id)
                ).all()
                asserted, pending, best_similarity, best_rerank = _link_sets(links)
                segments = _segments(s, series, meeting, meeting_id)
                asserted_links = tuple(
                    _link_outcome(
                        link.topic_label, segments, series, ids.index(link.linked_meeting_id)
                    )
                    for link in links
                    if link.status == "asserted" and link.linked_meeting_id is not None
                )

            expected_indices = series.expected_linked_indices(index)
            results.append(
                CaseResult(
                    case_id=f"{series.id}#{index}",
                    category=_series_category(expected_indices, index),
                    expected=frozenset(ids[j] for j in expected_indices),
                    asserted=asserted,
                    pending=pending,
                    best_similarity=best_similarity,
                    best_rerank=best_rerank,
                    past_ids=tuple(ids[:index]),
                    segments=segments,
                    asserted_links=asserted_links,
                    held_back=_held_back(asserted, best_similarity, best_rerank),
                )
            )
        return results
    finally:
        with session_scope() as s:
            s.execute(delete(Team).where(Team.id == team_id))


def _held_back(
    asserted: frozenset[str], best_similarity: dict[str, float], best_rerank: dict[str, float]
) -> frozenset[str]:
    settings = get_settings()
    return frozenset(
        m
        for m, sim in best_similarity.items()
        if sim >= settings.link_similarity_threshold
        and m not in asserted
        and best_rerank[m] < settings.link_confidence_threshold
    )


def _series_category(expected: frozenset[int], index: int) -> str:
    if not expected:
        return "no_link"
    return "all_history" if len(expected) == index else "some_history"


def _link_sets(
    links: Sequence[CtxTopicLink],
) -> tuple[frozenset[str], frozenset[str], dict[str, float], dict[str, float]]:
    """(asserted, pending, best similarity, best rerank score) per linked meeting."""
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
    return asserted, pending, best_similarity, best_rerank


def _segments(
    session: Session, series: EvalSeries, meeting: SeriesMeeting, meeting_id: str
) -> tuple[SegmentOutcome, ...]:
    """The meeting's topic segments, each tagged by most of its utterances."""
    line_topics = meeting.line_topics
    rows = session.scalars(
        select(CtxEmbedding)
        .where(CtxEmbedding.meeting_id == meeting_id, CtxEmbedding.kind == "topic")
        .order_by(CtxEmbedding.id)
    ).all()
    outcomes = []
    for row in rows:
        tags = Counter(line_topics[_line_index(u)] for u in row.utterance_ids or [])
        topic = tags.most_common(1)[0][0] if tags else None
        names = topic is not None and any(word in row.ref_label for word in series.topics[topic])
        outcomes.append(SegmentOutcome(label=row.ref_label, topic=topic, names_topic=names))
    return tuple(outcomes)


def _link_outcome(
    label: str, segments: tuple[SegmentOutcome, ...], series: EvalSeries, linked_index: int
) -> LinkOutcome:
    mine = [seg for seg in segments if seg.label == label]
    had = series.meetings[linked_index].topic_set
    return LinkOutcome(
        label=label,
        on_topic=any(seg.topic is not None and seg.topic in had for seg in mine),
        from_untagged=bool(mine) and all(seg.topic is None for seg in mine),
    )


def _line_index(utterance_id: str) -> int:
    """``_utterance_id``'s inverse."""
    return int(utterance_id.rsplit("_", 1)[1])


def _seed_meeting(team_id: str, meeting: EvalMeeting | SeriesMeeting) -> str:
    """The meeting row plus its ``utterances``, as module A leaves them before
    publishing ``TranscriptReady`` -- the re-ranker reads a past topic's text
    back from ``utterances``, so a meeting seeded without them would be scored
    against its label alone. One consenting participant speaks every line:
    topic linking analyses only a consenting speaker's utterances, so without
    it nothing would be linked at all. Deleted with the team, by cascade."""
    with session_scope() as s:
        row = Meeting(
            team_id=team_id,
            title=f"eval meeting (days_ago={meeting.days_ago})",
            status="analyzing",
            started_at=datetime.now(tz=UTC) - timedelta(days=meeting.days_ago),
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


def run_all(cases: list[EvalCase | EvalSeries] | None = None) -> list[CaseResult]:
    results: list[CaseResult] = []
    for item in cases if cases is not None else load_cases():
        if isinstance(item, EvalSeries):
            results += run_series(item)
        else:
            results.append(run_case(item))
    return results


def _pair_counts(results: list[CaseResult]) -> tuple[int, int, int]:
    """(true positive, false positive, false negative) asserted links, counted
    per (current meeting, past meeting) pair."""
    return (
        sum(len(r.asserted & r.expected) for r in results),
        sum(len(r.asserted - r.expected) for r in results),
        sum(len(r.expected - r.asserted) for r in results),
    )


def _f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def headline(results: list[CaseResult]) -> dict[str, float]:
    """The numbers ``--mode both`` puts side by side."""
    precision, recall, f1 = _f1(*_pair_counts(results))
    negatives = [r for r in results if not r.expected]
    metrics = {
        "accuracy": sum(r.correct for r in results) / len(results) if results else 0.0,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "no-link cases falsely linked": (
            sum(bool(r.asserted) for r in negatives) / len(negatives) if negatives else 0.0
        ),
    }
    segments = [seg for r in results for seg in r.segments]
    if segments:
        links = [link for r in results for link in r.asserted_links]
        metrics["labels naming their topic"] = sum(seg.names_topic for seg in segments) / len(
            segments
        )
        metrics["asserted links on topic"] = (
            sum(link.on_topic for link in links) / len(links) if links else 0.0
        )
    return metrics


def case_outcomes(results: list[CaseResult]) -> dict[str, bool]:
    return {f"{r.category}/{r.case_id}": r.correct for r in results}


def report(results: list[CaseResult]) -> str:
    """Per-case lines, the KPI, then the numbers that explain it.

    Past meetings print as their index in the case (``#0``, ``#1``), with the
    best dense similarity (``s``) and rerank score (``r``) each got, so a FAIL
    line reads against the fixture directly instead of against throwaway
    ``mtg_`` ids. Under ``engine_mode="llm"`` ``r`` is the LLM's probability that
    it is the same topic, and the threshold sweep is left out: the dense
    similarity no longer decides anything, so a sweep over it would read as a
    tuning result that it is not.
    """
    settings = get_settings()
    llm_mode = settings.engine_mode == "llm"
    lines = [_case_line(r) for r in results]
    correct = sum(r.correct for r in results)
    total = len(results)
    accuracy = correct / total if total else 0.0
    verdict = "PASS" if accuracy >= _TARGET_ACCURACY else "BELOW TARGET"

    tp, fp, fn = _pair_counts(results)
    surfaced = sum(len((r.asserted | r.pending) & r.expected) for r in results)
    negatives = [r for r in results if not r.expected]
    false_linked = sum(bool(r.asserted) for r in negatives)
    _precision, _recall, f1 = _f1(tp, fp, fn)

    lines += [
        "",
        accuracy_line("topic linking accuracy", correct, total)
        + f" (target {_TARGET_ACCURACY}) -- {verdict}",
        (
            f"engine_mode = llm ({settings.llm_impl}:{settings.llm_model_name}): "
            f"llm_link_threshold = "
            f"{settings.llm_link_threshold}, asked about the top "
            f"{settings.llm_topic_candidates} candidates per topic"
            if llm_mode
            else f"link_similarity_threshold = {settings.link_similarity_threshold}, "
            f"link_confidence_threshold (rerank) = {settings.link_confidence_threshold}"
        ),
        *(
            [
                f"engine_mode = hybrid: every link asserted above is one "
                f"{settings.llm_impl}:{settings.llm_model_name} confirmed at "
                f"llm_link_threshold = {settings.llm_link_threshold} (a link it was "
                f"under {settings.llm_pending_floor} on was dropped)"
            ]
            if settings.engine_mode == "hybrid"
            else []
        ),
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
    ]
    lines += _series_lines(results)
    if settings.engine_mode == "classic":
        # Only classic: the sweep re-derives ``asserted`` from the stored dense and
        # re-ranker scores, which in ``llm`` and ``hybrid`` no longer decide it.
        lines += [
            "",
            "threshold sweep (accuracy if link_similarity_threshold were t,",
            " link_confidence_threshold and the keyphrase check held as configured;",
            " below the configured t the keyphrase check was not run and counts as passed):",
            *_sweep(
                results, settings.link_similarity_threshold, settings.link_confidence_threshold
            ),
        ]
    return "\n".join(lines)


def _series_lines(results: list[CaseResult]) -> list[str]:
    """What only tagged blocks make measurable; nothing for a ``version: 1`` set."""
    segments = [seg for r in results for seg in r.segments]
    if not segments:
        return []
    links = [link for r in results for link in r.asserted_links]
    tagged = [seg for seg in segments if seg.topic is not None]
    named = sum(seg.names_topic for seg in segments)
    return [
        "",
        "topic segments of the scored meetings, read against the block tags:",
        f"  label names the segment's own topic   {ratio(named, len(segments))}",
        f"    among segments about a topic        {ratio(named, len(tagged))}",
        f"  segment is an opening, closing or small talk  "
        f"{ratio(len(segments) - len(tagged), len(segments))}",
        "asserted link rows, read against the block tags:",
        f"  join a topic the linked meeting had   "
        f"{ratio(sum(k.on_topic for k in links), len(links))}",
        f"  made by an opening, closing or small talk  "
        f"{ratio(sum(k.from_untagged for k in links), len(links))}",
    ]


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


def _sweep(results: list[CaseResult], current: float, rerank: float) -> list[str]:
    if not results:
        return []
    points = sweep_points(0.50, 0.95, 0.025)

    def correct_at(t: float) -> int:
        return sum(r.asserted_at(t, rerank) == r.expected for r in results)

    rows = []
    best = max(points, key=correct_at)
    for t in points:
        ok = correct_at(t)
        marks = ("  <- current" if abs(t - current) < 1e-9 else "") + (
            "  <- best" if t == best else ""
        )
        rows.append(f"  t={t:.3f}  {ok:>2}/{len(results)} = {ok / len(results):.2f}{marks}")
    return rows
