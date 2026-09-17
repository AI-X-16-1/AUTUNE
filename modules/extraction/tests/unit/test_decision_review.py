"""Nothing about a decision leaves before a person confirms it (#246).

SQLite in memory and the router on a bare app, as ``test_read_endpoints`` does.
The rules under test: a decision starts pending, a verdict and a rewording are
kept, a rebuild keeps a review only for the same decision, and the outbound list
carries exactly what was confirmed.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_contracts.enums import UtteranceKind
from autune_core import AutuneError, Base, Meeting, Utterance, get_session
from autune_extraction import service
from autune_extraction.config import ExtractionSettings
from autune_extraction.confirmations import WEAK_ASSENT
from autune_extraction.decisions import ClassifiedUtterance
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtClassification,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionReview,
    ExtDecisionSource,
    ExtEditEvent,
)
from autune_extraction.router import router

MEETING = "mtg_1"
PREFIX = "/api/extraction"
K = UtteranceKind

TABLES = [
    Meeting.__table__,
    Utterance.__table__,
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtClassification.__table__,
    ExtDecision.__table__,
    ExtDecisionSource.__table__,
    ExtDecisionReview.__table__,
    ExtConfirmation.__table__,
    ExtEditEvent.__table__,
]


def settings_with(threshold: float | None) -> ExtractionSettings:
    return ExtractionSettings(_env_file=None, candidate_confidence=threshold)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTUNE_EXTRACTION_CANDIDATE_CONFIDENCE", raising=False)
    monkeypatch.setattr(service, "get_settings", lambda: settings_with(None))


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as session:
        session.add(Meeting(id=MEETING, team_id="team_1", title="주간 회의"))
        for i in range(1, 9):
            session.add(
                Utterance(
                    id=f"utt_{i}",
                    meeting_id=MEETING,
                    speaker_label="SPEAKER_00",
                    start_sec=float(i),
                    end_sec=float(i) + 1,
                    text=f"발화 {i}",
                )
            )
        session.flush()
        yield session


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)


def labelled(kinds: dict[int, UtteranceKind]) -> list[ClassifiedUtterance]:
    """Eight utterances; the ones named get a kind, the rest none."""
    return [
        ClassifiedUtterance(id=f"utt_{i}", kind=kinds.get(i), confidence=0.9, text=f"발화 {i}")
        for i in range(1, 9)
    ]


def two_decisions(session: Session) -> list[ExtDecision]:
    """Utterance 2 and utterance 7 settle two separate decisions."""
    return service.build_decisions(
        session, meeting_id=MEETING, utterances=labelled({2: K.DECISION, 7: K.DECISION})
    )


def item(session: Session, item_id: str, status: str) -> None:
    session.add(
        ExtActionItem(
            id=item_id,
            meeting_id=MEETING,
            description=f"{item_id} 할 일",
            status=status,
            confidence=0.8,
            origin="model",
        )
    )
    session.flush()


# --- what a person sees ------------------------------------------------------


def test_every_proposed_decision_starts_pending(client: TestClient, session: Session) -> None:
    two_decisions(session)

    body = client.get(f"{PREFIX}/reviews/{MEETING}").json()

    assert [d["status"] for d in body["decisions"]] == ["pending", "pending"]
    assert body["pending_decisions"] == 2
    assert all(d["statement"] == d["model_statement"] for d in body["decisions"])


def test_nothing_is_pre_checked_while_there_is_no_measured_line(
    client: TestClient, session: Session
) -> None:
    two_decisions(session)

    body = client.get(f"{PREFIX}/reviews/{MEETING}").json()

    assert {d["suggested"] for d in body["decisions"]} == {None}


def test_a_measured_line_pre_checks_what_clears_it(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    two_decisions(session)
    monkeypatch.setattr(service, "get_settings", lambda: settings_with(0.95))

    body = client.get(f"{PREFIX}/reviews/{MEETING}").json()

    assert {d["suggested"] for d in body["decisions"]} == {False}


def test_the_review_lists_only_items_that_still_need_somebody(
    client: TestClient, session: Session
) -> None:
    item(session, "act_waiting", "needs_confirmation")
    item(session, "act_accepted", "todo")

    body = client.get(f"{PREFIX}/reviews/{MEETING}").json()

    assert [i["id"] for i in body["action_items"]] == ["act_waiting"]


def test_a_weak_assent_shows_where_its_question_stands(
    client: TestClient, session: Session
) -> None:
    session.add(ExtConfirmation(utterance_id="utt_4", meeting_id=MEETING, reason=WEAK_ASSENT))
    session.flush()

    body = client.get(f"{PREFIX}/reviews/{MEETING}").json()

    assert body["ambiguous_agreements"] == [
        {"utterance_id": "utt_4", "outcome": "not_asked", "resolved_kind": None}
    ]


def test_an_unknown_meeting_is_not_found(client: TestClient) -> None:
    assert client.get(f"{PREFIX}/reviews/mtg_nope").status_code == 404
    assert client.get(f"{PREFIX}/reviews/mtg_nope/outbound").status_code == 404


# --- a verdict ------------------------------------------------------------------


def test_a_confirmed_decision_is_the_only_one_that_goes_out(
    client: TestClient, session: Session
) -> None:
    first, second = two_decisions(session)

    response = client.patch(f"{PREFIX}/decisions/{first.id}", json={"status": "confirmed"})
    outbound = client.get(f"{PREFIX}/reviews/{MEETING}/outbound").json()

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"
    assert [d["id"] for d in outbound["decisions"]] == [first.id]
    assert second.id not in {d["id"] for d in outbound["decisions"]}


def test_a_rejected_decision_does_not_go_out_and_is_no_longer_pending(
    client: TestClient, session: Session
) -> None:
    first, _ = two_decisions(session)

    client.patch(f"{PREFIX}/decisions/{first.id}", json={"status": "rejected"})
    review = client.get(f"{PREFIX}/reviews/{MEETING}").json()
    outbound = client.get(f"{PREFIX}/reviews/{MEETING}/outbound").json()

    assert review["pending_decisions"] == 1
    assert outbound["decisions"] == []


def test_a_mis_click_can_be_put_back_to_pending(client: TestClient, session: Session) -> None:
    first, _ = two_decisions(session)

    client.patch(f"{PREFIX}/decisions/{first.id}", json={"status": "confirmed"})
    client.patch(f"{PREFIX}/decisions/{first.id}", json={"status": "pending"})

    assert client.get(f"{PREFIX}/reviews/{MEETING}/outbound").json()["decisions"] == []


def test_a_rewording_is_what_goes_out_and_the_models_wording_is_kept_beside_it(
    client: TestClient, session: Session
) -> None:
    first, _ = two_decisions(session)

    client.patch(
        f"{PREFIX}/decisions/{first.id}",
        json={"status": "confirmed", "statement": "출시는 금요일로 확정"},
    )
    listed = next(
        d
        for d in client.get(f"{PREFIX}/reviews/{MEETING}").json()["decisions"]
        if d["id"] == first.id
    )
    outbound = client.get(f"{PREFIX}/reviews/{MEETING}/outbound").json()

    assert listed["statement"] == "출시는 금요일로 확정"
    assert listed["model_statement"] == first.statement
    assert outbound["decisions"] == [{"id": first.id, "statement": "출시는 금요일로 확정"}]


def test_sending_the_models_own_wording_back_clears_the_rewording(
    client: TestClient, session: Session
) -> None:
    first, _ = two_decisions(session)
    client.patch(f"{PREFIX}/decisions/{first.id}", json={"statement": "다르게 적음"})

    client.patch(f"{PREFIX}/decisions/{first.id}", json={"statement": first.statement})

    stored = session.get(ExtDecisionReview, first.id)
    assert stored is not None
    assert stored.statement is None


def test_an_empty_body_records_nothing(client: TestClient, session: Session) -> None:
    first, _ = two_decisions(session)

    assert client.patch(f"{PREFIX}/decisions/{first.id}", json={}).status_code == 200
    assert session.scalars(select(ExtDecisionReview)).all() == []


def test_an_unknown_decision_is_not_found(client: TestClient) -> None:
    response = client.patch(f"{PREFIX}/decisions/dec_nope", json={"status": "confirmed"})
    assert response.status_code == 404


def test_a_reviewer_cannot_be_recorded(client: TestClient, session: Session) -> None:
    """ADR 0003: who confirmed what is one person's conduct in a meeting."""
    first, _ = two_decisions(session)

    response = client.patch(
        f"{PREFIX}/decisions/{first.id}", json={"status": "confirmed", "reviewer_id": "user_1"}
    )

    assert response.status_code == 422
    columns = {c["name"] for c in inspect(session.get_bind()).get_columns("ext_decision_reviews")}
    assert columns == {"decision_id", "meeting_id", "status", "statement", "reviewed_at"}


