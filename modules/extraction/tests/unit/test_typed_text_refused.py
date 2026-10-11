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

import re
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
from autune_integrations.privacy import find_unmasked

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


# --- a value broken over lines (module B's owner, 2026-10-10) --------------------

# Invented values. The detector's shapes stop at a line break -- it was written
# for a transcript, where a break parts two speakers' numbers -- so each of
# these was read as nothing and stored.
HEAD, TAIL = "900101", "1234567"
BROKEN = [
    pytest.param(f"{HEAD}-\n{TAIL}", "rrn", id="LF after the hyphen"),
    pytest.param(f"{HEAD}\n-{TAIL}", "rrn", id="LF before the hyphen"),
    pytest.param(f"{HEAD}-\r\n{TAIL}", "rrn", id="CRLF after the hyphen"),
    pytest.param(f"{HEAD}\r\n-{TAIL}", "rrn", id="CRLF before the hyphen"),
    pytest.param(f"{HEAD}-\r{TAIL}", "rrn", id="a lone CR after the hyphen"),
    pytest.param(f"{HEAD}\r-{TAIL}", "rrn", id="a lone CR before the hyphen"),
    pytest.param(f"{HEAD}-\n\n{TAIL}", "rrn", id="a blank line between"),
    pytest.param(f"{HEAD}-\r\n\r\n{TAIL}", "rrn", id="a blank line between, CRLF"),
    pytest.param(f"{HEAD}-\n    {TAIL}", "rrn", id="a break and an indent"),
    pytest.param(f"주민번호는 {HEAD}-\n{TAIL}입니다", "rrn", id="inside a sentence"),
    pytest.param("123456-\n01-\n234567", "digits", id="an account over three lines"),
    pytest.param("123456-\r\n01-\r\n234567", "digits", id="an account over three lines, CRLF"),
    pytest.param("+82 10-1234-\n5678", "phone", id="a +82 phone before its last group"),
]


@pytest.mark.parametrize(("broken", "category"), BROKEN)
def test_a_value_broken_over_lines_is_refused_in_a_memo(
    broken: str,
    category: str,
    client: TestClient,
    session: Session,
    queued: list[str],
) -> None:
    """The memo is the field a person writes lines in. As typed the detector
    reads none of these -- which is the hole -- and the save is refused."""
    assert find_unmasked(broken) == [], "read as nothing as typed"
    before, held = counts(session), texts(session)

    with capture_logs() as logs:
        response = _memo(client, f"회의 메모\n{broken}\n다음 주에 확인")
    session.rollback()

    assert response.status_code == 422
    assert counts(session) == before, "no row was added"
    assert texts(session) == held, "and none was changed"
    assert queued == []
    details = response.json()["error"]["details"]
    assert (details["field"], details["reason"]) == ("body", "personal_data")
    assert category in details["categories"]
    for said in (response.text, repr(logs)):
        for digits in re.findall(r"\d{4,}", broken):
            assert digits not in said


@pytest.mark.parametrize("split", ["-\n", "-\r\n"], ids=["LF", "CRLF"])
@pytest.mark.parametrize(("knock", "field"), DOORS)
def test_every_door_refuses_a_value_broken_over_two_lines(
    knock: Any, field: str, split: str, client: TestClient, session: Session, queued: list[str]
) -> None:
    before, held = counts(session), texts(session)

    with capture_logs() as logs:
        response = knock(client, f"거래처 담당 {HEAD}{split}{TAIL} 확인")
    session.rollback()

    assert response.status_code == 422
    assert counts(session) == before
    assert texts(session) == held
    assert queued == []
    details = response.json()["error"]["details"]
    assert (details["field"], details["reason"]) == (field, "personal_data")
    assert "rrn" in details["categories"]
    for said in (response.text, repr(logs)):
        assert TAIL not in said
        assert "거래처" not in said
    (line,) = [entry for entry in logs if entry["event"] == "extraction_typed_text_refused"]
    assert line["field"] == field


def test_a_memo_is_stored_with_the_lines_it_was_typed_in(
    client: TestClient, session: Session
) -> None:
    """Folded for the check only. A memo is lines, and stays lines."""
    memo = "회의 메모\n- 예산 150000\n- 인건비 200000\n\n다음 주에 3건 확인"

    response = _memo(client, memo)

    assert response.status_code == 200, response.text
    session.expire_all()
    assert session.scalars(select(ExtMeetingNote.body)).one() == memo


