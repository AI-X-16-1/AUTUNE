"""Building and storing a meeting's topic graph, against a real PostgreSQL.

Runs the ``fake`` extractor (deterministic, no weights), so what it finds is
decided by the text: "검색" is a feature, "캐시" a system, "30%" a metric.

``service`` opens its own sessions through ``session_scope``, so these tests
commit real rows and clean up by deleting the team — which is also the deletion
test's premise: everything here hangs off the meeting.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import delete, func, select

from autune_contracts import (
    PrivacyFlags,
    TranscriptMetadata,
    TranscriptReady,
    TranscriptSource,
)
from autune_contracts import Utterance as UtterancePayload
from autune_core import Meeting, Participant, Team, Utterance, session_scope
from autune_gap import service
from autune_gap.config import get_settings
from autune_gap.models import GapParticipation, GapTopic, GapTopicEdge, GapTopicUtterance
from autune_gap.pipeline import reset_cache


@pytest.fixture(autouse=True)
def _fake_extractor(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AUTUNE_GAP_NER_IMPL", "fake")
    get_settings.cache_clear()
    reset_cache()
    yield
    get_settings.cache_clear()
    reset_cache()


@pytest.fixture
def team_id(db_engine: object) -> Iterator[str]:  # db_engine ensures migrations ran
    with session_scope() as s:
        row = Team(name="gap-test")
        s.add(row)
        s.flush()
        tid = row.id
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))


@dataclass
class Line:
    speaker: str
    text: str


def seed(team_id: str, lines: list[Line], *, declined: frozenset[str] = frozenset()) -> str:
    """A meeting as module A leaves it: participants and masked utterances.

    Returns the meeting id. Utterance ids are ``utt_<meeting>_<n>`` so the
    transcript built from the same lines refers to the same rows.
    """
    with session_scope() as s:
        meeting = Meeting(team_id=team_id, title="회의", status="analyzing")
        s.add(meeting)
        s.flush()
        people = {}
        for name in dict.fromkeys(line.speaker for line in lines):
            person = Participant(
                meeting_id=meeting.id, speaker_label=name, consented=name not in declined
            )
            s.add(person)
            s.flush()
            people[name] = person.id
        for index, line in enumerate(lines):
            s.add(
                Utterance(
                    id=f"utt_{meeting.id}_{index}",
                    meeting_id=meeting.id,
                    participant_id=people[line.speaker],
                    speaker_label=line.speaker,
                    start_sec=float(index),
                    end_sec=float(index) + 1,
                    text=line.text,
                )
            )
        return meeting.id


def transcript(meeting_id: str, lines: list[Line]) -> TranscriptReady:
    return TranscriptReady(
        meeting_id=meeting_id,
        utterances=[
            UtterancePayload(
                id=f"utt_{meeting_id}_{index}",
                speaker=line.speaker,
                start=float(index),
                end=float(index) + 1,
                text=line.text,
                confidence=0.9,
            )
            for index, line in enumerate(lines)
        ],
        metadata=TranscriptMetadata(
            duration=float(len(lines)),
            participants=sorted({line.speaker for line in lines}),
            source=TranscriptSource.FILE_UPLOAD,
            privacy=PrivacyFlags(original_audio_deleted=True, pii_masked=True),
        ),
    )


MEETING = [
    Line("김서연", "검색 기능 응답이 캐시 때문에 느립니다"),
    Line("이건우", "캐시 만료를 30% 줄이면 됩니다"),
    Line("김서연", "검색 결과는 다음 주에 다시 보죠"),
]


def build(team_id: str, lines: list[Line], **seed_options: frozenset[str]) -> str:
    meeting_id = seed(team_id, lines, **seed_options)
    service.build_topic_graph(transcript(meeting_id, lines))
    return meeting_id


def labels(meeting_id: str) -> set[str]:
    with session_scope() as s:
        return set(s.scalars(select(GapTopic.label).where(GapTopic.meeting_id == meeting_id)))


def counts(meeting_id: str) -> dict[str, int]:
    with session_scope() as s:
        topic_ids = select(GapTopic.id).where(GapTopic.meeting_id == meeting_id)
        return {
            "topics": s.scalar(select(func.count()).select_from(topic_ids.subquery())) or 0,
            "evidence": s.scalar(
                select(func.count()).where(GapTopicUtterance.topic_id.in_(topic_ids))
            )
            or 0,
            "edges": s.scalar(select(func.count()).where(GapTopicEdge.meeting_id == meeting_id))
            or 0,
            "participation": s.scalar(
                select(func.count()).where(GapParticipation.topic_id.in_(topic_ids))
            )
            or 0,
        }


# --- what gets stored -------------------------------------------------------


def test_a_meeting_gets_its_topics(team_id: str) -> None:
    meeting_id = build(team_id, MEETING)

    assert labels(meeting_id) == {"검색 기능", "캐시", "30%", "검색", "다음 주"}


def test_topics_named_in_one_utterance_are_joined(team_id: str) -> None:
    meeting_id = build(team_id, MEETING)

    with session_scope() as s:
        label = dict(
            s.execute(
                select(GapTopic.id, GapTopic.label).where(GapTopic.meeting_id == meeting_id)
            ).all()
        )
        pairs = {
            (label[source], label[target])
            for source, target in s.execute(
                select(GapTopicEdge.source_topic_id, GapTopicEdge.target_topic_id).where(
                    GapTopicEdge.meeting_id == meeting_id
                )
            ).tuples()
        }

    assert ("검색 기능", "캐시") in pairs
    assert ("캐시", "검색 기능") in pairs


def test_a_topic_points_at_the_utterances_it_came_from(team_id: str) -> None:
    meeting_id = build(team_id, MEETING)

    with session_scope() as s:
        evidence = list(
            s.execute(
                select(GapTopicUtterance.utterance_id, GapTopicUtterance.position)
                .join(GapTopic, GapTopic.id == GapTopicUtterance.topic_id)
                .where(GapTopic.meeting_id == meeting_id, GapTopic.label == "캐시")
                .order_by(GapTopicUtterance.position)
            ).tuples()
        )

    assert evidence == [(f"utt_{meeting_id}_0", 0), (f"utt_{meeting_id}_1", 1)]


def test_participation_records_who_spoke_on_a_topic(team_id: str) -> None:
    meeting_id = build(team_id, MEETING)

    with session_scope() as s:
        rows = s.execute(
            select(Participant.speaker_label, GapParticipation.spoke)
            .join(GapParticipation, GapParticipation.participant_id == Participant.id)
            .join(GapTopic, GapTopic.id == GapParticipation.topic_id)
            .where(GapTopic.meeting_id == meeting_id, GapTopic.label == "30%")
        ).all()

    assert dict(rows) == {"이건우": True, "김서연": False}


def test_a_topic_names_the_extractor_that_built_it(team_id: str) -> None:
    meeting_id = build(team_id, MEETING)

    with session_scope() as s:
        versions = set(
            s.scalars(select(GapTopic.extractor_version).where(GapTopic.meeting_id == meeting_id))
        )

    assert versions == {"fake"}


# --- consent ----------------------------------------------------------------


def test_speech_of_a_participant_who_declined_is_not_analysed(team_id: str) -> None:
    """privacy.md section 5. "결제" is only ever said by the person who
    declined, so a topic for it could only have come from their speech."""
    lines = [*MEETING, Line("박도윤", "결제 화면도 바꿔야 합니다")]
    meeting_id = build(team_id, lines, declined=frozenset({"박도윤"}))

    assert not any("결제" in label for label in labels(meeting_id))


def test_a_participant_who_declined_is_not_in_the_matrix(team_id: str) -> None:
    lines = [*MEETING, Line("박도윤", "결제 화면도 바꿔야 합니다")]
    meeting_id = build(team_id, lines, declined=frozenset({"박도윤"}))

    with session_scope() as s:
        speakers = set(
            s.scalars(
                select(Participant.speaker_label)
                .join(GapParticipation, GapParticipation.participant_id == Participant.id)
                .join(GapTopic, GapTopic.id == GapParticipation.topic_id)
                .where(GapTopic.meeting_id == meeting_id)
            )
        )

    assert speakers == {"김서연", "이건우"}


# --- idempotency and deletion (docs/engineering/testing.md, required) -------


def test_running_twice_leaves_the_same_graph(team_id: str) -> None:
    meeting_id = seed(team_id, MEETING)
    payload = transcript(meeting_id, MEETING)

    service.build_topic_graph(payload)
    first = (counts(meeting_id), labels(meeting_id))
    service.build_topic_graph(payload)

    assert (counts(meeting_id), labels(meeting_id)) == first


def test_deleting_the_meeting_deletes_the_graph(team_id: str) -> None:
    meeting_id = build(team_id, MEETING)
    assert counts(meeting_id)["participation"] > 0

    with session_scope() as s:
        s.execute(delete(Meeting).where(Meeting.id == meeting_id))

    assert counts(meeting_id) == {"topics": 0, "evidence": 0, "edges": 0, "participation": 0}


def test_a_meeting_module_a_never_wrote_is_refused(team_id: str) -> None:
    with pytest.raises(ValueError, match="meeting row not found"):
        service.build_topic_graph(transcript("mtg_missing", MEETING))
