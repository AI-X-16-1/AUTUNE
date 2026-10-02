"""A person deleting their own speech takes their words out of C (#587).

Seeds the rows directly, as ``test_gap_detection`` does: what is under test is
which rows the hook changes, not how extraction built them. ``service`` opens
its own sessions, so these commit real rows and clean up by deleting the team.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import delete, select

from autune_core import Meeting, Participant, Team, Utterance, session_scope
from autune_core.deletion import registered_speech_modules
from autune_gap import service
from autune_gap.models import GapGap, GapRelatedTopic, GapTopic, GapTopicUtterance
from autune_gap.template import get_template

GENERAL = get_template("general")
ITEM = next(item for item in GENERAL.items if item.key == "success_criteria")


@pytest.fixture
def team_id(db_engine: object) -> Iterator[str]:  # db_engine ensures migrations ran
    with session_scope() as s:
        row = Team(name="gap-speech-deleted-test")
        s.add(row)
        s.flush()
        tid = row.id
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))


@dataclass(frozen=True)
class Seeded:
    meeting_id: str
    mine: str
    theirs: str
    topic_mine_only: str
    topic_shared: str
    gap_on_mine: str
    gap_on_shared: str


def seed(team_id: str) -> Seeded:
    """Two lines, one each. "검색 개인화" was named only in mine; "결제 API" in
    both. One gap per topic, its question naming the topic."""
    with session_scope() as s:
        meeting = Meeting(team_id=team_id, title="회의", status="complete")
        s.add(meeting)
        s.flush()
        me = Participant(meeting_id=meeting.id, speaker_label="화자0", consented=True)
        them = Participant(meeting_id=meeting.id, speaker_label="화자1", consented=True)
        s.add_all([me, them])
        s.flush()

        lines = {}
        for index, (person, text) in enumerate(
            [(me, "검색 개인화와 결제 API 얘기"), (them, "결제 API는 다음 주")]
        ):
            line = Utterance(
                meeting_id=meeting.id,
                participant_id=person.id,
                speaker_label=person.speaker_label,
                start_sec=float(index),
                end_sec=float(index) + 1,
                text=text,
            )
            s.add(line)
            s.flush()
            lines[person.id] = line.id

        def topic(label: str, said_in: list[str]) -> str:
            row = GapTopic(
                meeting_id=meeting.id,
                label=label,
                extractor_version="fake",
                centrality=1.0,
                betweenness=0.0,
            )
            s.add(row)
            s.flush()
            s.add_all(
                GapTopicUtterance(topic_id=row.id, utterance_id=u, position=p)
                for p, u in enumerate(said_in)
            )
            return row.id

        mine_only = topic("검색 개인화", [lines[me.id]])
        shared = topic("결제 API", [lines[me.id], lines[them.id]])

        def gap(item_key: str, topic_id: str, label: str) -> str:
            row = GapGap(
                meeting_id=meeting.id,
                category="success",
                title=f"{ITEM.item} — 충분히 다뤄지지 않았습니다",
                severity="medium",
                risk_score=0.5,
                template_item=ITEM.item,
                template_key=GENERAL.key,
                template_version=GENERAL.version,
                template_item_key=item_key,
                suggested_question=ITEM.question_about.format(topic=label),
                coverage="partial",
            )
            s.add(row)
            s.flush()
            s.add(GapRelatedTopic(gap_id=row.id, topic_id=topic_id))
            return row.id

        on_mine = gap("success_criteria", mine_only, "검색 개인화")
        on_shared = gap("ownership", shared, "결제 API")

        return Seeded(
            meeting_id=meeting.id,
            mine=lines[me.id],
            theirs=lines[them.id],
            topic_mine_only=mine_only,
            topic_shared=shared,
            gap_on_mine=on_mine,
            gap_on_shared=on_shared,
        )


def forget(utterance_ids: list[str]) -> service.SpeechForgotten:
    with session_scope() as s:
        return service.forget_speech(s, utterance_ids)


def topic_ids(meeting_id: str) -> set[str]:
    with session_scope() as s:
        return set(s.scalars(select(GapTopic.id).where(GapTopic.meeting_id == meeting_id)))


def question(gap_id: str) -> str | None:
    with session_scope() as s:
        row = s.get(GapGap, gap_id)
        assert row is not None, "the gap itself must stay"
        return row.suggested_question


def test_a_topic_only_my_speech_named_goes(team_id: str) -> None:
    seeded = seed(team_id)

    done = forget([seeded.mine])

    assert topic_ids(seeded.meeting_id) == {seeded.topic_shared}
    assert done.topics_deleted == 1
    assert done.meetings == (seeded.meeting_id,)


def test_the_gap_stays_and_its_question_stops_naming_the_topic(team_id: str) -> None:
    seeded = seed(team_id)

    done = forget([seeded.mine])

    assert question(seeded.gap_on_mine) == ITEM.question
    assert "검색 개인화" not in (question(seeded.gap_on_mine) or "")
    assert done.questions_reset == 1


def test_a_topic_somebody_else_also_named_stays_with_its_question(team_id: str) -> None:
    seeded = seed(team_id)
    before = question(seeded.gap_on_shared)

    forget([seeded.mine])

    assert seeded.topic_shared in topic_ids(seeded.meeting_id)
    assert question(seeded.gap_on_shared) == before


def test_forgetting_twice_finds_nothing_the_second_time(team_id: str) -> None:
    seeded = seed(team_id)
    forget([seeded.mine])

    again = forget([seeded.mine])

    assert again == service.SpeechForgotten(meetings=(), topics_deleted=0, questions_reset=0)
    assert question(seeded.gap_on_mine) == ITEM.question


def test_no_utterances_is_a_no_op(team_id: str) -> None:
    seeded = seed(team_id)

    forget([])

    assert topic_ids(seeded.meeting_id) == {seeded.topic_mine_only, seeded.topic_shared}


def test_a_template_no_longer_shipped_leaves_no_question(team_id: str) -> None:
    seeded = seed(team_id)
    with session_scope() as s:
        row = s.get(GapGap, seeded.gap_on_mine)
        assert row is not None
        row.template_key = "retired_template"

    forget([seeded.mine])

    assert question(seeded.gap_on_mine) is None


def test_the_hook_is_registered_where_a_router_is_imported() -> None:
    import autune_gap.router  # noqa: F401  -- the API process imports routers only

    assert "gap" in registered_speech_modules()


def test_the_hook_queues_a_republish_of_each_meeting_it_changed(
    team_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = seed(team_id)
    queued: list[str] = []
    monkeypatch.setattr(service, "enqueue_publish_report", queued.append)

    service.forget_deleted_speech("user_x", [seeded.mine])

    assert queued == [seeded.meeting_id]


def test_a_broker_that_is_down_does_not_stop_the_deletion(
    team_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = seed(team_id)

    def down(_: str) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr(service, "enqueue_publish_report", down)

    service.forget_deleted_speech("user_x", [seeded.mine])

    assert topic_ids(seeded.meeting_id) == {seeded.topic_shared}
