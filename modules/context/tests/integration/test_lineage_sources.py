"""Decision lineage reads a decision's source utterances -- consented ones only --
to thread a terse statement (``service._context_vectors``) and to classify how it
changed (``service._change_hypotheses``)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from autune_context import service
from autune_context.config import get_settings
from autune_context.models import CtxDecisionVersion
from autune_context.pipeline import reset_cache
from autune_contracts import ExtractionResult
from autune_contracts.extraction import Decision
from autune_core import Meeting, Participant, Team, Utterance, session_scope


class _KeywordEmbedder:
    """One axis per subject word; a terse statement names no subject."""

    model_version = "keyword-stub"
    dim = 4
    _AXES = ("배너", "주차", "예산")

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(axis in t) for axis in self._AXES] + [0.05] for t in texts]


@pytest.fixture
def team_id(db_engine: object, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    for knob in ("EMBEDDER", "RERANKER", "NLI", "LLM"):
        monkeypatch.setenv(f"AUTUNE_CONTEXT_{knob}_IMPL", "fake")
    get_settings.cache_clear()
    reset_cache()
    monkeypatch.setattr(service, "get_embedder", lambda: _KeywordEmbedder())
    with session_scope() as s:
        team = Team(name="lineage-sources-test")
        s.add(team)
        s.flush()
        tid = team.id
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))
    get_settings.cache_clear()
    reset_cache()


def _meeting(team_id: str, days_ago: int, lines: list[tuple[str, bool]]) -> tuple[str, list[str]]:
    with session_scope() as s:
        meeting = Meeting(
            team_id=team_id,
            title="회의",
            status="analyzing",
            started_at=datetime.now(tz=UTC) - timedelta(days=days_ago),
        )
        s.add(meeting)
        s.flush()
        yes = Participant(meeting_id=meeting.id, speaker_label="SPEAKER_00", consented=True)
        no = Participant(meeting_id=meeting.id, speaker_label="SPEAKER_01", consented=False)
        s.add_all([yes, no])
        s.flush()
        ids = []
        for i, (text, consented) in enumerate(lines):
            who = yes if consented else no
            ids.append(f"utt_{meeting.id}_{i}")
            s.add(
                Utterance(
                    id=ids[-1],
                    meeting_id=meeting.id,
                    participant_id=who.id,
                    speaker_label=who.speaker_label,
                    start_sec=float(i),
                    end_sec=float(i) + 1,
                    text=text,
                    confidence=0.9,
                )
            )
        return meeting.id, ids


def _change_type(meeting_id: str) -> str:
    with session_scope() as s:
        return s.scalars(
            select(CtxDecisionVersion.change_type).where(
                CtxDecisionVersion.meeting_id == meeting_id
            )
        ).one()


def _decide(meeting_id: str, statement: str, ids: list[str]) -> str:
    service.build_decision_lineage(
        ExtractionResult(
            meeting_id=meeting_id,
            decisions=[
                Decision(
                    id=f"dec_{meeting_id}",
                    statement=statement,
                    source_utterance_ids=ids,
                    confidence=0.9,
                )
            ],
        )
    )
    with session_scope() as s:
        return s.scalars(
            select(CtxDecisionVersion.thread_id).where(CtxDecisionVersion.meeting_id == meeting_id)
        ).one()


def test_a_terse_decision_joins_the_thread_its_utterances_name(team_id: str) -> None:
    past, past_ids = _meeting(team_id, 10, [("홈 화면 배너는 세 개로 하죠.", True)])
    now, now_ids = _meeting(team_id, 0, [("배너 시안 중 2번이 좋아요.", True)])

    assert _decide(now, "2번 시안으로 정한다.", now_ids) == _decide(
        past, "배너는 세 개를 순환한다.", past_ids
    )


def test_a_terse_change_is_classified_on_the_line_it_was_settled_in(team_id: str) -> None:
    """The fake NLI entails only an identical string: the consented line restates
    the earlier statement word for word, the terse statement does not."""
    past, past_ids = _meeting(team_id, 10, [("홈 화면 배너는 세 개로 하죠.", True)])
    now, now_ids = _meeting(team_id, 0, [("배너는 세 개를 순환한다.", True)])

    past_thread = _decide(past, "배너는 세 개를 순환한다.", past_ids)
    assert _decide(now, "그렇게 정리했다.", now_ids) == past_thread
    assert _change_type(now) == "unchanged"


def test_an_unconsented_line_is_not_what_a_change_is_classified_on(team_id: str) -> None:
    past, past_ids = _meeting(team_id, 10, [("홈 화면 배너는 세 개로 하죠.", True)])
    now, now_ids = _meeting(
        team_id, 0, [("배너는 세 개를 순환한다.", False), ("배너 시안 얘기를 했어요.", True)]
    )

    past_thread = _decide(past, "배너는 세 개를 순환한다.", past_ids)
    assert _decide(now, "그렇게 정리했다.", now_ids) == past_thread
    assert _change_type(now) == "modified"


def test_an_unconsented_utterance_is_not_read(team_id: str) -> None:
    past, past_ids = _meeting(team_id, 10, [("홈 화면 배너는 세 개로 하죠.", True)])
    now, now_ids = _meeting(team_id, 0, [("배너 시안 중 2번이 좋아요.", False)])

    past_thread = _decide(past, "배너는 세 개를 순환한다.", past_ids)
    assert _decide(now, "2번 시안으로 정한다.", now_ids) != past_thread
