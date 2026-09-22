"""Building and storing a meeting's topic graph, against a real PostgreSQL.

Runs the ``fake`` extractor (deterministic, no weights), so what it finds is
decided by the text: "검색" is a feature, "캐시" a system, "30%" a metric.

``service`` opens its own sessions through ``session_scope``, so these tests
commit real rows and clean up by deleting the team — which is also the deletion
test's premise: everything here hangs off the meeting.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select

from autune_contracts import (
    PrivacyFlags,
    TranscriptMetadata,
    TranscriptReady,
    TranscriptSource,
    validate_major_version,
)
from autune_contracts import Utterance as UtterancePayload
from autune_contracts.events import GAP_COMPLETED
from autune_contracts.gap import GapReport
from autune_core import Meeting, Participant, Team, User, Utterance, session_scope
from autune_gap import service, tasks
from autune_gap.config import get_settings
from autune_gap.models import (
    GapGap,
    GapParticipation,
    GapRelatedTopic,
    GapTopic,
    GapTopicEdge,
    GapTopicUtterance,
)
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

BLOCKED = [
    Line("김서연", "검색 기능은 캐시가 안 잡혀 있어서 이번 스프린트엔 무리입니다"),
    Line("이건우", "캐시 얘기는 다음 주에 다시 보죠"),
]
"""A meeting that says out loud what is in the way. ``MEETING`` never does —
"캐시 때문에 느립니다" is a cause with no blocker word in it — so the two
fixtures cover both sides of step 2."""


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


# --- what step 2 asserted, and what nothing asserted -------------------------


def test_a_relation_the_meeting_stated_is_stored_with_its_direction(team_id: str) -> None:
    """ "검색 기능은 캐시가 안 잡혀 있어서 무리입니다" is a blocker offered as a
    reason, and it points one way. The reverse is not stored, and co-occurrence
    does not put it back."""
    meeting_id = build(team_id, BLOCKED)

    with session_scope() as s:
        label = dict(
            s.execute(
                select(GapTopic.id, GapTopic.label).where(GapTopic.meeting_id == meeting_id)
            ).all()
        )
        edges = {
            (label[source], relation, label[target])
            for source, relation, target in s.execute(
                select(
                    GapTopicEdge.source_topic_id,
                    GapTopicEdge.relation,
                    GapTopicEdge.target_topic_id,
                ).where(GapTopicEdge.meeting_id == meeting_id)
            ).tuples()
        }

    assert ("검색 기능", "blocked_by", "캐시") in edges
    assert ("캐시", "blocked_by", "검색 기능") not in edges
    assert ("캐시", "co_occurs", "검색 기능") not in edges


def test_an_edge_says_what_asserted_it_and_co_occurrence_says_nothing(team_id: str) -> None:
    """The NULL is the point: nothing extracted a co-occurrence edge, the two
    topics merely shared an utterance. Gap precision is compared across versions
    of whatever built the graph, and after #32 two different things build it."""
    blocked = build(team_id, BLOCKED)
    plain = build(team_id, MEETING)

    with session_scope() as s:
        versions = {
            relation: version
            for relation, version in s.execute(
                select(GapTopicEdge.relation, GapTopicEdge.extractor_version).where(
                    GapTopicEdge.meeting_id.in_([blocked, plain])
                )
            ).tuples()
        }

    assert versions == {"blocked_by": "rules-1", "co_occurs": None}


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


# --- the report (#36) --------------------------------------------------------
#
# ``publish`` is replaced rather than run: the subscriber is E's task, and this
# module may not import E to register it (invariant 2). What is asserted is what
# C hands to ``publish`` — the event name and a payload E can parse.


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    captured: list[tuple[str, dict]] = []

    def capture(event: str, payload: dict) -> list[str]:
        captured.append((event, payload))
        return ["autune.intelligence.on_gap_completed"]

    monkeypatch.setattr(service, "publish", capture)
    return captured


def published_report(sent: list[tuple[str, dict]]) -> GapReport:
    (_, payload) = sent[-1]
    report = GapReport.model_validate(payload)
    validate_major_version(report)
    return report


def participant_id(meeting_id: str, speaker: str) -> str:
    with session_scope() as s:
        return s.scalars(
            select(Participant.id).where(
                Participant.meeting_id == meeting_id, Participant.speaker_label == speaker
            )
        ).one()


def test_the_report_goes_out_as_the_gap_completed_event(
    team_id: str, sent: list[tuple[str, dict]]
) -> None:
    meeting_id = build(team_id, MEETING)
    service.publish_report(meeting_id)

    assert [event for event, _ in sent] == [GAP_COMPLETED]


