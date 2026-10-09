"""Text a person typed is screened before it is stored (#1130).

Every door through which typed text enters module B's store, driven through
its own endpoint on a bare app over SQLite in memory: eight routes (a project
is saved by two) and the ten checks behind them, one for each field of each
save. The rules under test: text the detector reads is refused with a 422
and nothing is stored or changed; the refusal, the log line and the response
carry the field and the categories and never the text; text sent back as it is
stored is not a write, so a row from before the rule stays editable; a name
passes, because the detector reads patterns.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from autune_core import Base, Meeting, Participant, TeamMember, User, Utterance, get_session
from autune_core.errors import AutuneError, ValidationError
from autune_extraction import tasks, tools
from autune_extraction.models import (
    ExtActionItem,
    ExtDecision,
    ExtDecisionReview,
    ExtEditEvent,
    ExtMaterial,
    ExtMeetingNote,
    ExtProject,
)
from autune_extraction.router import router
from autune_extraction.typed_text import TypedPersonalDataError, refuse_personal_data

from .conftest import sign_in

TEAM = "team_1"
MEETING = "mtg_1"
PREFIX = "/api/extraction"
DRIVE = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view"

PHONE = "010-1234-5678"
EMAIL = "minsu.kim@example.com"
WITH_A_PHONE = f"거래처 담당 {PHONE} 로 견적 보내기"
WITH_AN_EMAIL = f"견적은 {EMAIL} 로 보내기"
CLEAN = "거래처 담당에게 견적 보내기"


@pytest.fixture(autouse=True)
def queued(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What a save starts after its response, recorded and not run: each opens
    its own session on the real database. A refused save must start none."""
    calls: list[str] = []
    for name in ("sync_after_confirmation", "sync_decision_after_confirmation"):
        monkeypatch.setattr(tasks, name, lambda ident, name=name: calls.append(name))
    monkeypatch.setattr(tasks, "refresh_project_minutes", lambda ident: calls.append("minutes"))
    return calls


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Meeting, User, TeamMember, Participant, Utterance)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id=TEAM, title="주간 회의"))
        s.add(
            ExtActionItem(
                id="act_1",
                meeting_id=MEETING,
                description="견적 보내기",
                status="todo",
                confidence=0.8,
                origin="model",
            )
        )
        s.add(
            ExtDecision(
                id="dec_1", meeting_id=MEETING, statement="견적은 이번 주에 보낸다", confidence=0.9
            )
        )
        s.flush()
        yield s


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    sign_in(app, session, team_id=TEAM)
    session.commit()
    yield TestClient(app)


def counts(session: Session) -> dict[str, int]:
    models = (
        ExtActionItem,
        ExtDecision,
        ExtDecisionReview,
        ExtEditEvent,
        ExtMeetingNote,
        ExtProject,
        ExtMaterial,
    )
    return {
        m.__tablename__: session.scalar(select(func.count()).select_from(m)) or 0 for m in models
    }


def texts(session: Session) -> str:
    """Every typed-text column of module B's store, in one string."""
    session.expire_all()
    held: list[str | None] = []
    for item in session.scalars(select(ExtActionItem)):
        held += [item.description, item.assignee_label]
    held += list(session.scalars(select(ExtDecision.statement)))
    held += list(session.scalars(select(ExtDecisionReview.statement)))
    held += list(session.scalars(select(ExtMeetingNote.body)))
    for project in session.scalars(select(ExtProject)):
        held += [project.name, project.aliases]
    held += list(session.scalars(select(ExtMaterial.title)))
    return "\n".join(text for text in held if text)


# Each door: how to knock on it with ``text`` in the field under test.
def _create_item(client: TestClient, text: str) -> Any:
    return client.post(f"{PREFIX}/action-items", json={"meeting_id": MEETING, "description": text})


def _create_item_label(client: TestClient, text: str) -> Any:
    return client.post(
        f"{PREFIX}/action-items",
        json={"meeting_id": MEETING, "description": CLEAN, "assignee_label": text},
    )


def _edit_item(client: TestClient, text: str) -> Any:
    return client.patch(f"{PREFIX}/action-items/act_1", json={"description": text})


def _edit_item_label(client: TestClient, text: str) -> Any:
    return client.patch(f"{PREFIX}/action-items/act_1", json={"assignee_label": text})


def _reword_decision(client: TestClient, text: str) -> Any:
    return client.patch(f"{PREFIX}/decisions/dec_1", json={"statement": text})


def _create_decision(client: TestClient, text: str) -> Any:
    return client.post(f"{PREFIX}/decisions", json={"meeting_id": MEETING, "statement": text})


def _memo(client: TestClient, text: str) -> Any:
    return client.put(f"{PREFIX}/summary/{MEETING}/note", json={"body": text})


def _project_name(client: TestClient, text: str) -> Any:
    return client.post(f"{PREFIX}/projects?team_id={TEAM}", json={"name": text})


