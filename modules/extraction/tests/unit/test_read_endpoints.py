"""The read side of module B's HTTP surface (#108).

SQLite in memory and the router on a bare app, the way apps/api mounts it. The
rules under test are about which rows a filter keeps, the order a quotation
comes back in, and which response is allowed to carry transcript text at all --
none of which a pure function can show without a store behind it.

This is not the integration suite: no Postgres, no migrations.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_contracts.extraction import ExtractionResult
from autune_core import AutuneError, Base, Meeting, User, Utterance, get_session
from autune_extraction import service
from autune_extraction.config import ExtractionSettings
from autune_extraction.confirmations import WEAK_ASSENT
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
OTHER_MEETING = "mtg_2"
PREFIX = "/api/extraction"

TABLES = [
    Meeting.__table__,
    User.__table__,
    Utterance.__table__,
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtClassification.__table__,
    ExtDecision.__table__,
    ExtDecisionSource.__table__,
    # The result leaves out decisions a person rejected (#247), so it reads this.
    ExtDecisionReview.__table__,
    ExtConfirmation.__table__,
    ExtEditEvent.__table__,
]


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """``read_model`` reads the candidate threshold; answer from defaults only.

    Same reason as ``test_candidate_threshold``: a developer's ``.env`` must not
    decide what a test sees.
    """
    monkeypatch.delenv("AUTUNE_EXTRACTION_CANDIDATE_CONFIDENCE", raising=False)
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )


@pytest.fixture
def session() -> Iterator[Session]:
    # One connection shared with the TestClient's thread: an in-memory database
    # exists per connection, and SQLite refuses cross-thread use by default.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as session:
        for meeting_id in (MEETING, OTHER_MEETING):
            session.add(Meeting(id=meeting_id, team_id="team_1", title="주간 회의"))
        session.flush()
        yield session


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    """Module B's router on a bare app, with the error mapping apps/api installs."""
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)


def utterance(session: Session, uid: str, start: float, text: str) -> str:
    session.add(
        Utterance(
            id=uid,
            meeting_id=MEETING,
            speaker_label="SPEAKER_00",
            start_sec=start,
            end_sec=start + 2.0,
            text=text,
        )
    )
    session.flush()
    return uid


def action_item(
    session: Session,
    item_id: str,
    *,
    meeting_id: str = MEETING,
    status: str = "needs_confirmation",
    assignee_id: str | None = None,
    due_date: date | None = None,
    sources: tuple[str, ...] = (),
    origin: str = "model",
) -> ExtActionItem:
    row = ExtActionItem(
        id=item_id,
        meeting_id=meeting_id,
        description=f"{item_id} 할 일",
        status=status,
        assignee_id=assignee_id,
        due_date=due_date,
        confidence=0.8 if origin == "model" else 1.0,
        origin=origin,
    )
    row.sources = [ExtActionItemSource(utterance_id=uid) for uid in sources]
    session.add(row)
    session.flush()
    return row


def ids(response_body: list[dict[str, object]]) -> list[object]:
    return [entry["id"] for entry in response_body]


# --- GET /action-items ------------------------------------------------------


def test_the_list_is_every_item_when_nothing_is_filtered(
    client: TestClient, session: Session
) -> None:
    action_item(session, "act_1")
    action_item(session, "act_2", meeting_id=OTHER_MEETING)

    response = client.get(f"{PREFIX}/action-items")

    assert response.status_code == 200
    assert sorted(ids(response.json())) == ["act_1", "act_2"]


def test_filters_narrow_the_list_and_combine(client: TestClient, session: Session) -> None:
    action_item(session, "act_1", status="todo", assignee_id="user_a")
    action_item(session, "act_2", status="todo", assignee_id="user_b")
    action_item(session, "act_3", status="done", assignee_id="user_a")
    action_item(session, "act_4", status="todo", assignee_id="user_a", meeting_id=OTHER_MEETING)

    def listed(**params: str) -> list[object]:
        return sorted(ids(client.get(f"{PREFIX}/action-items", params=params).json()))

    assert listed(meeting_id=MEETING) == ["act_1", "act_2", "act_3"]
    assert listed(assignee_id="user_a") == ["act_1", "act_3", "act_4"]
    assert listed(status="todo") == ["act_1", "act_2", "act_4"]
    assert listed(meeting_id=MEETING, assignee_id="user_a", status="todo") == ["act_1"]


def test_an_identified_assignee_carries_their_current_name(
    client: TestClient, session: Session
) -> None:
    """``assignee_label`` is only ever the unresolved fallback text -- an item
    whose speaker *was* identified has no label, and a card reading only that
    field shows an assigned item as unassigned. ``assignee_name`` is read fresh
    from ``users`` for exactly this case, so a display name change reaches the
    board on the next request rather than needing the item rewritten."""
    session.add(User(id="user_a", email="a@example.com", display_name="박지영"))
    session.flush()
    action_item(session, "act_1", assignee_id="user_a")
    action_item(session, "act_2")  # no assignee at all

    body = client.get(f"{PREFIX}/action-items", params={"meeting_id": MEETING}).json()

    by_id = {item["id"]: item for item in body}
    assert by_id["act_1"]["assignee_name"] == "박지영"
    assert by_id["act_2"]["assignee_name"] is None

    detail = client.get(f"{PREFIX}/action-items/act_1").json()
    assert detail["assignee_name"] == "박지영"


