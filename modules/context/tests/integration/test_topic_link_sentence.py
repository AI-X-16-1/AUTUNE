"""The sentence shown beside a topic link's label (``service.topic_link_sentences``)
is read from the meeting's transcript on every request: only a consenting
speaker's line, only one still there, and never from a copy of its own.

Rows are seeded directly, as ``test_topic_link_consent.py`` seeds them, so a
segment can be built from several utterances by different speakers. The routes
are called as plain functions, as ``test_read_api.py`` calls them.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, update

from autune_context import router, service
from autune_context.constants import EMBEDDING_DIM
from autune_context.models import CtxEmbedding, CtxTopicLink
from autune_context.schemas import LinkConfirmRequest
from autune_core import Meeting, Participant, Team, TeamMember, User, Utterance, session_scope
from autune_core.ids import new_id

_LABEL = "결제 모듈 연동"
_SENTENCE = "결제 모듈 연동은 수요일까지 끝내야 해요."


@pytest.fixture
def team_id(db_engine: object) -> Iterator[str]:  # db_engine ensures migrations ran
    with session_scope() as s:
        row = Team(name="topic-link-sentence-test")
        s.add(row)
        s.flush()
        tid = row.id
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))


@pytest.fixture
def member(team_id: str) -> Iterator[User]:
    with session_scope() as s:
        user = User(email=f"member-{team_id}@sentence.test", display_name="문장 테스트")
        s.add(user)
        s.flush()
        s.add(TeamMember(team_id=team_id, user_id=user.id))
        s.flush()
        s.expunge(user)
    yield user
    with session_scope() as s:
        s.execute(delete(User).where(User.id == user.id))


@pytest.fixture
def meeting(team_id: str) -> str:
    with session_scope() as s:
        row = Meeting(team_id=team_id, title="회의", status="analyzing", started_at=_now())
        s.add(row)
        s.flush()
        return row.id


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _segment(meeting_id: str, label: str, lines: list[tuple[str, bool]]) -> list[str]:
    """A topic segment of ``lines`` -- (text, whether its speaker consents), one
    speaker each, in order -- stored as topic linking stores it. Returns the
    speakers' participant ids."""
    speakers: list[str] = []
    utterance_ids: list[str] = []
    with session_scope() as s:
        for i, (text, consented) in enumerate(lines):
            speaker = Participant(
                meeting_id=meeting_id, speaker_label=new_id("spk"), consented=consented
            )
            s.add(speaker)
            s.flush()
            utterance = Utterance(
                meeting_id=meeting_id,
                participant_id=speaker.id,
                speaker_label=speaker.speaker_label,
                start_sec=float(i),
                end_sec=float(i) + 1,
                text=text,
            )
            s.add(utterance)
            s.flush()
            speakers.append(speaker.id)
            utterance_ids.append(utterance.id)
        s.add(
            CtxEmbedding(
                meeting_id=meeting_id,
                kind="topic",
                ref_label=label,
                utterance_ids=utterance_ids,
                embedding=[1.0] + [0.0] * (EMBEDDING_DIM - 1),
                model_version="test",
            )
        )
    return speakers


def _link(meeting_id: str, label: str, *, status: str = "asserted") -> int:
    with session_scope() as s:
        team_id = s.get(Meeting, meeting_id).team_id
        past = Meeting(team_id=team_id, title="지난 회의", status="complete", started_at=_now())
        s.add(past)
        s.flush()
        link = CtxTopicLink(
            meeting_id=meeting_id,
            topic_label=label,
            linked_meeting_id=past.id,
            linked_meeting_date=date.today(),
            similarity=0.8,
            rerank_score=0.8,
            confidence=0.8,
            status=status,
            retriever_version="test",
            reranker_version="test",
        )
        s.add(link)
        s.flush()
        return link.id


def _sentences(meeting_id: str, *labels: str) -> dict[str, str]:
    with session_scope() as s:
        return service.topic_link_sentences(s, meeting_id, labels)