def _project_alias(client: TestClient, text: str) -> Any:
    return client.post(
        f"{PREFIX}/projects?team_id={TEAM}", json={"name": "견적", "aliases": ["가격", text]}
    )


def _material(client: TestClient, text: str) -> Any:
    return client.post(f"{PREFIX}/materials?team_id={TEAM}", json={"title": text, "link": DRIVE})


DOORS = [
    pytest.param(_create_item, "description", id="a typed item"),
    pytest.param(_create_item_label, "assignee_label", id="a typed item's owner label"),
    pytest.param(_edit_item, "description", id="an edited item"),
    pytest.param(_edit_item_label, "assignee_label", id="an edited owner label"),
    pytest.param(_reword_decision, "statement", id="a reworded decision"),
    pytest.param(_create_decision, "statement", id="a typed decision"),
    pytest.param(_memo, "body", id="the memo"),
    pytest.param(_project_name, "name", id="a project's name"),
    pytest.param(_project_alias, "aliases", id="a project's alias"),
    pytest.param(_material, "title", id="a material's title"),
]


@pytest.mark.parametrize(("knock", "field"), DOORS)
def test_typed_text_the_detector_reads_is_refused_and_nothing_is_stored(
    knock: Any, field: str, client: TestClient, session: Session, queued: list[str]
) -> None:
    before = counts(session)
    held = texts(session)

    with capture_logs() as logs:
        response = knock(client, WITH_A_PHONE)
    session.rollback()  # what ``get_session`` does when a request raises

    assert response.status_code == 422
    assert counts(session) == before, "no row was added"
    assert texts(session) == held, "and none was changed"
    assert queued == [], "nothing is sent anywhere for a save that did not happen"

    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"] == {"field": field, "reason": "personal_data", "categories": ["phone"]}
    # Neither the value nor anything typed around it, anywhere it could be read.
    for said in (response.text, repr(logs)):
        assert PHONE not in said
        assert "거래처" not in said
    (line,) = [entry for entry in logs if entry["event"] == "extraction_typed_text_refused"]
    assert line["field"] == field
    assert line["categories"] == ["phone"]


@pytest.mark.parametrize(("knock", "field"), DOORS)
def test_the_same_door_takes_text_the_detector_does_not_read(
    knock: Any, field: str, client: TestClient, session: Session
) -> None:
    """Without this the test above would pass on a door that refuses everything.
    A name is not a pattern, so it passes -- as it passes the outbound check."""
    response = knock(client, "김민수 팀장 견적 건")

    assert response.status_code in (200, 201), response.text
    assert "김민수 팀장 견적 건" in texts(session)


def test_the_refusal_says_which_kind_of_value_to_take_out(client: TestClient) -> None:
    response = _create_item(client, WITH_AN_EMAIL)

    assert response.status_code == 422
    assert response.json()["error"]["details"]["categories"] == ["email"]
    assert EMAIL not in response.text


def test_a_refused_rewording_does_not_record_the_verdict_sent_with_it(
    client: TestClient, session: Session
) -> None:
    """One request carries both. Half of it stored would confirm a decision
    under wording nobody saw saved."""
    response = client.patch(
        f"{PREFIX}/decisions/dec_1", json={"status": "confirmed", "statement": WITH_A_PHONE}
    )
    session.rollback()

    assert response.status_code == 422
    assert session.get(ExtDecisionReview, "dec_1") is None


def test_a_refused_edit_changes_none_of_the_fields_sent_with_it(
    client: TestClient, session: Session
) -> None:
    response = client.patch(
        f"{PREFIX}/action-items/act_1", json={"description": WITH_A_PHONE, "status": "done"}
    )
    session.rollback()

    assert response.status_code == 422
    item = session.get(ExtActionItem, "act_1")
    assert item is not None
    assert (item.description, item.status) == ("견적 보내기", "todo")
    assert session.scalar(select(func.count()).select_from(ExtEditEvent)) == 0


# --- rows from before the rule -------------------------------------------------


@pytest.fixture
def old_rows(session: Session) -> None:
    """Stored before the rule, with a number in them. Nothing rewrites them."""
    item = session.get(ExtActionItem, "act_1")
    assert item is not None
    item.description = WITH_A_PHONE
    item.assignee_label = f"담당 {PHONE}"
    session.add(
        ExtDecisionReview(
            decision_id="dec_1", meeting_id=MEETING, status="pending", statement=WITH_A_PHONE
        )
    )
    session.add(ExtMeetingNote(meeting_id=MEETING, body=WITH_A_PHONE))
    session.add(ExtProject(id="prj_1", team_id=TEAM, name=f"견적 {PHONE}", aliases=f"문의 {PHONE}"))
    session.commit()