def test_an_assignee_whose_account_is_gone_reports_no_name(
    client: TestClient, session: Session
) -> None:
    """``assignee_id`` is ``SET NULL`` on account deletion in Postgres; this
    unit suite's SQLite tables enforce no such foreign key, so the dangling id
    this test writes is the shape a deleted-account row is left in. Reads
    nothing, rather than raising on a user that used to exist."""
    action_item(session, "act_1", assignee_id="user_ghost")

    body = client.get(f"{PREFIX}/action-items", params={"meeting_id": MEETING}).json()

    assert body[0]["assignee_id"] == "user_ghost"
    assert body[0]["assignee_name"] is None


def test_due_before_is_strict_and_drops_items_with_no_date(
    client: TestClient, session: Session
) -> None:
    """Passing today's date asks for what is overdue.

    An item due today is not overdue, and an item with no due date is never
    before anything -- reading it as overdue would put every undated item at the
    top of S05's "things for me".
    """
    action_item(session, "act_past", due_date=date(2026, 9, 10))
    action_item(session, "act_today", due_date=date(2026, 9, 11))
    action_item(session, "act_undated")

    body = client.get(f"{PREFIX}/action-items", params={"due_before": "2026-09-11"}).json()

    assert ids(body) == ["act_past"]


def test_an_unknown_status_is_refused_rather_than_matching_nothing(client: TestClient) -> None:
    """A typo in a filter should fail loudly, not render an empty board."""
    response = client.get(f"{PREFIX}/action-items", params={"status": "finished"})

    assert response.status_code == 422


def test_the_list_carries_source_ids_but_never_their_text(
    client: TestClient, session: Session
) -> None:
    """Transcript text leaves only through the detail route.

    The card needs a count; the quotation is the drawer's. A board that nobody
    opens a drawer on should send no part of the transcript.
    """
    utterance(session, "utt_1", 1.0, "제가 금요일까지 정리할게요")
    action_item(session, "act_1", sources=("utt_1",))

    response = client.get(f"{PREFIX}/action-items")
    (entry,) = response.json()

    assert entry["source_utterance_ids"] == ["utt_1"]
    assert "sources" not in entry
    assert "금요일까지" not in response.text


# --- GET /action-items/{id} -------------------------------------------------


def test_the_detail_quotes_its_sources_in_the_order_they_were_spoken(
    client: TestClient, session: Session
) -> None:
    """Linked out of order on purpose: the proposal came first, the agreement last."""
    utterance(session, "utt_early", 10.0, "배포 스크립트 누가 정리하죠?")
    utterance(session, "utt_late", 42.0, "제가 금요일까지 할게요")
    action_item(session, "act_1", sources=("utt_late", "utt_early"))

    body = client.get(f"{PREFIX}/action-items/act_1").json()

    assert body["sources"] == [
        {"id": "utt_early", "text": "배포 스크립트 누가 정리하죠?"},
        {"id": "utt_late", "text": "제가 금요일까지 할게요"},
    ]
    assert body["source_utterance_ids"] == ["utt_late", "utt_early"], "insertion order"


def test_the_detail_returns_the_text_as_stored(client: TestClient, session: Session) -> None:
    """Masked by module A before the first write; this route adds nothing to it."""
    utterance(session, "utt_1", 1.0, "010-****-5678로 연락 주세요")
    action_item(session, "act_1", sources=("utt_1",))

    body = client.get(f"{PREFIX}/action-items/act_1").json()

    assert body["sources"][0]["text"] == "010-****-5678로 연락 주세요"


def test_a_hand_added_item_has_no_quotation(client: TestClient, session: Session) -> None:
    """That is what "the model missed it" means, and the drawer renders it."""
    action_item(session, "act_1", origin="user")

    body = client.get(f"{PREFIX}/action-items/act_1").json()

    assert body["sources"] == []
    assert body["origin"] == "user"


def test_the_detail_carries_everything_the_list_does(client: TestClient, session: Session) -> None:
    """The drawer opens from a card; it must not know less than the card did."""
    utterance(session, "utt_1", 1.0, "제가 할게요")
    action_item(session, "act_1", sources=("utt_1",), due_date=date(2026, 9, 18))

    (listed,) = client.get(f"{PREFIX}/action-items").json()
    detail = client.get(f"{PREFIX}/action-items/act_1").json()

    assert {k: v for k, v in detail.items() if k != "sources"} == listed


