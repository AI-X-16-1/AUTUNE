"""``engine_mode="llm"`` end to end against a real PostgreSQL, with a scripted LLM.

The re-ranker and NLI impls are pointed at names that do not exist: in ``llm`` mode
the service must not construct either, so a stray call fails these tests with the
registry's ``unknown ... IMPL`` error instead of quietly using the trained stack.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from autune_context import service
from autune_context.config import get_settings
from autune_context.models import CtxDecisionVersion, CtxTopicLink
from autune_context.pipeline import registry, reset_cache
from autune_context.pipeline.llm import FakeLlm
from autune_contracts import (
    PrivacyFlags,
    TranscriptMetadata,
    TranscriptReady,
    TranscriptSource,
    Utterance,
)
from autune_contracts.extraction import Decision, ExtractionResult
from autune_core import Meeting, Participant, Team, session_scope
from autune_core import Utterance as UtteranceRow


def _topic(same: bool, confidence: float) -> str:
    return json.dumps({"same_topic": same, "confidence": confidence})


def _relation(relation: str, confidence: float) -> str:
    return json.dumps({"relation": relation, "confidence": confidence})


@pytest.fixture
def llm_answers(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    """``llm_answers["default"] = ...`` scripts what the LLM replies with."""
    answers = {"default": _topic(True, 0.9)}
    made: list[FakeLlm] = []

    def build(settings) -> FakeLlm:
        llm = FakeLlm(settings, default=answers["default"])
        made.append(llm)
        return llm

    monkeypatch.setitem(registry._LLMS, "scripted", build)
    monkeypatch.setenv("AUTUNE_CONTEXT_ENGINE_MODE", "llm")
    monkeypatch.setenv("AUTUNE_CONTEXT_LLM_IMPL", "scripted")
    monkeypatch.setenv("AUTUNE_CONTEXT_EMBEDDER_IMPL", "fake")
    monkeypatch.setenv("AUTUNE_CONTEXT_RERANKER_IMPL", "does_not_exist")
    monkeypatch.setenv("AUTUNE_CONTEXT_NLI_IMPL", "does_not_exist")
    get_settings.cache_clear()
    reset_cache()
    yield answers
    get_settings.cache_clear()
    reset_cache()


@pytest.fixture
def team_id(db_engine: object) -> Iterator[str]:  # db_engine ensures migrations ran
    with session_scope() as s:
        row = Team(name="llm-mode-test")
        s.add(row)
        s.flush()
        tid = row.id
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))


def _meeting(team_id: str, *, days_ago: int) -> str:
    with session_scope() as s:
        row = Meeting(
            team_id=team_id,
            title="회의",
            status="analyzing",
            started_at=datetime.now(tz=UTC) - timedelta(days=days_ago),
        )
        s.add(row)
        s.flush()
        return row.id


def _persist(meeting_id: str, lines: list[str]) -> None:
    """What module A writes before publishing a transcript: a consenting
    participant and the utterance rows behind it. Only a consenting speaker's
    utterances are analysed, so a transcript without them has no topics."""
    with session_scope() as s:
        participant = Participant(meeting_id=meeting_id, speaker_label="화자", consented=True)
        s.add(participant)
        s.flush()
        s.add_all(
            UtteranceRow(
                id=f"utt_{meeting_id}_{i}",
                meeting_id=meeting_id,
                participant_id=participant.id,
                speaker_label="화자",
                start_sec=float(i),
                end_sec=float(i) + 1,
                text=line,
                confidence=0.9,
            )
            for i, line in enumerate(lines)
        )


def _transcript(meeting_id: str, lines: list[str]) -> TranscriptReady:
    _persist(meeting_id, lines)
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


def _extraction(meeting_id: str, dec_id: str, statement: str) -> ExtractionResult:
    return ExtractionResult(
        meeting_id=meeting_id,
        decisions=[
            Decision(id=dec_id, statement=statement, source_utterance_ids=[], confidence=0.9)
        ],
    )


_LINES = ["검색 개인화 논의"] * 5 + ["정렬 방식 결정"] * 5


def test_topic_links_are_the_llms_call_not_the_thresholds(
    team_id: str, llm_answers: dict[str, str]
) -> None:
    past = _meeting(team_id, days_ago=10)
    current = _meeting(team_id, days_ago=0)

    service.run_topic_linking(_transcript(past, _LINES))
    llm_answers["default"] = _topic(True, 0.9)
    service.run_topic_linking(_transcript(current, _LINES))

    with session_scope() as s:
        links = s.scalars(select(CtxTopicLink).where(CtxTopicLink.meeting_id == current)).all()
        assert links
        assert all(link.linked_meeting_id == past for link in links)
        assert {link.status for link in links} == {"asserted"}
        assert all(link.reranker_version.startswith("judge-v1+") for link in links)


def test_a_topic_the_llm_calls_different_is_not_linked_however_similar_the_text(
    team_id: str, llm_answers: dict[str, str]
) -> None:
    past = _meeting(team_id, days_ago=10)
    current = _meeting(team_id, days_ago=0)
    llm_answers["default"] = _topic(False, 0.95)

    service.run_topic_linking(_transcript(past, _LINES))
    service.run_topic_linking(_transcript(current, _LINES))  # identical text: cosine 1.0

    with session_scope() as s:
        assert s.scalars(select(CtxTopicLink).where(CtxTopicLink.meeting_id == current)).all() == []


def test_a_decision_is_threaded_and_classified_by_the_llm(
    team_id: str, llm_answers: dict[str, str]
) -> None:
    earlier = _meeting(team_id, days_ago=10)
    later = _meeting(team_id, days_ago=0)
    llm_answers["default"] = _relation("reversed", 0.9)

    service.build_decision_lineage(_extraction(earlier, "dec_1", "정렬은 최신순으로 한다"))
    service.build_decision_lineage(_extraction(later, "dec_2", "정렬은 인기순으로 바꾼다"))

    with session_scope() as s:
        first = s.scalars(
            select(CtxDecisionVersion).where(CtxDecisionVersion.meeting_id == earlier)
        ).one()
        second = s.scalars(
            select(CtxDecisionVersion).where(CtxDecisionVersion.meeting_id == later)
        ).one()
        assert second.thread_id == first.thread_id
        assert second.previous_version_id == first.id
        assert second.change_type == "reversed"
        assert second.confidence == pytest.approx(0.9)
        assert second.nli_label is None  # there was no NLI model to have a label
        assert second.nli_version.startswith("judge-v1+")
        assert first.change_type == "new"


def test_a_decision_the_llm_calls_unrelated_opens_its_own_thread(
    team_id: str, llm_answers: dict[str, str]
) -> None:
    earlier = _meeting(team_id, days_ago=10)
    later = _meeting(team_id, days_ago=0)
    llm_answers["default"] = _relation("unrelated", 0.95)

    service.build_decision_lineage(_extraction(earlier, "dec_1", "정렬은 최신순으로 한다"))
    service.build_decision_lineage(
        _extraction(later, "dec_2", "정렬은 최신순으로 한다")
    )  # same text

    with session_scope() as s:
        threads = s.scalars(
            select(CtxDecisionVersion.thread_id).where(
                CtxDecisionVersion.meeting_id.in_([earlier, later])
            )
        ).all()
        assert len(set(threads)) == 2


# --- hybrid ------------------------------------------------------------------


@pytest.fixture
def hybrid_answers(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    """As ``llm_answers``, but with the trained stack present (fake): ``hybrid``
    needs the re-ranker and NLI as well as the LLM."""
    answers = {"default": _topic(True, 0.9)}
    made: list[FakeLlm] = []

    def build(settings) -> FakeLlm:
        llm = FakeLlm(settings, default=answers["default"])
        made.append(llm)
        return llm

    monkeypatch.setitem(registry._LLMS, "scripted", build)
    monkeypatch.setenv("AUTUNE_CONTEXT_ENGINE_MODE", "hybrid")
    monkeypatch.setenv("AUTUNE_CONTEXT_LLM_IMPL", "scripted")
    for knob in ("EMBEDDER", "RERANKER", "NLI"):
        monkeypatch.setenv(f"AUTUNE_CONTEXT_{knob}_IMPL", "fake")
    get_settings.cache_clear()
    reset_cache()
    yield answers
    get_settings.cache_clear()
    reset_cache()


def test_hybrid_keeps_a_link_the_llm_confirms(team_id: str, hybrid_answers: dict[str, str]) -> None:
    past = _meeting(team_id, days_ago=10)
    current = _meeting(team_id, days_ago=0)
    service.run_topic_linking(_transcript(past, _LINES))
    service.run_topic_linking(_transcript(current, _LINES))  # identical text: classic asserts

    with session_scope() as s:
        links = s.scalars(select(CtxTopicLink).where(CtxTopicLink.meeting_id == current)).all()
        assert links
        assert {link.status for link in links} == {"asserted"}
        assert all("|judge-v1+" in link.reranker_version for link in links)


def test_hybrid_drops_a_link_classic_asserted_and_the_llm_rejects(
    team_id: str, hybrid_answers: dict[str, str]
) -> None:
    past = _meeting(team_id, days_ago=10)
    current = _meeting(team_id, days_ago=0)
    hybrid_answers["default"] = _topic(False, 0.95)

    service.run_topic_linking(_transcript(past, _LINES))
    service.run_topic_linking(_transcript(current, _LINES))

    with session_scope() as s:
        assert s.scalars(select(CtxTopicLink).where(CtxTopicLink.meeting_id == current)).all() == []


def test_hybrid_runs_decision_lineage_classic(team_id: str, hybrid_answers: dict[str, str]) -> None:
    """No LLM call for lineage: the version column is the NLI model's, and the NLI
    label is recorded, exactly as in classic."""
    earlier = _meeting(team_id, days_ago=10)
    later = _meeting(team_id, days_ago=0)

    service.build_decision_lineage(_extraction(earlier, "dec_1", "정렬은 최신순으로 한다"))
    service.build_decision_lineage(_extraction(later, "dec_2", "정렬은 최신순으로 한다"))

    with session_scope() as s:
        second = s.scalars(
            select(CtxDecisionVersion).where(CtxDecisionVersion.meeting_id == later)
        ).one()
        assert second.nli_version == "fake-nli-v1"
        assert second.nli_label == "entailment"
        assert second.change_type == "unchanged"
