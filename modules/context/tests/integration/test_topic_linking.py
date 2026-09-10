"""End-to-end topic linking and publishing against a real PostgreSQL.

Uses the ``fake`` model implementations (deterministic, no network): identical
topic text embeds to an identical vector, so a repeated topic links to the past
meeting with a high score.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

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
from autune_core import Meeting, Team, session_scope


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


def test_publish_waits_until_topic_linking_is_done(team_id: str, published: _CapturingApp) -> None:
    meeting = _meeting(team_id, days_ago=0)
    service.mark_extraction_seen(meeting)  # B first, D's topic linking not run yet

    assert service.publish_if_ready(meeting) is False
    assert published.sent == []