def test_an_unknown_item_is_a_404_that_names_only_the_id(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/action-items/act_missing")

    assert response.status_code == 404
    assert "act_missing" in response.text


# --- GET /results/{meeting_id} ----------------------------------------------


def test_a_meeting_with_nothing_extracted_is_empty_not_missing(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/results/{MEETING}")

    assert response.status_code == 200
    result = ExtractionResult.model_validate(response.json())
    assert result.meeting_id == MEETING
    assert result.action_items == []
    assert result.decisions == []
    assert result.classifications == []
    assert result.ambiguous_agreements == []


def test_a_meeting_that_does_not_exist_is_a_404(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/results/mtg_missing")

    assert response.status_code == 404


def test_the_result_is_the_contract_and_only_this_meeting(
    client: TestClient, session: Session
) -> None:
    """What D and E will read once #31 publishes it, built from the same rows."""
    utterance(session, "utt_1", 1.0, "이번 분기는 A안으로 가죠")
    utterance(session, "utt_2", 5.0, "제가 정리할게요")
    utterance(session, "utt_3", 9.0, "한번 볼게요")
    action_item(session, "act_1", status="todo", sources=("utt_2",))
    action_item(session, "act_other", meeting_id=OTHER_MEETING)
    session.add(
        ExtDecision(
            id="dec_1",
            meeting_id=MEETING,
            statement="이번 분기는 A안으로 간다",
            confidence=0.9,
            sources=[ExtDecisionSource(utterance_id="utt_1", position=0)],
        )
    )
    session.add(
        ExtConfirmation(
            utterance_id="utt_3",
            meeting_id=MEETING,
            reason=WEAK_ASSENT,
            sent_at=datetime(2026, 9, 11, tzinfo=UTC),
        )
    )
    session.flush()

    result = ExtractionResult.model_validate(client.get(f"{PREFIX}/results/{MEETING}").json())

    (item,) = result.action_items
    assert item.id == "act_1"
    assert item.source_utterance_ids == ["utt_2"]
    assert item.status.value == "todo"
    assert item.external_refs == [], "nothing is synced before #30"
    assert [d.id for d in result.decisions] == ["dec_1"]
    assert [a.utterance_id for a in result.ambiguous_agreements] == ["utt_3"]


def test_the_result_carries_what_the_pipeline_classified(
    client: TestClient, session: Session
) -> None:
    """``ext_classifications`` is what the pipeline writes (#151), in spoken order.

    Without this the endpoint answered ``classifications: []`` for a meeting
    that had been classified, and the only test here was of one that had not.
    """
    utterance(session, "utt_1", 9.0, "예산은 언제 나오나요")
    utterance(session, "utt_2", 1.0, "A안으로 가기로 했습니다")
    for uid, kind in (("utt_1", "open_question"), ("utt_2", "decision")):
        session.add(
            ExtClassification(
                utterance_id=uid,
                meeting_id=MEETING,
                kind=kind,
                confidence=0.8,
                model_version="fake",
                nli_verified=False,
            )
        )
    session.flush()

    result = ExtractionResult.model_validate(client.get(f"{PREFIX}/results/{MEETING}").json())

    assert [(c.utterance_id, c.kind.value) for c in result.classifications] == [
        ("utt_2", "decision"),
        ("utt_1", "open_question"),
    ]


def test_the_result_reflects_a_correction_made_after_extraction(
    client: TestClient, session: Session
) -> None:
    """Built from what is stored, so a deleted item is gone and an added one is in."""
    action_item(session, "act_wrong")
    client.delete(f"{PREFIX}/action-items/act_wrong")
    client.post(
        f"{PREFIX}/action-items",
        json={"meeting_id": MEETING, "description": "모델이 놓친 일"},
    )

    result = ExtractionResult.model_validate(client.get(f"{PREFIX}/results/{MEETING}").json())

    assert [item.description for item in result.action_items] == ["모델이 놓친 일"]


# --- a setting that cannot be read ----------------------------------------------


@pytest.mark.parametrize("method", ["post", "patch"])
def test_a_threshold_that_cannot_be_read_saves_nothing(
    session: Session, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """A 7 in .env fails every request; it must not also have saved the write.

    The response used to be built after the commit, so the item was stored and
    the client got a 500 -- and a retry made a second one. Here the session is
    wired the way ``get_session`` wires it: commit on success, roll back on an
    exception.
    """
    action_item(session, "act_1")
    session.commit()

    def unreadable() -> ExtractionSettings:
        return ExtractionSettings(_env_file=None, candidate_confidence=7.0)  # type: ignore[call-arg]

    monkeypatch.setattr(service, "get_settings", unreadable)

    def scoped() -> Iterator[Session]:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

    app = FastAPI()
    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = scoped
    client = TestClient(app, raise_server_exceptions=False)

    if method == "post":
        response = client.post(
            f"{PREFIX}/action-items", json={"meeting_id": MEETING, "description": "새 항목"}
        )
    else:
        response = client.patch(f"{PREFIX}/action-items/act_1", json={"description": "고친 항목"})

    assert response.status_code == 500
    assert session.query(ExtActionItem).count() == 1
    assert session.get(ExtActionItem, "act_1").description == "act_1 할 일"  # type: ignore[union-attr]
    assert session.query(ExtEditEvent).count() == 0, "no edit was counted either"