# --- what may leave -------------------------------------------------------------


def test_an_item_waiting_for_confirmation_does_not_go_out(
    client: TestClient, session: Session
) -> None:
    item(session, "act_waiting", "needs_confirmation")
    item(session, "act_accepted", "todo")

    outbound = client.get(f"{PREFIX}/reviews/{MEETING}/outbound").json()

    assert [i["id"] for i in outbound["action_items"]] == ["act_accepted"]


# --- a rerun --------------------------------------------------------------------


def test_a_rebuild_over_the_same_sources_keeps_the_verdict(
    client: TestClient, session: Session
) -> None:
    first_id = two_decisions(session)[0].id
    client.patch(f"{PREFIX}/decisions/{first_id}", json={"status": "confirmed"})

    rebuilt = [decision.id for decision in two_decisions(session)]

    assert first_id in rebuilt
    outbound = client.get(f"{PREFIX}/reviews/{MEETING}/outbound").json()
    assert [d["id"] for d in outbound["decisions"]] == [first_id]


def test_a_decision_whose_sources_changed_loses_its_verdict(
    client: TestClient, session: Session
) -> None:
    """A different decision. A confirmation given about the old one must not apply
    to it, and a rewording of the old one must not outlive it."""
    first_id = two_decisions(session)[0].id
    client.patch(
        f"{PREFIX}/decisions/{first_id}", json={"status": "confirmed", "statement": "옛 결정"}
    )

    rebuilt = service.build_decisions(
        session, meeting_id=MEETING, utterances=labelled({3: K.DECISION, 7: K.DECISION})
    )

    assert first_id not in {decision.id for decision in rebuilt}
    assert session.get(ExtDecisionReview, first_id) is None
    assert client.get(f"{PREFIX}/reviews/{MEETING}/outbound").json()["decisions"] == []