@pytest.mark.usefixtures("old_rows")
def test_an_old_item_can_still_have_its_other_fields_changed(
    client: TestClient, session: Session
) -> None:
    alone = client.patch(f"{PREFIX}/action-items/act_1", json={"status": "in_progress"})
    # A form that sends every field back, the text as it is stored.
    with_text = client.patch(
        f"{PREFIX}/action-items/act_1",
        json={
            "description": WITH_A_PHONE,
            "assignee_label": f"담당 {PHONE}",
            "due_date": "2026-10-20",
        },
    )

    assert (alone.status_code, with_text.status_code) == (200, 200)
    item = session.get(ExtActionItem, "act_1")
    assert item is not None
    assert item.description == WITH_A_PHONE, "nothing already stored is rewritten"
    assert str(item.due_date) == "2026-10-20"


@pytest.mark.usefixtures("old_rows")
def test_an_old_item_edited_into_other_text_that_still_holds_the_value_is_refused(
    client: TestClient, session: Session
) -> None:
    response = _edit_item(client, WITH_A_PHONE + " (오늘)")
    session.rollback()

    assert response.status_code == 422
    item = session.get(ExtActionItem, "act_1")
    assert item is not None and item.description == WITH_A_PHONE


@pytest.mark.usefixtures("old_rows")
def test_an_old_rewording_can_be_confirmed_and_sent_back_as_it_is(
    client: TestClient, session: Session
) -> None:
    verdict = client.patch(f"{PREFIX}/decisions/dec_1", json={"status": "confirmed"})
    same = client.patch(f"{PREFIX}/decisions/dec_1", json={"statement": WITH_A_PHONE})

    assert (verdict.status_code, same.status_code) == (200, 200)
    review = session.get(ExtDecisionReview, "dec_1")
    assert review is not None and review.statement == WITH_A_PHONE


@pytest.mark.usefixtures("old_rows")
def test_an_old_memo_and_an_old_project_can_be_saved_again_as_they_are(
    client: TestClient, session: Session
) -> None:
    memo = _memo(client, WITH_A_PHONE)
    project = client.put(
        f"{PREFIX}/projects/prj_1?team_id={TEAM}",
        json={"name": f"견적 {PHONE}", "aliases": [f"문의 {PHONE}", "가격"]},
    )

    assert (memo.status_code, project.status_code) == (200, 200)
    row = session.get(ExtProject, "prj_1")
    assert row is not None and row.aliases.split("\n") == [f"문의 {PHONE}", "가격"]


@pytest.mark.usefixtures("old_rows")
def test_an_old_project_takes_no_new_alias_with_a_value_in_it(
    client: TestClient, session: Session
) -> None:
    response = client.put(
        f"{PREFIX}/projects/prj_1?team_id={TEAM}",
        json={"name": f"견적 {PHONE}", "aliases": [f"문의 {PHONE}", "010-9999-0000 문의"]},
    )
    session.rollback()

    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "aliases"
    row = session.get(ExtProject, "prj_1")
    assert row is not None and row.aliases == f"문의 {PHONE}"


def test_the_models_own_wording_sent_back_clears_a_rewording_and_is_not_screened(
    client: TestClient, session: Session
) -> None:
    """A transcript line the masker let through is the masker's to fix, not a
    reason to stop a person undoing their own rewording."""
    decision = session.get(ExtDecision, "dec_1")
    assert decision is not None
    decision.statement = WITH_A_PHONE
    session.add(
        ExtDecisionReview(
            decision_id="dec_1", meeting_id=MEETING, status="pending", statement="고친 문장"
        )
    )
    session.commit()

    response = client.patch(f"{PREFIX}/decisions/dec_1", json={"statement": WITH_A_PHONE})

    assert response.status_code == 200
    review = session.get(ExtDecisionReview, "dec_1")
    assert review is not None and review.statement is None


# --- the helper ------------------------------------------------------------------


def test_the_refusal_is_a_validation_error_that_holds_no_text() -> None:
    with pytest.raises(TypedPersonalDataError) as raised:
        refuse_personal_data(WITH_A_PHONE, field="description", meeting_id=MEETING)

    error = raised.value
    assert isinstance(error, ValidationError)
    assert error.status_code == 422
    assert PHONE not in str(error) and PHONE not in repr(error.to_dict())
    assert "거래처" not in str(error) and "거래처" not in repr(error.to_dict())


@pytest.mark.parametrize("text", [None, "", CLEAN])
def test_nothing_to_refuse(text: str | None) -> None:
    refuse_personal_data(text, field="description")


def test_stored_text_is_not_refused_and_anything_else_is() -> None:
    refuse_personal_data(WITH_A_PHONE, field="description", stored=WITH_A_PHONE)
    with pytest.raises(TypedPersonalDataError):
        refuse_personal_data(WITH_A_PHONE, field="description", stored=WITH_A_PHONE + " ")


def test_the_one_sentence_an_agent_tool_writes_through_this_door_is_not_refused() -> None:
    """Follow-up's item is made by ``create_action_item`` after a person approves
    the proposal, and its text is B's own fixed sentence -- no tool carries text
    a person typed. Were that sentence ever to read as personal data, every
    approval would end in a refusal the approver could do nothing about."""
    refuse_personal_data(tools.FOLLOWUP_DESCRIPTION, field="description")
