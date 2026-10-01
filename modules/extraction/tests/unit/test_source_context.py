"""The lines before a source, shown so "다음 주까지 볼게요" says what it is about.

SQLite in memory. The rules under test are which lines count as context (the ones
just before, in spoken order, never the sources themselves or anything later) and
whose lines never do: a speaker who did not consent, an utterance nobody is
recorded as having said, a blank one.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from autune_core import Base, Meeting, Participant, User
from autune_core import Utterance as StoredUtterance
from autune_extraction import service
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemRelated,
    ExtActionItemSource,
    ExtClassification,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionRef,
    ExtDecisionReview,
    ExtDecisionSource,
    ExtEditEvent,
    ExtExternalRef,
)

MEETING = "mtg_1"

TABLES = [
    User.__table__,
    Meeting.__table__,
    Participant.__table__,
    StoredUtterance.__table__,
    ExtClassification.__table__,
    ExtDecision.__table__,
    ExtDecisionSource.__table__,
    ExtDecisionReview.__table__,
    ExtDecisionRef.__table__,
    ExtActionItem.__table__,
    ExtActionItemRelated.__table__,
    ExtActionItemSource.__table__,
    ExtEditEvent.__table__,
    ExtConfirmation.__table__,
    ExtExternalRef.__table__,
]


def say(session: Session, n: int, text: str, *, participant: str | None = "par_yes") -> str:
    uid = f"utt_{n}"
    session.add(
        StoredUtterance(
            id=uid,
            meeting_id=MEETING,
            participant_id=participant,
            speaker_label="김민경",
            start_sec=float(n),
            end_sec=float(n) + 0.5,
            text=text,
        )
    )
    return uid


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as session:
        session.add(Meeting(id=MEETING, team_id="team_1", title="주간 회의"))
        session.add(
            Participant(id="par_yes", meeting_id=MEETING, speaker_label="김민경", consented=True)
        )
        session.add(
            Participant(id="par_no", meeting_id=MEETING, speaker_label="박지영", consented=False)
        )
        session.flush()
        yield session


def texts(lines) -> list[str]:
    return [line.text for line in lines]


def test_the_lines_just_before_the_source_come_back_in_spoken_order(session: Session) -> None:
    for n, text in enumerate(["첫째", "둘째", "셋째", "넷째", "그럼 제가 볼게요", "이후 발화"], 1):
        say(session, n, text)
    session.flush()

    context = service.context_before(session, ["utt_5"])

    assert texts(context) == ["둘째", "셋째", "넷째"]  # three, not the fourth back, not later


def test_several_sources_take_the_lines_before_the_first(session: Session) -> None:
    for n, text in enumerate(["가", "나", "다", "라", "마"], 1):
        say(session, n, text)
    session.flush()

    context = service.context_before(session, ["utt_5", "utt_3"])

    assert texts(context) == ["가", "나"]  # nothing between or after the sources


def test_a_speaker_who_did_not_consent_is_never_context(session: Session) -> None:
    say(session, 1, "동의한 사람의 앞선 말")
    say(session, 2, "동의하지 않은 사람의 말", participant="par_no")
    say(session, 3, "그럼 제가 볼게요")
    session.flush()

    context = service.context_before(session, ["utt_3"])

    assert texts(context) == ["동의한 사람의 앞선 말"]


def test_an_utterance_with_no_participant_or_no_text_is_not_context(session: Session) -> None:
    say(session, 1, "보이는 말")
    say(session, 2, "누구 말인지 모르는 말", participant=None)
    say(session, 3, "   ")
    say(session, 4, "그럼 제가 볼게요")
    session.flush()

    context = service.context_before(session, ["utt_4"])

    assert texts(context) == ["보이는 말"]


def test_no_sources_means_no_context(session: Session) -> None:
    say(session, 1, "아무 말")
    session.flush()

    assert service.context_before(session, []) == []


def test_an_items_detail_carries_its_context_beside_its_sources(session: Session) -> None:
    say(session, 1, "지난주 고객 인터뷰 결과가 아직 정리가 안 됐어요")
    say(session, 2, "그럼 제가 다음 주 화요일까지 볼게요")
    item = ExtActionItem(
        meeting_id=MEETING,
        description="다음 주 화요일까지 볼 예정",
        confidence=0.9,
        origin="model",
        sources=[ExtActionItemSource(utterance_id="utt_2")],
    )
    session.add(item)
    session.flush()

    detail = service.read_detail(session, item)

    assert texts(detail.sources) == ["그럼 제가 다음 주 화요일까지 볼게요"]
    assert texts(detail.context) == ["지난주 고객 인터뷰 결과가 아직 정리가 안 됐어요"]
