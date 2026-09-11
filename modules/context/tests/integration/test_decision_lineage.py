"""End-to-end decision lineage against a real PostgreSQL.

Uses the ``fake`` model implementations: ``FakeEmbedder`` maps identical text to
an identical vector (so a decision restated verbatim threads onto the previous
one), and ``FakeNli`` marks a pair ``contradiction`` when exactly one side
negates, ``entailment`` when the two strings are equal, else ``neutral``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from autune_context import service
from autune_context.config import get_settings
from autune_context.models import CtxDecision, CtxDecisionVersion, CtxMeetingStatus
from autune_context.pipeline import reset_cache
from autune_contracts import (
    ContextLinks,
    PrivacyFlags,
    TranscriptMetadata,
    TranscriptReady,
    TranscriptSource,
    Utterance,
)
from autune_contracts.extraction import Decision, ExtractionResult
from autune_core import Meeting, Participant, Team, User, session_scope


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
        row = Team(name="lineage-test")
        s.add(row)
        s.flush()
        tid = row.id
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))
        s.execute(delete(User).where(User.email.like("lineage-%")))


def _user(email: str) -> str:
    with session_scope() as s:
        row = User(email=email, display_name=email)
        s.add(row)
        s.flush()
        return row.id


def _meeting(team_id: str, *, days_ago: int, present: list[str] | None = None) -> str:
    with session_scope() as s:
        row = Meeting(
            team_id=team_id,
            title="회의",
            status="analyzing",
            started_at=datetime.now(tz=UTC) - timedelta(days=days_ago),
        )
        s.add(row)
        s.flush()
        for i, user_id in enumerate(present or []):
            s.add(
                Participant(
                    meeting_id=row.id,
                    user_id=user_id,
                    speaker_label=f"화자{i}",
                    consented=True,
                )
            )
        return row.id


def _extraction(meeting_id: str, decisions: list[tuple[str, str, float]]) -> ExtractionResult:
    return ExtractionResult(
        meeting_id=meeting_id,
        decisions=[
            Decision(id=dec_id, statement=statement, source_utterance_ids=[], confidence=conf)
            for dec_id, statement, conf in decisions
        ],
    )


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


_D1 = "검색 정렬은 최신순으로 한다"


def test_a_new_decision_opens_a_thread(team_id: str) -> None:
    meeting = _meeting(team_id, days_ago=0)
    service.build_decision_lineage(_extraction(meeting, [("dec_1", _D1, 0.9)]))

    with session_scope() as s:
        versions = s.scalars(
            select(CtxDecisionVersion).where(CtxDecisionVersion.meeting_id == meeting)
        ).all()
        assert len(versions) == 1
        v = versions[0]
        assert v.change_type == "new"
        assert v.nli_label is None
        assert v.previous_version_id is None
        assert v.confidence == pytest.approx(0.9)
        assert s.scalar(select(CtxDecision.team_id).where(CtxDecision.id == v.thread_id)) == team_id
        status = s.get(CtxMeetingStatus, meeting)
        assert status is not None and status.lineage_done and status.extraction_seen


def test_a_restated_decision_threads_onto_the_previous_version(team_id: str) -> None:
    first = _meeting(team_id, days_ago=10)
    second = _meeting(team_id, days_ago=0)
    service.build_decision_lineage(_extraction(first, [("dec_1", _D1, 0.9)]))
    service.build_decision_lineage(_extraction(second, [("dec_2", _D1, 0.8)]))

    with session_scope() as s:
        v1 = s.scalars(
            select(CtxDecisionVersion).where(CtxDecisionVersion.meeting_id == first)
        ).one()
        v2 = s.scalars(
            select(CtxDecisionVersion).where(CtxDecisionVersion.meeting_id == second)
        ).one()
        assert v2.thread_id == v1.thread_id  # same lineage
        assert v2.previous_version_id == v1.id
        assert v2.previous_meeting_id == first
        assert v2.previous_statement == _D1
        assert v2.change_type == "unchanged"
        assert v2.nli_label == "entailment"


def test_a_negated_restatement_is_reversed(team_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the match: FakeEmbedder gives unrelated text unrelated vectors, and a
    # reversal is by definition a reworded statement.
    monkeypatch.setenv("AUTUNE_CONTEXT_LINEAGE_MATCH_THRESHOLD", "-1")
    get_settings.cache_clear()

    first = _meeting(team_id, days_ago=10)
    second = _meeting(team_id, days_ago=0)
    service.build_decision_lineage(_extraction(first, [("dec_1", _D1, 0.9)]))
    service.build_decision_lineage(
        _extraction(second, [("dec_2", "검색 정렬은 최신순으로 안 한다", 0.9)])
    )

    with session_scope() as s:
        v2 = s.scalars(
            select(CtxDecisionVersion).where(CtxDecisionVersion.meeting_id == second)
        ).one()
        assert v2.change_type == "reversed"
        assert v2.nli_label == "contradiction"


def test_absent_stakeholders_are_recorded(team_id: str) -> None:
    alice, bob = _user("lineage-alice@x"), _user("lineage-bob@x")
    first = _meeting(team_id, days_ago=10, present=[alice, bob])
    second = _meeting(team_id, days_ago=0, present=[alice])  # bob missed it

    service.build_decision_lineage(_extraction(first, [("dec_1", _D1, 0.9)]))
    service.build_decision_lineage(_extraction(second, [("dec_2", _D1, 0.9)]))

    with session_scope() as s:
        v2 = s.scalars(
            select(CtxDecisionVersion).where(CtxDecisionVersion.meeting_id == second)
        ).one()
        assert v2.key_stakeholders_absent == [bob]


def test_rebuild_replaces_versions_and_leaves_no_orphan_threads(team_id: str) -> None:
    first = _meeting(team_id, days_ago=10)
    second = _meeting(team_id, days_ago=0)
    service.build_decision_lineage(_extraction(first, [("dec_1", _D1, 0.9)]))

    service.build_decision_lineage(_extraction(second, [("dec_2", _D1, 0.9)]))
    with session_scope() as s:
        before = s.scalars(
            select(CtxDecisionVersion.id).where(CtxDecisionVersion.meeting_id == second)
        ).all()
        threads_before = s.scalars(select(CtxDecision.id)).all()

    service.build_decision_lineage(_extraction(second, [("dec_2", _D1, 0.9)]))
    with session_scope() as s:
        after = s.scalars(
            select(CtxDecisionVersion.id).where(CtxDecisionVersion.meeting_id == second)
        ).all()
        threads_after = s.scalars(select(CtxDecision.id)).all()

    assert len(after) == len(before) == 1
    assert set(before).isdisjoint(after)  # replaced, not appended
    assert len(threads_after) == len(threads_before) == 1  # still one thread, no orphan


def test_lineage_follows_meeting_time_not_processing_order(team_id: str) -> None:
    """B can finish a February meeting before January's — a long meeting, a
    backfill. The chain must follow when the meetings happened, not when B
    reported them."""
    jan = _meeting(team_id, days_ago=30)
    feb = _meeting(team_id, days_ago=20)
    mar = _meeting(team_id, days_ago=10)

    service.build_decision_lineage(_extraction(feb, [("dec_feb", _D1, 0.9)]))
    service.build_decision_lineage(_extraction(jan, [("dec_jan", _D1, 0.9)]))  # arrives late
    service.build_decision_lineage(_extraction(mar, [("dec_mar", _D1, 0.9)]))

    with session_scope() as s:
        by_meeting = {
            m: s.scalars(select(CtxDecisionVersion).where(CtxDecisionVersion.meeting_id == m)).one()
            for m in (jan, feb, mar)
        }
        assert by_meeting[jan].change_type == "new"
        assert by_meeting[jan].previous_version_id is None
        assert by_meeting[feb].previous_meeting_id == jan
        assert by_meeting[feb].previous_version_id == by_meeting[jan].id
        assert by_meeting[mar].previous_meeting_id == feb
        assert by_meeting[mar].previous_version_id == by_meeting[feb].id
        assert by_meeting[jan].thread_id == by_meeting[feb].thread_id == by_meeting[mar].thread_id


def test_rerunning_an_earlier_meeting_keeps_the_later_chain_intact(team_id: str) -> None:
    """A retried task (``acks_late``) or a manual re-run is not guaranteed to be
    the thread's most recent meeting. Re-threading must repair, not orphan, the
    versions that already chained onto the one being replaced."""
    first = _meeting(team_id, days_ago=10)
    second = _meeting(team_id, days_ago=0)
    service.build_decision_lineage(_extraction(first, [("dec_1", _D1, 0.9)]))
    service.build_decision_lineage(_extraction(second, [("dec_2", _D1, 0.9)]))

    service.build_decision_lineage(_extraction(first, [("dec_1b", _D1, 0.9)]))  # re-run

    with session_scope() as s:
        v1 = s.scalars(
            select(CtxDecisionVersion).where(CtxDecisionVersion.meeting_id == first)
        ).one()
        v2 = s.scalars(
            select(CtxDecisionVersion).where(CtxDecisionVersion.meeting_id == second)
        ).one()
        assert v1.change_type == "new"
        assert v1.previous_version_id is None
        assert v2.previous_version_id == v1.id  # re-pointed at the new row, not SET NULL
        assert v2.previous_meeting_id == first
        assert v2.previous_statement == _D1
        assert v2.change_type == "unchanged"


def test_publish_carries_the_decision_lineage(team_id: str, published: _CapturingApp) -> None:
    first = _meeting(team_id, days_ago=10)
    second = _meeting(team_id, days_ago=0)
    service.build_decision_lineage(_extraction(first, [("dec_1", _D1, 0.9)]))

    service.run_topic_linking(_transcript(second, ["검색 개인화 논의"] * 5))
    service.build_decision_lineage(_extraction(second, [("dec_2", _D1, 0.8)]))

    assert service.publish_if_ready(second) is True
    name, args = published.sent[0]
    assert name == "autune.intelligence.on_context_completed"
    links = ContextLinks.model_validate(args[0])
    assert links.missing_sources == []  # B reported and lineage was built
    assert len(links.decision_lineage) == 1
    change = links.decision_lineage[0]
    assert change.source_decision_id == "dec_2"
    assert change.previous_meeting_id == first
    assert change.change_type == "unchanged"