def test_what_is_published_is_a_gap_report_e_can_parse(
    team_id: str, sent: list[tuple[str, dict]]
) -> None:
    """testing.md, "Contract conformance": what a module publishes validates
    against the contract it is published as."""
    meeting_id = build(team_id, MEETING)
    service.publish_report(meeting_id)

    assert published_report(sent).meeting_id == meeting_id


def test_the_report_carries_every_topic_most_central_first(
    team_id: str, sent: list[tuple[str, dict]]
) -> None:
    meeting_id = build(team_id, MEETING)
    service.publish_report(meeting_id)
    report = published_report(sent)

    assert {t.label for t in report.topics} == labels(meeting_id)
    centralities = [t.centrality for t in report.topics]
    assert centralities == sorted(centralities, reverse=True)


def test_topics_that_carried_the_meeting_equally_come_in_meeting_order(
    team_id: str, sent: list[tuple[str, dict]]
) -> None:
    """Two topics tie: "검색 기능" (said first) and "30%" (said second) each
    share one utterance with "캐시". Ordered by id instead, a redelivered task
    would publish the same report shuffled."""
    meeting_id = build(team_id, MEETING)
    service.publish_report(meeting_id)
    order = [t.label for t in published_report(sent).topics]

    assert order.index("검색 기능") < order.index("30%")


def test_a_reported_topic_points_at_its_utterances_in_order(
    team_id: str, sent: list[tuple[str, dict]]
) -> None:
    meeting_id = build(team_id, MEETING)
    service.publish_report(meeting_id)

    cache = next(t for t in published_report(sent).topics if t.label == "캐시")
    assert cache.utterance_ids == [f"utt_{meeting_id}_0", f"utt_{meeting_id}_1"]


def test_reported_participation_names_who_spoke_and_who_did_not(
    team_id: str, sent: list[tuple[str, dict]]
) -> None:
    meeting_id = build(team_id, MEETING)
    service.publish_report(meeting_id)
    report = published_report(sent)

    metric = next(t for t in report.topics if t.label == "30%")
    row = next(p for p in report.participation if p.topic_id == metric.id)
    assert row.spoke == [participant_id(meeting_id, "이건우")]
    assert row.silent == [participant_id(meeting_id, "김서연")]


def test_one_person_split_into_two_voices_is_reported_once(
    team_id: str, sent: list[tuple[str, dict]]
) -> None:
    """Review of #164. Diarization split alice into 화자1 and 화자2 and she
    confirmed both. She spoke on "캐시" as 화자1 and not as 화자2 — she spoke on
    it, once, and is silent on nothing she said."""
    lines = [
        Line("화자1", "캐시 만료를 30% 줄이면 됩니다"),
        Line("화자2", "검색 결과는 다음 주에 다시 보죠"),
        Line("화자3", "검색 기능 응답이 느립니다"),
    ]
    meeting_id = seed(team_id, lines)
    with session_scope() as s:
        alice = User(email=f"alice-{meeting_id}@example.com", display_name="alice")
        s.add(alice)
        s.flush()
        alice_id = alice.id
        for person in s.scalars(
            select(Participant).where(
                Participant.meeting_id == meeting_id,
                Participant.speaker_label.in_(["화자1", "화자2"]),
            )
        ):
            person.user_id = alice_id
    try:
        service.build_topic_graph(transcript(meeting_id, lines))
        service.publish_report(meeting_id)
        report = published_report(sent)
    finally:
        with session_scope() as s:
            s.execute(delete(User).where(User.id == alice_id))

    as_alice = min(participant_id(meeting_id, "화자1"), participant_id(meeting_id, "화자2"))
    bob = participant_id(meeting_id, "화자3")
    cache = next(t for t in report.topics if t.label == "캐시")
    row = next(p for p in report.participation if p.topic_id == cache.id)
    assert (row.spoke, row.silent) == ([as_alice], [bob])
    for row in report.participation:
        assert {*row.spoke, *row.silent} == {as_alice, bob}


