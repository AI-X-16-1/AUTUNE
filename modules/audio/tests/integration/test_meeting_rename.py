"""A member renames a meeting, and a title is screened when it is saved
(#1161), against a real database.

What A's owner answered on #1161, one test or more each: a ``PATCH`` of the
title only, for any member of the team (2-1); a title that reads as personal
data refused at save without the value, when a meeting is renamed and when it
is opened alike (2-1, 2-2, #1130); nothing sent and nobody told, so what
already went out keeps the title it had (2-3).

Read ``conftest.py`` for ``db_session``: migrations once per session, and each
test in a transaction that is rolled back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from autune_audio.models import TranscriptionJob
from autune_audio.router import router
from autune_core import AutuneError, Meeting, Team, TeamMember, User, get_session
from autune_core.auth import current_user

TITLE = "고객사 A 주간 회의"
NEW = "고객사 A 3분기 계획"
PHONE = "010-1234-5678"


def person(session: Session, email: str, *, team: str | None = None) -> User:
    user = User(email=email, display_name=email.split("@")[0])
    session.add(user)
    session.flush()
    if team is not None:
        session.add(TeamMember(team_id=team, user_id=user.id))
        session.flush()
    return user


@pytest.fixture
def team(db_session: Session) -> str:
    row = Team(name="고객사 A 프로젝트")
    db_session.add(row)
    db_session.flush()
    return row.id


@pytest.fixture
def member(db_session: Session, team: str) -> User:
    return person(db_session, "member@example.com", team=team)


@pytest.fixture
def client_for(db_session: Session):
    def build(user: User) -> TestClient:
        app = FastAPI()

        @app.exception_handler(AutuneError)
        async def _render(_: Request, exc: AutuneError) -> JSONResponse:
            return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

        app.include_router(router, prefix="/api/audio")
        app.dependency_overrides[get_session] = lambda: db_session
        app.dependency_overrides[current_user] = lambda: user
        return TestClient(app)

    return build


def meeting_of(session: Session, team: str, *, title: str = TITLE, status: str = "complete") -> str:
    row = Meeting(
        team_id=team,
        title=title,
        status=status,
        started_at=datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        expires_at=datetime(2026, 10, 1, 1, 0, tzinfo=UTC) + timedelta(days=90),
    )
    session.add(row)
    session.flush()
    return row.id


def rename(client_for, by: User, meeting: str, title: object = NEW):
    return client_for(by).patch(f"/api/audio/meetings/{meeting}", json={"title": title})


def title_of(session: Session, meeting: str) -> str:
    session.expire_all()
    return session.scalars(sa.select(Meeting.title).where(Meeting.id == meeting)).one()


def row_of(session: Session, meeting: str) -> dict[str, object]:
    session.expire_all()
    row = session.get(Meeting, meeting)
    assert row is not None
    return {column.name: getattr(row, column.name) for column in Meeting.__table__.columns}


# --- a rename -----------------------------------------------------------------


def test_a_member_gives_a_meeting_a_new_title(
    db_session: Session, client_for, member: User, team: str
) -> None:
    meeting = meeting_of(db_session, team)

    response = rename(client_for, member, meeting)

    assert response.status_code == 200
    assert title_of(db_session, meeting) == NEW


def test_any_member_may_and_not_only_the_one_who_opened_it(
    db_session: Session, client_for, member: User, team: str
) -> None:
    """A meeting has no opener on its row; the answer on #1161 is any member."""
    meeting = meeting_of(db_session, team)
    mate = person(db_session, "mate@example.com", team=team)

    assert rename(client_for, mate, meeting).status_code == 200
    assert title_of(db_session, meeting) == NEW


def test_the_answer_does_not_repeat_the_title(
    db_session: Session, client_for, member: User, team: str
) -> None:
    meeting = meeting_of(db_session, team)

    response = rename(client_for, member, meeting)

    assert response.json() == {"meeting_id": meeting, "status": "complete"}


def test_nothing_but_the_title_changes(
    db_session: Session, client_for, member: User, team: str
) -> None:
    meeting = meeting_of(db_session, team)
    before = row_of(db_session, meeting)

    assert rename(client_for, member, meeting).status_code == 200

    after = row_of(db_session, meeting)
    changed = {name for name in before if before[name] != after[name]}
    # ``updated_at`` where the table keeps one; nothing a module reads as state.
    assert changed - {"updated_at"} == {"title"}


def test_space_around_a_title_does_not_count(
    db_session: Session, client_for, member: User, team: str
) -> None:
    meeting = meeting_of(db_session, team)

    assert rename(client_for, member, meeting, f"  {NEW} \n").status_code == 200

    assert title_of(db_session, meeting) == NEW


