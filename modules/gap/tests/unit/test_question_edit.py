"""S20's 해소용 질문 in a member's own words (#824): ``PUT /gaps/{id}/question``.

The routes run on SQLite with the router on a bare app, the harness
``test_read_endpoints`` uses. That a re-run keeps an edited question is
``tests/integration/test_tools.py``'s, beside the carried mark it mirrors.
"""

# ruff: noqa: F401, F811  -- fixtures shared with test_read_endpoints

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_gap import template
from autune_gap.models import GapGap

from .test_read_endpoints import (
    FOREIGN_MEETING,
    MEETING,
    PREFIX,
    anonymous,
    client,
    gap,
    queued,
    session,
    topic,
)


def path(gap_id: str) -> str:
    return f"{PREFIX}/gaps/{gap_id}/question"


def test_a_member_rewrites_the_question_and_e_is_sent_it(
    client: TestClient, session: Session, queued: list[str]
) -> None:
    gap(session, "gap_1")

    response = client.put(path("gap_1"), json={"question": "  배포 일정은   누가 정합니까? "})

    assert response.status_code == 200
    assert response.json() == {
        "gap_id": "gap_1",
        "meeting_id": MEETING,
        "suggested_question": "배포 일정은 누가 정합니까?",
        "edited": True,
    }
    row = session.get(GapGap, "gap_1")
    assert row is not None
    assert row.suggested_question == "배포 일정은 누가 정합니까?"
    assert row.question_edited_at is not None
    assert queued == [MEETING]


def test_the_explanation_says_the_question_was_edited(
    client: TestClient, session: Session, queued: list[str]
) -> None:
    item = template.get_template("general").items[0].key
    topic(session, "top_1")
    gap(session, "gap_1", template_key="general", item_key=item, coverage="missing")
    before = client.get(f"{PREFIX}/explanations/{MEETING}").json()["gaps"][0]

    client.put(path("gap_1"), json={"question": "누가 정합니까?"})

    after = client.get(f"{PREFIX}/explanations/{MEETING}").json()["gaps"][0]
    assert before["question_edited"] is False
    assert after["question_edited"] is True


def test_a_question_holding_personal_data_is_not_saved(
    client: TestClient, session: Session, queued: list[str]
) -> None:
    row = session.get(GapGap, gap(session, "gap_1"))
    assert row is not None
    original = row.suggested_question

    response = client.put(path("gap_1"), json={"question": "010-1234-5678로 연락해 확인할까요?"})

    assert response.status_code == 422
    assert "010" not in response.text
    session.refresh(row)
    assert row.suggested_question == original
    assert row.question_edited_at is None
    assert queued == []


def test_an_empty_or_long_question_is_refused(
    client: TestClient, session: Session, queued: list[str]
) -> None:
    gap(session, "gap_1")

    assert client.put(path("gap_1"), json={"question": ""}).status_code == 422
    assert client.put(path("gap_1"), json={"question": "   "}).status_code == 422
    assert client.put(path("gap_1"), json={"question": "가" * 501}).status_code == 422
    assert queued == []


def test_another_teams_gap_cannot_be_edited(
    client: TestClient, session: Session, queued: list[str]
) -> None:
    gap(session, "gap_other", meeting_id=FOREIGN_MEETING)

    response = client.put(path("gap_other"), json={"question": "누가 정합니까?"})

    assert response.status_code == 404
    assert queued == []


def test_editing_needs_a_caller(anonymous: TestClient, session: Session) -> None:
    gap(session, "gap_1")

    assert anonymous.put(path("gap_1"), json={"question": "누가?"}).status_code == 403
