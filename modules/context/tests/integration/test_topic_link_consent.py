"""A topic link's label is shown only while the speech behind it is consented to
(#474), at every place a label leaves D: the context tab and the agent tool
(``get_topic_links``), what E receives (``_build_context_links``) and the Slack
notice (``collect_topic_link_notices``).

``ctx_topic_links`` has no ``utterance_ids``; a link is read against the
``ctx_embeddings`` row with the same label. Rows are seeded directly, so a row
written before #439 (cut from a speaker who never consented) and one written
before #397 (no ``utterance_ids``) can be built as they are stored.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, update

from autune_context import service
from autune_context.constants import EMBEDDING_DIM
from autune_context.models import CtxEmbedding, CtxMeetingStatus, CtxTopicLink
from autune_core import Meeting, Participant, Team, Utterance, session_scope

_LABEL = "검색 정렬"
_OTHER = "배포 일정"


@pytest.fixture
def team_id(db_engine: object) -> Iterator[str]:  # db_engine ensures migrations ran
    with session_scope() as s:
        row = Team(name="topic-link-consent-test")
        s.add(row)
        s.flush()
        tid = row.id
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))


@pytest.fixture
def meeting(team_id: str) -> str:
    with session_scope() as s:
        row = Meeting(team_id=team_id, title="회의", status="analyzing", started_at=_now())
        s.add(row)
        s.flush()
        return row.id


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _segment(
    meeting_id: str,
    label: str,
    *,
    consented: bool = True,
    stored_ids: bool = True,
) -> str:
    """A topic segment cut from one utterance of a speaker whose consent is
    ``consented``, stored as topic linking stores it; with ``stored_ids=False``
    it carries no ``utterance_ids``, as a row from before #397 does. Returns the
    speaker's participant id, to change their consent afterwards."""
    with session_scope() as s:
        speaker = Participant(
            meeting_id=meeting_id, speaker_label=f"화자-{label}-{consented}", consented=consented
        )
        s.add(speaker)
        s.flush()
        utterance = Utterance(
            meeting_id=meeting_id,
            participant_id=speaker.id,
            speaker_label=speaker.speaker_label,
            start_sec=0.0,
            end_sec=1.0,
            text=f"{label} 이야기를 했다",
        )
        s.add(utterance)
        s.flush()
        s.add(
            CtxEmbedding(
                meeting_id=meeting_id,
                kind="topic",
                ref_label=label,
                utterance_ids=[utterance.id] if stored_ids else None,
                embedding=[1.0] + [0.0] * (EMBEDDING_DIM - 1),
                model_version="test",
            )
        )
        return speaker.id


def _link(meeting_id: str, label: str, *, status: str = "asserted") -> None:
    """A link from ``meeting_id`` to an earlier meeting of its team, which is what
    ``ContextLinks`` carries."""
    with session_scope() as s:
        team_id = s.get(Meeting, meeting_id).team_id
        past = Meeting(team_id=team_id, title="지난 회의", status="complete", started_at=_now())
        s.add(past)
        s.flush()
        s.add(
            CtxTopicLink(
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
        )


def _withdraw(participant_id: str) -> None:
    with session_scope() as s:
        s.execute(
            update(Participant).where(Participant.id == participant_id).values(consented=False)
        )


def _read_api_labels(meeting_id: str) -> set[str]:
    with session_scope() as s:
        asserted, pending = service.get_topic_links(s, meeting_id)
        return {link.topic_label for link in [*asserted, *pending]}


def _published_labels(meeting_id: str) -> set[str]:
    with session_scope() as s:
        status = CtxMeetingStatus(meeting_id=meeting_id, topic_linking_done=True)
        links = service._build_context_links(s, meeting_id, status)
        return {link.topic_label for link in links.topic_links}


def _noticed_labels(meeting_id: str) -> set[str]:
    with session_scope() as s:
        return {n.topic_label for n in service.collect_topic_link_notices(s, meeting_id)}


Reader = Callable[[str], set[str]]
_READERS = [_read_api_labels, _published_labels, _noticed_labels]


@pytest.mark.parametrize("read", _READERS)
def test_a_link_cut_from_a_consenting_speaker_is_shown(meeting: str, read: Reader) -> None:
    _segment(meeting, _LABEL)
    _link(meeting, _LABEL)

    assert read(meeting) == {_LABEL}


@pytest.mark.parametrize("read", _READERS)
def test_a_link_cut_from_a_speaker_who_never_consented_is_hidden(
    meeting: str, read: Reader
) -> None:
    """What a row from before #439 can be: a label made of speech nobody agreed
    to have analysed, beside one that is fine."""
    _segment(meeting, _LABEL, consented=False)
    _link(meeting, _LABEL)
    _segment(meeting, _OTHER)
    _link(meeting, _OTHER)

    assert read(meeting) == {_OTHER}


@pytest.mark.parametrize("read", _READERS)
def test_a_withdrawal_hides_the_link_without_re_deriving_the_meeting(
    meeting: str, read: Reader
) -> None:
    speaker = _segment(meeting, _LABEL)
    _link(meeting, _LABEL)
    assert read(meeting) == {_LABEL}

    _withdraw(speaker)

    assert read(meeting) == set()


@pytest.mark.parametrize("read", _READERS)
def test_a_label_that_cannot_be_checked_is_hidden(meeting: str, read: Reader) -> None:
    """No ``utterance_ids`` (stored before #397), or no segment row at all:
    unknown is not yes."""
    _segment(meeting, _LABEL, stored_ids=False)
    _link(meeting, _LABEL)
    _link(meeting, _OTHER)

    assert read(meeting) == set()


@pytest.mark.parametrize("read", _READERS)
def test_a_deleted_utterance_hides_the_label_cut_from_it(meeting: str, read: Reader) -> None:
    _segment(meeting, _LABEL)
    _link(meeting, _LABEL)
    with session_scope() as s:
        s.execute(delete(Utterance).where(Utterance.meeting_id == meeting))

    assert read(meeting) == set()


@pytest.mark.parametrize("read", _READERS)
def test_a_label_shared_by_two_segments_needs_both_to_pass(meeting: str, read: Reader) -> None:
    """The link does not say which segment it was cut from."""
    _segment(meeting, _LABEL)
    refuser = _segment(meeting, _LABEL, consented=False)
    _link(meeting, _LABEL)
    assert read(meeting) == set()

    with session_scope() as s:
        s.execute(update(Participant).where(Participant.id == refuser).values(consented=True))

    assert read(meeting) == {_LABEL}


def test_a_pending_link_is_hidden_as_well(meeting: str) -> None:
    _segment(meeting, _LABEL, consented=False)
    _link(meeting, _LABEL, status="pending")

    with session_scope() as s:
        asserted, pending = service.get_topic_links(s, meeting)

    assert (asserted, pending) == ([], [])


def test_another_meetings_consent_does_not_open_a_label(team_id: str, meeting: str) -> None:
    """Provenance is this meeting's own: a consenting speaker of another meeting
    that happens to share the label does not make this one's readable."""
    _segment(meeting, _LABEL, consented=False)
    _link(meeting, _LABEL)
    with session_scope() as s:
        other = Meeting(team_id=team_id, title="다른 회의", status="analyzing", started_at=_now())
        s.add(other)
        s.flush()
        other_id = other.id
    _segment(other_id, _LABEL)

    assert _read_api_labels(meeting) == set()
