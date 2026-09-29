"""End-to-end topic linking and publishing against a real PostgreSQL.

Uses the ``fake`` model implementations (deterministic, no network): identical
topic text embeds to an identical vector, so a repeated topic links to the past
meeting with a high score.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from autune_context import service
from autune_context.config import get_settings
from autune_context.models import CtxEmbedding, CtxMeetingStatus, CtxTopicLink
from autune_context.pipeline import reset_cache
from autune_contracts import (
    ContextLinks,
    PrivacyFlags,
    TranscriptMetadata,
    TranscriptReady,
    TranscriptSource,
    Utterance,
)
from autune_core import Meeting, Participant, Team, session_scope
from autune_core import Utterance as UtteranceRow


@pytest.fixture(autouse=True)
def _fake_models(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for knob in ("EMBEDDER", "RERANKER", "NLI", "LLM"):
        monkeypatch.setenv(f"AUTUNE_CONTEXT_{knob}_IMPL", "fake")
    monkeypatch.setenv("AUTUNE_CONTEXT_PUBLISH_TIMEOUT_S", "0")  # deadline = now
    get_settings.cache_clear()
    reset_cache()
    yield
    get_settings.cache_clear()
    reset_cache()


class _CapturingApp:
    def __init__(self) -> None:
        self.sent: list[tuple[str, list]] = []

    def send_task(self, name: str, *, args: list) -> None:
        self.sent.append((name, args))


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> _CapturingApp:
    app = _CapturingApp()
    monkeypatch.setattr(service, "current_app", app)
    return app


@pytest.fixture
def team_id(db_engine: object) -> Iterator[str]:  # db_engine ensures migrations ran
    with session_scope() as s:
        row = Team(name="svc-test")
        s.add(row)
        s.flush()
        tid = row.id
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))


def _meeting(team_id: str, *, days_ago: int, expires_at: datetime | None = None) -> str:
    with session_scope() as s:
        row = Meeting(
            team_id=team_id,
            title="회의",
            status="analyzing",
            started_at=datetime.now(tz=UTC) - timedelta(days=days_ago),
            expires_at=expires_at,
        )
        s.add(row)
        s.flush()
        return row.id


def _transcript(
    meeting_id: str,
    lines: list[str],
    *,
    speakers: list[str] | None = None,
    refusing: frozenset[str] = frozenset(),
) -> TranscriptReady:
    """The payload, plus what module A writes before publishing it: one
    ``participants`` row per speaker label (consenting unless in ``refusing``)
    and the ``utterances`` rows behind it. Safe to call again for a re-run."""
    labels = speakers or ["화자"] * len(lines)
    _persist(meeting_id, lines, labels, refusing)
    return TranscriptReady(
        meeting_id=meeting_id,
        utterances=[
            Utterance(
                id=f"utt_{meeting_id}_{i}",
                speaker=label,
                start=float(i),
                end=float(i) + 1,
                text=line,
                confidence=0.9,
            )
            for i, (line, label) in enumerate(zip(lines, labels, strict=True))
        ],
        metadata=TranscriptMetadata(
            duration=float(len(lines)),
            participants=["화자"],
            source=TranscriptSource.FILE_UPLOAD,
            language="ko",
            privacy=PrivacyFlags(original_audio_deleted=True, pii_masked=True),
        ),
    )


def _persist(
    meeting_id: str, lines: list[str], labels: list[str], refusing: frozenset[str]
) -> None:
    with session_scope() as s:
        existing = {
            p.speaker_label: p.id
            for p in s.scalars(select(Participant).where(Participant.meeting_id == meeting_id))
        }
        for label in dict.fromkeys(labels):
            if label not in existing:
                row = Participant(
                    meeting_id=meeting_id, speaker_label=label, consented=label not in refusing
                )
                s.add(row)
                s.flush()
                existing[label] = row.id
        have = set(
            s.scalars(select(UtteranceRow.id).where(UtteranceRow.meeting_id == meeting_id)).all()
        )
        s.add_all(
            UtteranceRow(
                id=f"utt_{meeting_id}_{i}",
                meeting_id=meeting_id,
                participant_id=existing[label],
                speaker_label=label,
                start_sec=float(i),
                end_sec=float(i) + 1,
                text=line,
                confidence=0.9,
            )
            for i, (line, label) in enumerate(zip(lines, labels, strict=True))
            if f"utt_{meeting_id}_{i}" not in have
        )


_SEARCH = ["검색 개인화 논의"] * 5
_SORT = ["정렬 방식 결정"] * 5


def test_a_repeated_topic_links_to_the_past_meeting(team_id: str) -> None:
    past = _meeting(team_id, days_ago=10)
    current = _meeting(team_id, days_ago=0)

    service.run_topic_linking(_transcript(past, _SEARCH + _SORT))
    service.run_topic_linking(_transcript(current, _SEARCH + _SORT))

    with session_scope() as s:
        links = s.scalars(select(CtxTopicLink).where(CtxTopicLink.meeting_id == current)).all()
        assert links
        assert all(link.linked_meeting_id == past for link in links)
        assert any(link.status == "asserted" for link in links)
        status = s.get(CtxMeetingStatus, current)
        assert status is not None and status.topic_linking_done is True


def test_an_expired_past_meeting_is_not_a_link_candidate(team_id: str) -> None:
    """`_corpus` and `_dense_ranking` must apply the same retention filter.

    A meeting past its `expires_at` still has rows until the retention sweep
    runs. If only one of the two rankings excludes it, a dense hit on it either
    crashes `retrieve()` with a `KeyError` (missing from `corpus`) or, if that
    were papered over with `.get()`, lets a link to it slip past the retention
    window (privacy.md section 4)."""
    expired = _meeting(team_id, days_ago=100, expires_at=datetime.now(tz=UTC) - timedelta(days=10))
    current = _meeting(team_id, days_ago=0)

    service.run_topic_linking(_transcript(expired, _SEARCH + _SORT))
    service.run_topic_linking(_transcript(current, _SEARCH + _SORT))  # must not raise

    with session_scope() as s:
        links = s.scalars(select(CtxTopicLink).where(CtxTopicLink.meeting_id == current)).all()
        assert all(link.linked_meeting_id != expired for link in links)


class _RecordingReranker:
    """Scores everything 0.9 and remembers what it was asked to compare."""

    model_version = "recording-reranker"

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def score(self, query: str, passages: list[str]) -> list[float]:
        self.calls.append((query, list(passages)))
        return [0.9] * len(passages)


def test_the_reranker_reads_the_past_segments_text_not_its_label(
    team_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each past meeting is judged on its topic segment closest to the query,
    read back from ``utterances`` -- not on a one-word label, which scored real
    matches near zero against a full segment of text."""
    past = _meeting(team_id, days_ago=10)
    current = _meeting(team_id, days_ago=0)
    service.run_topic_linking(_transcript(past, _SEARCH + _SORT))

    reranker = _RecordingReranker()
    monkeypatch.setattr(service, "get_reranker", lambda: reranker)
    service.run_topic_linking(_transcript(current, _SEARCH + _SORT))

    assert len(reranker.calls) == 2  # one per topic segment
    for query, passages in reranker.calls:
        # The fake embedder maps identical text to an identical vector, so the
        # closest past segment is the one with the same lines as the query.
        assert passages == [query]

    with session_scope() as s:
        stored = s.scalars(
            select(CtxEmbedding.utterance_ids).where(CtxEmbedding.meeting_id == past)
        ).all()
    assert sorted(len(ids or []) for ids in stored) == [5, 5]


