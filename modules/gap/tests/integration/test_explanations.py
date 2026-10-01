"""Why each gap was raised (``service.explain``), against a real PostgreSQL.

One meeting holds one gap of each basis: ``risk`` rests on a thin topic,
``dependency`` on a keyword said without becoming a topic, ``next_step`` on
nothing. What these pin is what S20 shows beside a verdict: the quote it rests
on, only from consenting speech, and a score breakdown that adds up to the
stored score.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from autune_core import Meeting, Participant, Team, Utterance, session_scope
from autune_gap import service
from autune_gap.models import GapGap, GapParticipation, GapTopic, GapTopicUtterance

STARTED = datetime(2026, 9, 21, 10, tzinfo=UTC)


@pytest.fixture
def team_id(db_engine: object) -> Iterator[str]:  # db_engine ensures migrations ran
    with session_scope() as s:
        row = Team(name="gap-explain-test")
        s.add(row)
        s.flush()
        tid = row.id
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))


def seed(team_id: str) -> dict[str, str]:
    with session_scope() as s:
        meeting = Meeting(
            team_id=team_id, title="알림 발송 기획", status="analyzing", started_at=STARTED
        )
        s.add(meeting)
        s.flush()
        yes = Participant(meeting_id=meeting.id, speaker_label="화자0", consented=True)
        no = Participant(meeting_id=meeting.id, speaker_label="화자1", consented=False)
        s.add_all([yes, no])
        s.flush()

        def say(uid: str, who: Participant, start: float, text: str) -> str:
            s.add(
                Utterance(
                    id=uid,
                    meeting_id=meeting.id,
                    participant_id=who.id,
                    speaker_label=who.speaker_label,
                    start_sec=start,
                    end_sec=start + 2,
                    text=text,
                )
            )
            return uid

        # The declined speaker says a dependency keyword first; it must not be quoted.
        say("utt_declined", no, 5.0, "선행 작업이 필요해요")
        risky = say("utt_risk", yes, 30.0, "리스크는 나중에 봐요")
        blocker = say("utt_blocker", yes, 62.5, "블로커는 없겠죠")
        s.flush()

        for label, centrality, utterances in (
            ("핵심 지표", 1.0, ()),
            ("담당자", 0.9, ()),
            ("리스크", 0.2, (risky,)),
        ):
            topic = GapTopic(
                meeting_id=meeting.id,
                label=label,
                extractor_version="fake",
                centrality=centrality,
                betweenness=0.0,
            )
            s.add(topic)
            s.flush()
            s.add(GapParticipation(topic_id=topic.id, participant_id=yes.id, spoke=True))
            for position, uid in enumerate(utterances):
                s.add(GapTopicUtterance(topic_id=topic.id, utterance_id=uid, position=position))
        meeting_id = meeting.id

    service.detect_gaps(meeting_id)
    return {"meeting": meeting_id, "risk": risky, "blocker": blocker}


def rows(meeting_id: str) -> dict[str, GapGap]:
    """The stored gaps by template item key, detached for reading."""
    with session_scope() as s:
        found = list(s.scalars(select(GapGap).where(GapGap.meeting_id == meeting_id)))
        s.expunge_all()
    return {str(g.template_item_key): g for g in found}


def explained(meeting_id: str) -> dict[str, object]:
    with session_scope() as s:
        result = service.explain(s, meeting_id)
    keys = {g.id: key for key, g in rows(meeting_id).items()}
    return {"result": result, "by_key": {keys[g.gap_id]: g for g in result.gaps}}


def test_a_thin_topic_is_quoted_with_its_time(team_id: str) -> None:
    ids = seed(team_id)
    risk = explained(ids["meeting"])["by_key"]["risk"]  # type: ignore[index]

    assert (risk.coverage, risk.basis, risk.topic_label) == ("partial", "topic", "리스크")
    assert risk.topic_centrality == pytest.approx(0.2)
    assert [(e.utterance_id, e.start_sec, e.text) for e in risk.evidence] == [
        (ids["risk"], 30.0, "리스크는 나중에 봐요")
    ]


def test_a_keyword_said_is_quoted_and_declined_speech_is_not(team_id: str) -> None:
    ids = seed(team_id)
    dependency = explained(ids["meeting"])["by_key"]["dependency"]  # type: ignore[index]

    assert (dependency.coverage, dependency.basis) == ("partial", "keyword")
    assert dependency.matched_keywords == ["블로커"]
    assert [e.utterance_id for e in dependency.evidence] == [ids["blocker"]]


def test_a_missing_item_says_what_it_was_searched_for(team_id: str) -> None:
    ids = seed(team_id)
    next_step = explained(ids["meeting"])["by_key"]["next_step"]  # type: ignore[index]

    assert (next_step.coverage, next_step.basis) == ("missing", "none")
    assert next_step.evidence == []
    assert "다음" in next_step.keywords


def test_every_breakdown_adds_up_to_the_stored_score(team_id: str) -> None:
    ids = seed(team_id)
    result = explained(ids["meeting"])["result"]

    stored = {g.id: g.risk_score for g in rows(ids["meeting"]).values()}
    for gap in result.gaps:  # type: ignore[attr-defined]
        assert gap.breakdown is not None
        assert gap.breakdown.score == stored[gap.gap_id]
        parts = gap.breakdown.parts
        raw = sum(p.weight * p.value for p in parts) / sum(p.weight for p in parts)
        assert raw * (gap.breakdown.damping or 1.0) == pytest.approx(gap.breakdown.score)


def test_the_meeting_is_named_for_the_breadcrumb(team_id: str) -> None:
    ids = seed(team_id)
    result = explained(ids["meeting"])["result"]

    assert result.meeting_title == "알림 발송 기획"  # type: ignore[attr-defined]
    assert result.meeting_date == STARTED  # type: ignore[attr-defined]


def test_a_meeting_with_no_start_is_dated_by_when_it_was_registered(team_id: str) -> None:
    """An uploaded recording has no start time. It is dated the way C orders
    a team's meetings (`tools._previous_analysed`): by when its row was made."""
    with session_scope() as s:
        meeting = Meeting(team_id=team_id, title="업로드한 회의", status="analyzing")
        s.add(meeting)
        s.flush()
        meeting_id, registered = meeting.id, meeting.created_at

    with session_scope() as s:
        result = service.explain(s, meeting_id)

    assert result.meeting_date == registered
    assert result.gaps == []


def test_a_missing_items_question_names_the_meetings_subject(team_id: str) -> None:
    ids = seed(team_id)

    # dependency asks about the subject; next_step is about the meeting itself.
    question = rows(ids["meeting"])["dependency"].suggested_question
    assert question is not None
    assert "핵심 지표" in question


def test_a_refresh_after_detection_changes_no_question(team_id: str) -> None:
    """All three bases at once -- a thin topic, a keyword said, nothing -- so the
    backfill's inputs are the ones detection used in every case."""
    ids = seed(team_id)

    assert service.refresh_questions(ids["meeting"]) == 0