def test_the_links_route_shows_the_sentence_that_says_each_label(
    meeting: str, member: User
) -> None:
    _segment(meeting, _LABEL, [("일정부터 볼게요.", True), (f"음, {_SENTENCE}", True)])
    _link(meeting, _LABEL)
    _segment(meeting, "배포 일정", [("배포 일정은 아직이에요.", True)])
    _link(meeting, "배포 일정", status="pending")

    with session_scope() as s:
        links = router.get_links(meeting, s, member)

    assert [(link.topic_label, link.topic_sentence) for link in links.asserted] == [
        (_LABEL, _SENTENCE)
    ]
    assert [(link.topic_label, link.topic_sentence) for link in links.pending] == [
        ("배포 일정", "배포 일정은 아직이에요.")
    ]


def test_a_confirmed_link_comes_back_with_its_sentence(meeting: str, member: User) -> None:
    """The context tab moves the link the confirm route returns into the settled
    list, so that one carries the sentence too."""
    _segment(meeting, _LABEL, [(_SENTENCE, True)])
    link_id = _link(meeting, _LABEL, status="pending")

    with session_scope() as s:
        link = router.confirm_link(link_id, LinkConfirmRequest(status="confirmed"), s, member)

    assert (link.status, link.topic_sentence) == ("confirmed", _SENTENCE)


def test_a_label_no_sentence_says_is_shown_alone(meeting: str, member: User) -> None:
    _segment(meeting, _LABEL, [("결제 쪽은 모듈부터 연동하죠.", True)])
    _link(meeting, _LABEL)

    with session_scope() as s:
        links = router.get_links(meeting, s, member)

    assert [(link.topic_label, link.topic_sentence) for link in links.asserted] == [(_LABEL, None)]


def test_a_line_of_a_speaker_who_does_not_consent_is_never_read(meeting: str) -> None:
    """Such a segment's link is hidden already (``_readable_topic_links``); the
    sentence draws the same line on its own as well."""
    _segment(meeting, _LABEL, [(_SENTENCE, False), ("결제 모듈 연동 테스트는 누가 맡죠?", True)])

    assert _sentences(meeting, _LABEL) == {_LABEL: "결제 모듈 연동 테스트는 누가 맡죠?"}


def test_a_withdrawal_takes_the_sentence_away_with_nothing_stored(meeting: str) -> None:
    speaker, _ = _segment(meeting, _LABEL, [(_SENTENCE, True), ("다른 얘기예요.", True)])
    assert _sentences(meeting, _LABEL) == {_LABEL: _SENTENCE}

    with session_scope() as s:
        s.execute(update(Participant).where(Participant.id == speaker).values(consented=False))

    assert _sentences(meeting, _LABEL) == {}


def test_a_deleted_line_is_not_read(meeting: str) -> None:
    _segment(meeting, _LABEL, [(_SENTENCE, True)])
    with session_scope() as s:
        s.execute(delete(Utterance).where(Utterance.meeting_id == meeting))

    assert _sentences(meeting, _LABEL) == {}


def test_a_segment_with_no_utterance_ids_has_no_sentence(meeting: str) -> None:
    _segment(meeting, _LABEL, [(_SENTENCE, True)])
    with session_scope() as s:
        s.execute(
            update(CtxEmbedding)
            .where(CtxEmbedding.meeting_id == meeting)
            .values(utterance_ids=None)
        )

    assert _sentences(meeting, _LABEL) == {}


def test_only_the_labels_asked_for_are_read(meeting: str) -> None:
    _segment(meeting, _LABEL, [(_SENTENCE, True)])
    _segment(meeting, "배포 일정", [("배포 일정은 아직이에요.", True)])

    assert _sentences(meeting, _LABEL) == {_LABEL: _SENTENCE}
    assert _sentences(meeting) == {}


def test_of_two_segments_with_one_label_the_first_with_a_sentence_is_read(meeting: str) -> None:
    _segment(meeting, _LABEL, [("다른 얘기예요.", True)])
    _segment(meeting, _LABEL, [(_SENTENCE, True)])
    _segment(meeting, _LABEL, [("결제 모듈 연동 테스트는 누가 맡죠?", True)])

    assert _sentences(meeting, _LABEL) == {_LABEL: _SENTENCE}