def test_the_reranker_falls_back_to_the_label_without_utterances(
    team_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A past row whose utterances are gone (or which predates
    ``utterance_ids``) is still a candidate, scored against its label."""
    past = _meeting(team_id, days_ago=10)
    current = _meeting(team_id, days_ago=0)
    service.run_topic_linking(_transcript(past, _SEARCH + _SORT))
    with session_scope() as s:  # deleted since, e.g. by the person who said them
        s.execute(delete(UtteranceRow).where(UtteranceRow.meeting_id == past))

    reranker = _RecordingReranker()
    monkeypatch.setattr(service, "get_reranker", lambda: reranker)
    service.run_topic_linking(_transcript(current, _SEARCH + _SORT))

    with session_scope() as s:
        labels = set(
            s.scalars(select(CtxEmbedding.ref_label).where(CtxEmbedding.meeting_id == past)).all()
        )
    assert reranker.calls
    for _query, passages in reranker.calls:
        assert passages and set(passages) <= labels


def test_topic_linking_is_idempotent(team_id: str) -> None:
    past = _meeting(team_id, days_ago=10)
    current = _meeting(team_id, days_ago=0)
    service.run_topic_linking(_transcript(past, _SEARCH + _SORT))

    service.run_topic_linking(_transcript(current, _SEARCH + _SORT))
    with session_scope() as s:
        first = s.scalars(select(CtxTopicLink.id).where(CtxTopicLink.meeting_id == current)).all()
        embeds_first = s.scalars(
            select(CtxEmbedding.id).where(CtxEmbedding.meeting_id == current)
        ).all()

    service.run_topic_linking(_transcript(current, _SEARCH + _SORT))
    with session_scope() as s:
        second = s.scalars(select(CtxTopicLink.id).where(CtxTopicLink.meeting_id == current)).all()
        embeds_second = s.scalars(
            select(CtxEmbedding.id).where(CtxEmbedding.meeting_id == current)
        ).all()

    assert len(second) == len(first)
    assert len(embeds_second) == len(embeds_first)
    assert set(first).isdisjoint(second)  # replaced, not appended


def test_publish_names_extraction_as_missing_when_b_times_out(
    team_id: str, published: _CapturingApp
) -> None:
    meeting = _meeting(team_id, days_ago=0)
    service.run_topic_linking(_transcript(meeting, _SEARCH + _SORT))

    assert service.publish_if_ready(meeting) is True
    assert len(published.sent) == 1
    name, args = published.sent[0]
    assert name == "autune.intelligence.on_context_completed"
    links = ContextLinks.model_validate(args[0])
    assert links.meeting_id == meeting
    assert links.missing_sources == ["extraction"]
    assert links.decision_lineage == []

    # idempotent: a second call does not re-publish
    assert service.publish_if_ready(meeting) is False
    assert len(published.sent) == 1


def test_publish_has_no_missing_sources_once_b_has_reported(
    team_id: str, published: _CapturingApp
) -> None:
    meeting = _meeting(team_id, days_ago=0)
    service.run_topic_linking(_transcript(meeting, _SEARCH + _SORT))
    service.mark_extraction_seen(meeting)

    assert service.publish_if_ready(meeting) is True
    links = ContextLinks.model_validate(published.sent[0][1][0])
    assert links.missing_sources == []


def test_a_rerun_after_publish_republishes_the_rebuilt_links(
    team_id: str, published: _CapturingApp
) -> None:
    """The topic-link half of the reprocess gap: before, the rerun rebuilt
    ``ctx_topic_links`` and the ``published_at`` guard kept E on the old set."""
    current = _meeting(team_id, days_ago=0)
    assert service.run_topic_linking(_transcript(current, _SEARCH + _SORT)) is False
    assert service.publish_if_ready(current) is True
    assert ContextLinks.model_validate(published.sent[0][1][0]).topic_links == []

    # A past meeting on the same topics only exists by the time A reprocesses
    # ``current``, so the rerun finds links the first run could not.
    past = _meeting(team_id, days_ago=10)
    service.run_topic_linking(_transcript(past, _SEARCH + _SORT))
    assert service.run_topic_linking(_transcript(current, _SEARCH + _SORT)) is True

    assert service.publish_if_ready(current, force=True) is True
    links = ContextLinks.model_validate(published.sent[1][1][0])
    assert links.topic_links
    assert all(link.linked_meeting_id == past for link in links.topic_links)


def test_publish_waits_until_topic_linking_is_done(team_id: str, published: _CapturingApp) -> None:
    meeting = _meeting(team_id, days_ago=0)
    service.mark_extraction_seen(meeting)  # B first, D's topic linking not run yet

    assert service.publish_if_ready(meeting) is False
    assert published.sent == []


# --------------------------------------------------------------------------- #
# Consent — privacy.md section 5, the line modules B and C already draw
# --------------------------------------------------------------------------- #


def _topic_rows(meeting_id: str) -> list[CtxEmbedding]:
    with session_scope() as s:
        rows = s.scalars(select(CtxEmbedding).where(CtxEmbedding.meeting_id == meeting_id)).all()
        s.expunge_all()
        return list(rows)


def test_a_meeting_nobody_consented_to_is_not_analysed(team_id: str) -> None:
    past = _meeting(team_id, days_ago=10)
    current = _meeting(team_id, days_ago=0)
    service.run_topic_linking(_transcript(past, _SEARCH + _SORT))

    service.run_topic_linking(_transcript(current, _SEARCH + _SORT, refusing=frozenset({"화자"})))

    assert _topic_rows(current) == []
    with session_scope() as s:
        assert s.scalars(select(CtxTopicLink).where(CtxTopicLink.meeting_id == current)).all() == []
        status = s.get(CtxMeetingStatus, current)
        assert status is not None and status.topic_linking_done  # the publish is not held up


def test_only_a_consenting_speakers_utterances_reach_a_segment(team_id: str) -> None:
    meeting = _meeting(team_id, days_ago=0)
    speakers = ["동의"] * 5 + ["거부"] * 5

    service.run_topic_linking(
        _transcript(meeting, _SEARCH + _SORT, speakers=speakers, refusing=frozenset({"거부"}))
    )

    stored = [i for row in _topic_rows(meeting) for i in (row.utterance_ids or [])]
    assert stored
    assert set(stored) <= {f"utt_{meeting}_{i}" for i in range(5)}


def test_an_utterance_with_no_participant_behind_it_is_not_analysed(team_id: str) -> None:
    """Whether its speaker consented is unknown, and unknown is not yes."""
    meeting = _meeting(team_id, days_ago=0)
    transcript = _transcript(meeting, _SEARCH + _SORT)
    with session_scope() as s:
        s.execute(
            update(UtteranceRow)
            .where(UtteranceRow.meeting_id == meeting)
            .values(participant_id=None)
        )

    service.run_topic_linking(transcript)

    assert _topic_rows(meeting) == []


def test_a_rerun_after_a_speaker_withdraws_drops_what_they_said(team_id: str) -> None:
    meeting = _meeting(team_id, days_ago=0)
    service.run_topic_linking(_transcript(meeting, _SEARCH + _SORT))
    assert _topic_rows(meeting)
    with session_scope() as s:
        s.execute(
            update(Participant).where(Participant.meeting_id == meeting).values(consented=False)
        )

    service.run_topic_linking(_transcript(meeting, _SEARCH + _SORT))

    assert _topic_rows(meeting) == []


def test_the_reranker_never_reads_a_withdrawn_speakers_past_text(
    team_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``utterance_ids`` were stored while the past speaker consented; the
    text is checked again when read, so a withdrawal takes effect without
    re-running the past meeting. The past segment is still scored -- on its
    label, the same fallback as for deleted utterances."""
    past = _meeting(team_id, days_ago=10)
    current = _meeting(team_id, days_ago=0)
    service.run_topic_linking(_transcript(past, _SEARCH + _SORT))
    with session_scope() as s:
        s.execute(update(Participant).where(Participant.meeting_id == past).values(consented=False))

    reranker = _RecordingReranker()
    monkeypatch.setattr(service, "get_reranker", lambda: reranker)
    service.run_topic_linking(_transcript(current, _SEARCH + _SORT))

    labels = {row.ref_label for row in _topic_rows(past)}
    assert reranker.calls
    for _query, passages in reranker.calls:
        assert passages and set(passages) <= labels


# --------------------------------------------------------------------------- #
# rederive_topics -- the backfill after consent changed behind a meeting
# --------------------------------------------------------------------------- #


def _as_module_a_leaves_it(meeting_id: str) -> None:
    """The privacy flags module A sets on the row once it has transcribed."""
    with session_scope() as s:
        row = s.get(Meeting, meeting_id)
        assert row is not None
        row.pii_masked = True
        row.original_audio_deleted = True


def _analysed(team_id: str, lines: list[str], **transcript_kwargs: object) -> str:
    meeting = _meeting(team_id, days_ago=0)
    service.run_topic_linking(_transcript(meeting, lines, **transcript_kwargs))  # type: ignore[arg-type]
    _as_module_a_leaves_it(meeting)
    return meeting


def _utterance_ids_behind_topics(meeting_id: str) -> set[str]:
    return {uid for row in _topic_rows(meeting_id) for uid in (row.utterance_ids or [])}


def test_rederive_drops_what_a_non_consenting_speaker_said(team_id: str) -> None:
    # Before #439 every utterance was analysed: stand that state up by
    # analysing while both speakers count, then record the refusal.
    lines = _SEARCH + _SORT
    speakers = ["동의"] * 5 + ["거부"] * 5
    meeting = _analysed(team_id, lines, speakers=speakers)
    assert _utterance_ids_behind_topics(meeting) & {f"utt_{meeting}_{i}" for i in range(5, 10)}
    with session_scope() as s:
        s.execute(
            update(Participant)
            .where(Participant.meeting_id == meeting, Participant.speaker_label == "거부")
            .values(consented=False)
        )

    assert service.rederive_topics(meeting) is False  # analysed, not yet published

    kept = _utterance_ids_behind_topics(meeting)
    assert kept and kept <= {f"utt_{meeting}_{i}" for i in range(5)}


def test_rederive_picks_up_consent_attested_after_analysis(team_id: str) -> None:
    meeting = _analysed(team_id, _SEARCH + _SORT, refusing=frozenset({"화자"}))
    assert _topic_rows(meeting) == []
    with session_scope() as s:  # what autune_audio.service.attest_consent does
        s.execute(
            update(Participant).where(Participant.meeting_id == meeting).values(consented=True)
        )

    service.rederive_topics(meeting)

    assert _topic_rows(meeting)


def test_rederive_reports_a_published_meeting_so_it_is_republished(team_id: str) -> None:
    meeting = _analysed(team_id, _SEARCH + _SORT)
    with session_scope() as s:
        status = s.get(CtxMeetingStatus, meeting)
        assert status is not None
        status.published_at = datetime.now(tz=UTC)

    assert service.rederive_topics(meeting) is True


@pytest.mark.parametrize("state", ["not_analysed", "expired", "no_privacy_guarantees"])
def test_rederive_leaves_alone_what_it_must_not_stand_in_for(team_id: str, state: str) -> None:
    meeting = _analysed(team_id, _SEARCH + _SORT)
    before = sorted(row.id for row in _topic_rows(meeting))
    with session_scope() as s:
        row = s.get(Meeting, meeting)
        assert row is not None
        if state == "not_analysed":
            s.execute(delete(CtxMeetingStatus).where(CtxMeetingStatus.meeting_id == meeting))
        elif state == "expired":
            row.expires_at = datetime.now(tz=UTC) - timedelta(minutes=1)
        else:
            row.pii_masked = False

    assert service.rederive_topics(meeting) is None
    assert sorted(row.id for row in _topic_rows(meeting)) == before


def test_rederivable_meetings_are_this_teams_analysed_ones_oldest_first(team_id: str) -> None:
    newer = _meeting(team_id, days_ago=1)
    older = _meeting(team_id, days_ago=5)
    never_analysed = _meeting(team_id, days_ago=3)
    for meeting in (newer, older):
        service.run_topic_linking(_transcript(meeting, _SEARCH))

    with session_scope() as s:
        ids = service.rederivable_meeting_ids(s, team_id=team_id)

    assert ids == [older, newer]
    assert never_analysed not in ids
