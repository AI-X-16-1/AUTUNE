"""The speaker answers their ambiguous agreements on the web (#585, C).

A deployed stack has no receiver for a Slack click, so the DM links to the
meeting's 할 일 tab, where the speaker sees their own open questions and answers
them. Under test: the list holds the reader's own lines only; an answer takes
the DM button's path (a commitment drafts at once and sends the summary job;
another answer takes the draft back); a question never put is put by the
answer; nobody but the speaker can answer, and another team gets a 404.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_core import (
    Base,
    Meeting,
    Participant,
    TeamMember,
    User,
    Utterance,
    current_user,
    get_session,
)
from autune_core.errors import AutuneError
from autune_extraction import service, tasks
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import ExtActionItem, ExtConfirmation
from autune_extraction.router import router

MEETING = "mtg_1"
OTHER_TEAMS = "mtg_other"


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Meeting, User, Participant, Utterance, TeamMember)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id="team_1", title="주간 회의"))
        s.add(Meeting(id=OTHER_TEAMS, team_id="team_2", title="다른 팀"))
        for uid in ("user_kim", "user_lee", "user_out"):
            s.add(User(id=uid, email=f"{uid}@example.com", display_name=uid))
        s.add_all(
            [
                TeamMember(team_id="team_1", user_id="user_kim"),
                TeamMember(team_id="team_1", user_id="user_lee"),
                TeamMember(team_id="team_2", user_id="user_out"),
                Participant(
                    id="par_kim",
                    meeting_id=MEETING,
                    user_id="user_kim",
                    speaker_label="김",
                    consented=True,
                ),
                Participant(
                    id="par_lee",
                    meeting_id=MEETING,
                    user_id="user_lee",
                    speaker_label="이",
                    consented=True,
                ),
            ]
        )
        for n, (uid, who, text, asked) in enumerate(
            [
                ("utt_kim1", "par_kim", "그럼 제가 한번 볼게요", True),
                ("utt_kim2", "par_kim", "네 그렇게 하죠", False),
                ("utt_lee1", "par_lee", "저도 확인해 볼게요", True),
            ]
        ):
            s.add(
                Utterance(
                    id=uid,
                    meeting_id=MEETING,
                    participant_id=who,
                    speaker_label="화자",
                    start_sec=float(n),
                    end_sec=float(n) + 0.5,
                    text=text,
                )
            )
            s.flush()
            s.add(
                ExtConfirmation(
                    utterance_id=uid,
                    meeting_id=MEETING,
                    reason="weak_assent",
                    sent_at=datetime.now(UTC) if asked else None,
                )
            )
        s.commit()
        yield s


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    queued: list[str] = []
    monkeypatch.setattr(tasks.summarise_confirmed_draft, "delay", queued.append)
    return queued


def client(session: Session, user_id: str) -> TestClient:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix="/api/extraction")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: session.get(User, user_id)
    return TestClient(app)


def test_the_list_is_the_readers_own_lines_only(session: Session) -> None:
    mine = client(session, "user_kim").get(
        "/api/extraction/confirmations", params={"meeting_id": MEETING}
    )

    assert mine.status_code == 200
    assert [(c["utterance_id"], c["text"], c["answer"]) for c in mine.json()] == [
        ("utt_kim1", "그럼 제가 한번 볼게요", None),
        ("utt_kim2", "네 그렇게 하죠", None),
    ]
    assert "저도 확인해 볼게요" not in mine.text, "another speaker's line"


def test_another_teams_reader_gets_a_404(session: Session) -> None:
    reply = client(session, "user_out").get(
        "/api/extraction/confirmations", params={"meeting_id": MEETING}
    )

    assert reply.status_code == 404


def test_yes_drafts_at_once_and_sends_the_summary_job(session: Session, jobs: list[str]) -> None:
    reply = client(session, "user_kim").post(
        "/api/extraction/confirmations/utt_kim1", json={"answer": "commitment"}
    )

    assert reply.status_code == 200 and reply.json()["answer"] == "commitment"
    (item,) = session.scalars(select(ExtActionItem)).all()
    assert [s.utterance_id for s in item.sources] == ["utt_kim1"]
    assert jobs == ["utt_kim1"]


def test_a_changed_answer_takes_the_draft_back(session: Session, jobs: list[str]) -> None:
    kim = client(session, "user_kim")
    kim.post("/api/extraction/confirmations/utt_kim1", json={"answer": "commitment"})

    reply = kim.post("/api/extraction/confirmations/utt_kim1", json={"answer": "not_commitment"})

    assert reply.json()["answer"] == "not_commitment"
    assert session.scalars(select(ExtActionItem)).all() == []
    row = session.get(ExtConfirmation, "utt_kim1", populate_existing=True)
    assert row is not None and row.resolved_kind == "concern"  # as the DM's 아닙니다


def test_a_question_never_put_is_put_by_the_answer(session: Session, jobs: list[str]) -> None:
    reply = client(session, "user_kim").post(
        "/api/extraction/confirmations/utt_kim2", json={"answer": "decision"}
    )

    assert reply.status_code == 200
    row = session.get(ExtConfirmation, "utt_kim2", populate_existing=True)
    assert row is not None and row.sent_at is not None and row.resolved_kind == "decision"
    assert jobs == []


@pytest.mark.parametrize("user", ["user_lee", "user_out"])
def test_nobody_but_the_speaker_can_answer(session: Session, jobs: list[str], user: str) -> None:
    reply = client(session, user).post(
        "/api/extraction/confirmations/utt_kim1", json={"answer": "commitment"}
    )

    assert reply.status_code == 404
    assert session.scalars(select(ExtActionItem)).all() == []
    row = session.get(ExtConfirmation, "utt_kim1", populate_existing=True)
    assert row is not None and row.resolved_kind is None


def test_an_answer_outside_the_three_is_refused(session: Session) -> None:
    reply = client(session, "user_kim").post(
        "/api/extraction/confirmations/utt_kim1", json={"answer": "maybe"}
    )

    assert reply.status_code == 422


def test_the_dm_links_to_the_meetings_actions_tab() -> None:
    assert service.answer_url(MEETING).endswith(f"/meetings/{MEETING}/actions")