# --- a person adds, rewords and deletes -------------------------------------------


def add(client: TestClient, statement: str = "회의는 격주로 하기로", **extra: object) -> dict:
    response = client.post(
        f"{PREFIX}/decisions", json={"meeting_id": MEETING, "statement": statement, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_a_person_can_add_a_decision_the_model_missed(client: TestClient, session: Session) -> None:
    added = add(client, source_utterance_ids=["utt_5", "utt_4"])

    assert added["origin"] == "user"
    assert added["status"] == "confirmed"
    assert added["confidence"] == 1.0
    assert added["source_utterance_ids"] == ["utt_5", "utt_4"]
    outbound = client.get(f"{PREFIX}/reviews/{MEETING}/outbound").json()
    assert outbound["decisions"] == [{"id": added["id"], "statement": "회의는 격주로 하기로"}]
    assert added["id"] in {d.id for d in service.result_for_meeting(session, MEETING).decisions}


def test_a_decision_a_person_added_survives_a_rerun(client: TestClient, session: Session) -> None:
    added = add(client)

    two_decisions(session)
    two_decisions(session)

    review = client.get(f"{PREFIX}/reviews/{MEETING}").json()
    assert {d["origin"] for d in review["decisions"]} == {"model", "user"}
    mine = next(d for d in review["decisions"] if d["id"] == added["id"])
    assert mine["status"] == "confirmed"


def test_rewording_a_persons_own_decision_changes_the_decision_itself(
    client: TestClient, session: Session
) -> None:
    added = add(client)

    response = client.patch(f"{PREFIX}/decisions/{added['id']}", json={"statement": "월 1회로"})

    assert response.json()["statement"] == response.json()["model_statement"] == "월 1회로"
    review = session.get(ExtDecisionReview, added["id"])
    assert review is not None
    assert review.statement is None


def test_deleting_a_decision_a_person_added_removes_it(
    client: TestClient, session: Session
) -> None:
    added = add(client)

    assert client.delete(f"{PREFIX}/decisions/{added['id']}").status_code == 204

    assert session.get(ExtDecision, added["id"]) is None
    assert session.get(ExtDecisionReview, added["id"]) is None
    assert client.get(f"{PREFIX}/reviews/{MEETING}").json()["decisions"] == []


def test_deleting_a_proposed_decision_rejects_it_and_a_rerun_keeps_it_out(
    client: TestClient, session: Session
) -> None:
    """Really deleting it would last until the next run proposed it again."""
    first_id = two_decisions(session)[0].id
    client.patch(f"{PREFIX}/decisions/{first_id}", json={"statement": "고친 문장"})

    assert client.delete(f"{PREFIX}/decisions/{first_id}").status_code == 204
    two_decisions(session)

    listed = next(
        d
        for d in client.get(f"{PREFIX}/reviews/{MEETING}").json()["decisions"]
        if d["id"] == first_id
    )
    assert listed["status"] == "rejected"
    assert listed["statement"] == listed["model_statement"]
    assert client.get(f"{PREFIX}/reviews/{MEETING}/outbound").json()["decisions"] == []


def test_a_source_from_another_meeting_is_refused(client: TestClient, session: Session) -> None:
    session.add(Meeting(id="mtg_2", team_id="team_1", title="다른 회의"))
    session.add(
        Utterance(
            id="utt_other",
            meeting_id="mtg_2",
            speaker_label="SPEAKER_00",
            start_sec=0.0,
            end_sec=1.0,
            text="다른 회의 발화",
        )
    )
    session.flush()

    response = client.post(
        f"{PREFIX}/decisions",
        json={"meeting_id": MEETING, "statement": "결정", "source_utterance_ids": ["utt_other"]},
    )

    assert response.status_code == 422
    assert "다른 회의 발화" not in response.text
    assert session.scalars(select(ExtDecision)).all() == []


def test_a_decision_cannot_be_added_to_an_unknown_meeting(client: TestClient) -> None:
    response = client.post(f"{PREFIX}/decisions", json={"meeting_id": "mtg_nope", "statement": "x"})
    assert response.status_code == 404


def test_a_person_cannot_set_the_confidence_of_what_they_typed(client: TestClient) -> None:
    response = client.post(
        f"{PREFIX}/decisions", json={"meeting_id": MEETING, "statement": "x", "confidence": 0.3}
    )
    assert response.status_code == 422


def test_deleting_an_unknown_decision_is_not_found(client: TestClient) -> None:
    assert client.delete(f"{PREFIX}/decisions/dec_nope").status_code == 404


# --- the screen on the way out ----------------------------------------------------


def test_a_confirmed_rewording_with_personal_data_is_held_back_by_category(
    client: TestClient, session: Session
) -> None:
    """A rewording is typed by a person and never passed module A's masker."""
    first_id, second_id = (d.id for d in two_decisions(session))
    client.patch(
        f"{PREFIX}/decisions/{first_id}",
        json={"status": "confirmed", "statement": "담당 연락처 010-1234-5678 로 공유"},
    )
    client.patch(f"{PREFIX}/decisions/{second_id}", json={"status": "confirmed"})

    response = client.get(f"{PREFIX}/reviews/{MEETING}/outbound")
    outbound = response.json()

    assert [d["id"] for d in outbound["decisions"]] == [second_id]
    assert outbound["blocked"] == [{"id": first_id, "kind": "decision", "categories": ["phone"]}]
    assert "1234-5678" not in response.text


def test_an_accepted_item_whose_description_carries_personal_data_is_held_back(
    client: TestClient, session: Session
) -> None:
    session.add(
        ExtActionItem(
            id="act_pii",
            meeting_id=MEETING,
            description="고객 010-9876-5432 에게 회신",
            status="todo",
            confidence=1.0,
            origin="user",
        )
    )
    item(session, "act_clean", "todo")

    response = client.get(f"{PREFIX}/reviews/{MEETING}/outbound")
    outbound = response.json()

    assert [i["id"] for i in outbound["action_items"]] == ["act_clean"]
    assert outbound["blocked"] == [
        {"id": "act_pii", "kind": "action_item", "categories": ["phone"]}
    ]
    assert "9876-5432" not in response.text


def test_nothing_is_blocked_when_nothing_carries_personal_data(
    client: TestClient, session: Session
) -> None:
    first_id = two_decisions(session)[0].id
    client.patch(f"{PREFIX}/decisions/{first_id}", json={"status": "confirmed"})

    assert client.get(f"{PREFIX}/reviews/{MEETING}/outbound").json()["blocked"] == []