def test_a_split_person_is_reported_by_a_participant_id_not_their_user_id(
    team_id: str, sent: list[tuple[str, dict]]
) -> None:
    """A user id is the same in every meeting; a report carrying one could be
    joined across meetings into a record of one person's silences."""
    lines = [Line("화자1", "캐시 얘기"), Line("화자2", "검색 얘기")]
    meeting_id = seed(team_id, lines)
    with session_scope() as s:
        alice = User(email=f"alice-{meeting_id}@example.com", display_name="alice")
        s.add(alice)
        s.flush()
        alice_id = alice.id
        for person in s.scalars(select(Participant).where(Participant.meeting_id == meeting_id)):
            person.user_id = alice_id
    try:
        service.build_topic_graph(transcript(meeting_id, lines))
        service.publish_report(meeting_id)
        report = published_report(sent)
    finally:
        with session_scope() as s:
            s.execute(delete(User).where(User.id == alice_id))

    ids = {who for row in report.participation for who in (*row.spoke, *row.silent)}
    assert ids and all(who.startswith("prt_") for who in ids)


def test_somebody_who_withdrew_consent_is_not_in_the_report(
    team_id: str, sent: list[tuple[str, dict]]
) -> None:
    """Review of #164. The graph is built while everyone consents, so
    ``gap_participation`` holds a row for each of them, and those rows outlive
    a withdrawal until the meeting is processed again. ``build_report`` reads
    the stored rows, so it has to drop anybody ``_people`` no longer returns —
    privacy.md section 5."""
    meeting_id = build(team_id, MEETING)
    gone = participant_id(meeting_id, "김서연")
    with session_scope() as s:
        for person in s.scalars(select(Participant).where(Participant.id == gone)):
            person.consented = False

    service.publish_report(meeting_id)
    report = published_report(sent)

    reported = {who for row in report.participation for who in (*row.spoke, *row.silent)}
    assert reported == {participant_id(meeting_id, "이건우")}


def test_the_report_carries_no_utterance_text(team_id: str, sent: list[tuple[str, dict]]) -> None:
    """Topic labels are words the meeting used; whole utterances are not. E
    reads the quotation by id, from ``utterances``, if it needs one."""
    meeting_id = build(team_id, MEETING)
    service.publish_report(meeting_id)

    dumped = json.dumps(sent[-1][1], ensure_ascii=False)
    assert not any(line.text in dumped for line in MEETING)


def test_a_gap_somebody_dismissed_is_not_reported(
    team_id: str, sent: list[tuple[str, dict]]
) -> None:
    meeting_id = build(team_id, MEETING)
    with session_scope() as s:
        topic_id = s.scalars(
            select(GapTopic.id).where(GapTopic.meeting_id == meeting_id, GapTopic.label == "캐시")
        ).one()
        kept, dismissed = (
            GapGap(
                meeting_id=meeting_id,
                category="technical_spec",
                title=title,
                severity="high",
                risk_score=0.8,
                dismissed_at=when,
            )
            for title, when in (("kept", None), ("dismissed", datetime.now(tz=UTC)))
        )
        s.add_all([kept, dismissed])
        s.flush()
        s.add(GapRelatedTopic(gap_id=kept.id, topic_id=topic_id))
        kept_id = kept.id

    service.publish_report(meeting_id)
    gaps = published_report(sent).gaps

    assert [g.id for g in gaps] == [kept_id]
    assert gaps[0].related_topic_ids == [topic_id]


def test_the_transcript_task_builds_the_graph_and_publishes_it(
    team_id: str, sent: list[tuple[str, dict]]
) -> None:
    meeting_id = seed(team_id, MEETING)

    tasks.on_transcript_ready(transcript(meeting_id, MEETING).model_dump(mode="json"))

    assert {t.label for t in published_report(sent).topics} == labels(meeting_id)


def test_a_redelivered_task_publishes_the_same_report_again(
    team_id: str, sent: list[tuple[str, dict]]
) -> None:
    """testing.md, "Idempotency". ``acks_late`` redelivers a task whose worker
    died; the second run rebuilds the graph, so topic ids differ, and what the
    report says about the meeting does not."""
    meeting_id = seed(team_id, MEETING)
    payload = transcript(meeting_id, MEETING).model_dump(mode="json")

    tasks.on_transcript_ready(payload)
    tasks.on_transcript_ready(payload)
    first, second = (GapReport.model_validate(p) for _, p in sent)

    def shape(report: GapReport) -> list[tuple[str, float, int, int]]:
        spoken = {p.topic_id: (len(p.spoke), len(p.silent)) for p in report.participation}
        return [(t.label, t.centrality, *spoken[t.id]) for t in report.topics]

    assert shape(first) == shape(second)


def test_nothing_is_published_for_a_meeting_that_could_not_be_built(
    team_id: str, sent: list[tuple[str, dict]]
) -> None:
    with pytest.raises(ValueError):
        tasks.on_transcript_ready(transcript("mtg_missing", MEETING).model_dump(mode="json"))

    assert sent == []