HARMLESS = [
    "예산 150000\n인건비 200000",
    "예산 150000\r\n인건비 200000",
    "- 참석 12\n- 불참 3\n- 안건 5",
    "1. 로그 정리\n2. 배포 연기\n3. 10월 12일 마감",
    "마감 2026-10-12\n3건 남음",
    "2026-10-02\n2026-10-05",
    "2026-10-02-\n2026-10-05",
    "2026-10-12\n14:30",
    "2024-\n2025 계획",
    "10월 12일\n10월 15일",
    "9-\n18시",
    "14:30\n20명",
    "v1.2.3\n4.5.6",
    "배포 -\n10월 12일",
    "내선 1234\n5678",
    "1234-\n5678",
    "1,200,000\n1,500,000",
    "1234567\n1234567",
    "주문 20261012-\n000123",
    "AUT-1234\nAUT-5678",
    "#1195\n#1196\n#1220",
    "달성 85%\n목표 90%",
]


@pytest.mark.parametrize("memo", HARMLESS)
def test_numbers_on_neighbouring_lines_of_a_memo_are_still_saved(
    memo: str, client: TestClient, session: Session
) -> None:
    """Without this the tests above would pass on a fold that refuses every
    memo with two numbers in it. A number with a word on its own side of the
    break joins nothing; nor does a date, a time, a count or a short pair."""
    response = _memo(client, memo)

    assert response.status_code == 200, response.text
    session.expire_all()
    assert session.scalars(select(ExtMeetingNote.body)).one() == memo, "as typed, breaks and all"


@pytest.mark.parametrize(
    ("memo", "category"),
    [
        pytest.param("150000\r\n200000", "rrn", id="six and six digits, CRLF"),
        pytest.param("150000\n\n200000", "rrn", id="six and six digits, a blank line"),
        pytest.param("매출 150000\r\n200000 목표", "rrn", id="six and six, words outside, CRLF"),
        pytest.param("1200000-\n1500000", "digits", id="a hyphenated range of long numbers"),
    ],
)
def test_what_the_fold_newly_refuses_that_is_nobodys_number(
    memo: str, category: str, client: TestClient
) -> None:
    """The cost, written down: two numbers that meet across the break and make
    a shape together. Each was saved until now. "A sentence the detector reads
    wrongly cannot be saved; that cost is accepted" (privacy.md, #1130)."""
    assert find_unmasked(memo) == [], "saved until now"

    response = _memo(client, memo)

    assert response.status_code == 422
    assert category in response.json()["error"]["details"]["categories"]


def test_two_numbers_one_line_feed_apart_were_refused_before_and_name_both_readings(
    client: TestClient,
) -> None:
    """Not new: the detector's account shape already crosses one line break.
    The refusal lists what each reading found, the typed one first."""
    memo = "150000\n200000"
    assert find_unmasked(memo) == ["account"]

    response = _memo(client, memo)

    assert response.status_code == 422
    assert response.json()["error"]["details"]["categories"] == ["account", "rrn"]


@pytest.mark.parametrize(
    "missed",
    [
        pytest.param("8512\n25-1234567", id="a break inside a group of digits"),
        pytest.param(f"{HEAD}-\n-{TAIL}", id="a hyphen on both sides of the break"),
        pytest.param("minsu.kim@\nexample.com", id="an email address broken at its @"),
    ],
)
def test_what_the_fold_does_not_catch_is_still_saved(missed: str, client: TestClient) -> None:
    """Known and not closed here: a fold puts a space where the break was and
    does not guess which breaks to close up. Pinned so that a change to any of
    them -- in the detector, which is not module B's -- is seen."""
    response = _memo(client, missed)

    assert response.status_code == 200, response.text


def test_a_broken_value_sent_back_as_it_is_stored_is_not_a_write() -> None:
    """The rule for a row from before this one holds for it too."""
    old = f"{HEAD}-\n{TAIL}"
    refuse_personal_data(old, field="body", stored=old)
    with pytest.raises(TypedPersonalDataError):
        refuse_personal_data(old + " 확인", field="body", stored=old)