def test_a_meeting_being_transcribed_can_be_renamed_and_its_job_is_not_touched(
    db_session: Session, client_for, member: User, team: str
) -> None:
    meeting = meeting_of(db_session, team, status="analyzing")
    db_session.add(TranscriptionJob(meeting_id=meeting, status="running"))
    db_session.flush()

    response = rename(client_for, member, meeting)

    assert response.json() == {"meeting_id": meeting, "status": "analyzing"}
    assert title_of(db_session, meeting) == NEW
    assert db_session.scalars(
        sa.select(TranscriptionJob.status).where(TranscriptionJob.meeting_id == meeting)
    ).all() == ["running"]


def test_nothing_is_published_for_a_rename(
    db_session: Session, client_for, member: User, team: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What was already sent keeps its title (2-3): no module is told."""
    told: list[object] = []
    monkeypatch.setattr("autune_audio.router.publish", lambda *a, **k: told.append(a))
    meeting = meeting_of(db_session, team)

    assert rename(client_for, member, meeting).status_code == 200

    assert told == []


def test_the_log_says_who_and_which_meeting_and_neither_title(
    db_session: Session, client_for, member: User, team: str
) -> None:
    meeting = meeting_of(db_session, team)

    with capture_logs() as logs:
        assert rename(client_for, member, meeting).status_code == 200

    (entry,) = [log for log in logs if log["event"] == "audio_meeting_renamed"]
    assert entry == {
        "event": "audio_meeting_renamed",
        "log_level": "info",
        "meeting_id": meeting,
        "team_id": team,
        "user_id": member.id,
    }
    assert TITLE not in str(logs)
    assert NEW not in str(logs)


# --- who ----------------------------------------------------------------------


def test_somebody_who_is_not_on_the_team_is_refused_like_any_reader(
    db_session: Session, client_for, member: User, team: str
) -> None:
    meeting = meeting_of(db_session, team)
    stranger = person(db_session, "stranger@example.com")

    response = rename(client_for, stranger, meeting)

    assert response.status_code == 403
    assert title_of(db_session, meeting) == TITLE


def test_a_stranger_is_refused_before_the_title_is_read(
    db_session: Session, client_for, member: User, team: str
) -> None:
    """The same 403 whatever was sent: a refusal of the title would say the
    meeting exists and that its title differs."""
    meeting = meeting_of(db_session, team)
    stranger = person(db_session, "stranger@example.com")

    for sent in (f"연락처 {PHONE}", "   ", TITLE):
        assert rename(client_for, stranger, meeting, sent).status_code == 403


def test_a_meeting_that_is_not_there_is_not_found(
    db_session: Session, client_for, member: User
) -> None:
    response = rename(client_for, member, "mtg_nothing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# --- what a title may be ------------------------------------------------------


@pytest.mark.parametrize(
    ("sent", "categories"),
    [
        (f"김 대리 {PHONE} 통화", ["phone"]),
        ("kim.minsu@example.com 건 회의", ["email"]),
        (f"연락처 {PHONE}, kim.minsu@example.com", ["phone", "email"]),
    ],
)
def test_a_title_that_reads_as_personal_data_is_refused_without_the_value(
    db_session: Session, client_for, member: User, team: str, sent: str, categories: list[str]
) -> None:
    meeting = meeting_of(db_session, team)

    with capture_logs() as logs:
        response = rename(client_for, member, meeting, sent)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"] == {
        "field": "title",
        "reason": "personal_data",
        "categories": categories,
    }
    assert title_of(db_session, meeting) == TITLE
    for value in (PHONE, "kim.minsu@example.com", sent):
        assert value not in response.text
        assert value not in str(logs)
    (entry,) = [log for log in logs if log["event"] == "audio_meeting_title_refused"]
    assert entry == {
        "event": "audio_meeting_title_refused",
        "log_level": "info",
        "categories": categories,
        "meeting_id": meeting,
        "user_id": member.id,
    }
    assert not [log for log in logs if log["event"] == "audio_meeting_renamed"]


def test_a_name_in_a_title_passes(db_session: Session, client_for, member: User, team: str) -> None:
    """Patterns only: the detector does not read a name, here or at any exit."""
    meeting = meeting_of(db_session, team)

    assert rename(client_for, member, meeting, "김민수 대리와 주간 회의").status_code == 200
    assert title_of(db_session, meeting) == "김민수 대리와 주간 회의"


def test_the_title_a_meeting_already_has_is_not_a_write(
    db_session: Session, client_for, member: User, team: str
) -> None:
    """A meeting titled before this rule keeps its title, and sending it back
    is neither refused nor logged as a rename."""
    old = f"김 대리 {PHONE} 통화"
    meeting = meeting_of(db_session, team, title=old)

    with capture_logs() as logs:
        response = rename(client_for, member, meeting, old)

    assert response.status_code == 200
    assert title_of(db_session, meeting) == old
    assert not [log for log in logs if log["event"].startswith("audio_meeting_")]


def test_such_a_meeting_can_be_given_a_clean_title(
    db_session: Session, client_for, member: User, team: str
) -> None:
    meeting = meeting_of(db_session, team, title=f"김 대리 {PHONE} 통화")

    assert rename(client_for, member, meeting).status_code == 200
    assert title_of(db_session, meeting) == NEW


@pytest.mark.parametrize("sent", ["", " ", "\t\n", "　"])
def test_a_title_of_nothing_or_nothing_but_space_is_refused(
    db_session: Session, client_for, member: User, team: str, sent: str
) -> None:
    meeting = meeting_of(db_session, team)

    response = rename(client_for, member, meeting, sent)

    assert response.status_code == 422
    assert response.json()["error"]["details"] == {"field": "title"}
    assert title_of(db_session, meeting) == TITLE


@pytest.mark.parametrize("sent", ["가" * 401, None, 7])
def test_a_title_the_schema_does_not_take_changes_nothing(
    db_session: Session, client_for, member: User, team: str, sent: object
) -> None:
    meeting = meeting_of(db_session, team)

    assert rename(client_for, member, meeting, sent).status_code == 422
    assert title_of(db_session, meeting) == TITLE


def test_a_title_of_four_hundred_characters_is_taken(
    db_session: Session, client_for, member: User, team: str
) -> None:
    meeting = meeting_of(db_session, team)

    assert rename(client_for, member, meeting, "가" * 400).status_code == 200
    assert title_of(db_session, meeting) == "가" * 400


def test_a_body_with_more_than_a_title_changes_only_the_title(
    db_session: Session, client_for, member: User, team: str
) -> None:
    """The route takes a title. A status or a team sent beside it is not read."""
    meeting = meeting_of(db_session, team)
    other = Team(name="다른 팀")
    db_session.add(other)
    db_session.flush()

    response = client_for(member).patch(
        f"/api/audio/meetings/{meeting}",
        json={"title": NEW, "status": "failed", "team_id": other.id, "pii_masked": True},
    )

    assert response.status_code == 200
    row = row_of(db_session, meeting)
    assert (row["title"], row["status"], row["team_id"]) == (NEW, "complete", team)


# --- the same screen when a meeting is opened ---------------------------------


def open_meeting(client_for, by: User, team: str, title: str):
    return client_for(by).post("/api/audio/meetings", json={"title": title, "team_id": team})


def meetings_in(session: Session, team: str) -> int:
    session.expire_all()
    return (
        session.scalar(
            sa.select(sa.func.count()).select_from(Meeting).where(Meeting.team_id == team)
        )
        or 0
    )


def test_a_meeting_is_not_opened_under_a_title_that_reads_as_personal_data(
    db_session: Session, client_for, member: User, team: str
) -> None:
    sent = f"김 대리 {PHONE} 통화"

    with capture_logs() as logs:
        response = open_meeting(client_for, member, team, sent)

    assert response.status_code == 422
    assert response.json()["error"]["details"] == {
        "field": "title",
        "reason": "personal_data",
        "categories": ["phone"],
    }
    assert meetings_in(db_session, team) == 0
    assert PHONE not in response.text
    assert PHONE not in str(logs)
    (entry,) = [log for log in logs if log["event"] == "audio_meeting_title_refused"]
    assert entry == {
        "event": "audio_meeting_title_refused",
        "log_level": "info",
        "categories": ["phone"],
        "team_id": team,
        "user_id": member.id,
    }
    assert not [log for log in logs if log["event"] == "audio_meeting_created"]


def test_a_meeting_is_opened_under_a_title_with_a_name_in_it(
    db_session: Session, client_for, member: User, team: str
) -> None:
    response = open_meeting(client_for, member, team, "김민수 대리와 주간 회의")

    assert response.status_code == 201
    assert title_of(db_session, response.json()["meeting_id"]) == "김민수 대리와 주간 회의"


def test_a_stranger_opening_a_meeting_is_refused_before_the_title_is_read(
    db_session: Session, client_for, member: User, team: str
) -> None:
    stranger = person(db_session, "stranger@example.com")

    response = open_meeting(client_for, stranger, team, f"김 대리 {PHONE} 통화")

    assert response.status_code == 403
    assert meetings_in(db_session, team) == 0
