"""After the upload, a count and a link to each person -- once each, and never the text."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from autune_agent.live import notice
from autune_agent.live.notice import notify_participants
from autune_agent.models import AgentLiveResearch, AgentLiveResearchNotice
from autune_core import Participant, TeamMember, User


class FakeSlack:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_dm(self, user_id: str, text: str, blocks: Any = None) -> str:
        self.sent.append((user_id, text))
        return "ts"


def _done(
    session: Session, team: dict[str, str], n: int = 2, requested_by: str | None = None
) -> None:
    for i in range(n):
        session.add(
            AgentLiveResearch(
                team_id=team["team"],
                meeting_id=team["meeting"],
                requested_by=requested_by,
                origin="auto",
                status="done",
                question=f"q{i}",
                body="비밀 본문",
                web_sources=[],
                meeting_sources=[],
            )
        )
    session.add(
        AgentLiveResearch(
            team_id=team["team"],
            meeting_id=team["meeting"],
            origin="auto",
            status="failed",
            question="실패",
            web_sources=[],
            meeting_sources=[],
        )
    )
    session.commit()


def _participant(session: Session, team: dict[str, str], user_id: str | None, label: str) -> None:
    session.add(Participant(meeting_id=team["meeting"], user_id=user_id, speaker_label=label))
    session.commit()


def test_each_participant_gets_a_count_and_a_link(session: Session, team: dict[str, str]) -> None:
    _done(session, team)
    _participant(session, team, team["member"], "팀원")
    _participant(session, team, None, "Speaker 2")
    slack = FakeSlack()

    sent = notify_participants(session, team["meeting"], slack=slack)

    assert sent == [team["member"]]
    user, text = slack.sent[0]
    assert user == team["member"]
    assert text.startswith("회의 중 조사 문서 2건이 준비됐습니다. ")
    assert text.endswith(f"/meetings/{team['meeting']}#live-research")
    assert "비밀 본문" not in text and "q0" not in text


def test_a_second_transcript_ready_sends_nothing(session: Session, team: dict[str, str]) -> None:
    _done(session, team)
    _participant(session, team, team["member"], "팀원")
    slack = FakeSlack()

    notify_participants(session, team["meeting"], slack=slack)
    notify_participants(session, team["meeting"], slack=slack)

    assert len(slack.sent) == 1
    assert session.get(AgentLiveResearchNotice, (team["meeting"], team["member"])) is not None


def test_no_done_document_sends_nothing(session: Session, team: dict[str, str]) -> None:
    _participant(session, team, team["member"], "팀원")
    slack = FakeSlack()

    assert notify_participants(session, team["meeting"], slack=slack) == []
    assert slack.sent == []


def test_a_participant_who_left_the_team_is_not_messaged(
    session: Session, team: dict[str, str]
) -> None:
    _done(session, team)
    _participant(session, team, team["outsider"], "외부")
    slack = FakeSlack()

    assert notify_participants(session, team["meeting"], slack=slack) == []


def test_one_slack_failure_does_not_stop_the_others(session: Session, team: dict[str, str]) -> None:
    other = User(email="other@example.com", display_name="다른 팀원")
    session.add(other)
    session.flush()
    session.add(TeamMember(team_id=team["team"], user_id=other.id))
    session.commit()
    _done(session, team)
    _participant(session, team, team["member"], "팀원")
    _participant(session, team, other.id, "다른 팀원")

    class Flaky(FakeSlack):
        def send_dm(self, user_id: str, text: str, blocks: Any = None) -> str:
            if user_id == team["member"]:
                raise RuntimeError("not linked")
            return super().send_dm(user_id, text)

    assert notify_participants(session, team["meeting"], slack=Flaky()) == [other.id]


def _member(session: Session, team: dict[str, str], email: str) -> str:
    user = User(email=email, display_name=email)
    session.add(user)
    session.flush()
    session.add(TeamMember(team_id=team["team"], user_id=user.id))
    session.commit()
    return user.id


def test_the_person_who_ran_the_session_hears_before_anyone_is_identified(
    session: Session, team: dict[str, str]
) -> None:
    _done(session, team, requested_by=team["member"])
    _participant(session, team, None, "Speaker 1")
    _participant(session, team, None, "Speaker 2")
    slack = FakeSlack()

    assert notify_participants(session, team["meeting"], slack=slack) == [team["member"]]
    assert notify_participants(session, team["meeting"], slack=slack) == []
    assert [user for user, _ in slack.sent] == [team["member"]]


def test_a_later_transcript_ready_reaches_only_people_identified_since(
    session: Session, team: dict[str, str]
) -> None:
    _done(session, team, requested_by=team["member"])
    _participant(session, team, None, "Speaker 1")
    slack = FakeSlack()
    notify_participants(session, team["meeting"], slack=slack)

    other = _member(session, team, "other@example.com")
    speaker = session.scalars(
        select(Participant).where(Participant.meeting_id == team["meeting"])
    ).one()
    speaker.user_id = other
    session.commit()

    assert notify_participants(session, team["meeting"], slack=slack) == [other]
    assert [user for user, _ in slack.sent] == [team["member"], other]


def test_a_requester_who_left_the_team_is_not_messaged(
    session: Session, team: dict[str, str]
) -> None:
    _done(session, team, requested_by=team["member"])
    session.execute(delete(TeamMember).where(TeamMember.user_id == team["member"]))
    session.commit()
    slack = FakeSlack()

    assert notify_participants(session, team["meeting"], slack=slack) == []
    assert slack.sent == []


def test_a_team_without_slack_records_nothing(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # team_integrations is JSONB, so the SQLite suite answers "never connected" here.
    monkeypatch.setattr(notice, "load_integration", lambda *args: None)
    _done(session, team, requested_by=team["member"])
    _participant(session, team, team["member"], "팀원")

    assert notify_participants(session, team["meeting"]) == []
    assert session.scalars(select(AgentLiveResearchNotice)).all() == []
