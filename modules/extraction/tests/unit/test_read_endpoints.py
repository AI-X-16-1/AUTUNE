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
from autune_core import (
    AutuneError,
    Base,
    Meeting,
    Participant,
    TeamMember,
    User,
    Utterance,
    get_session,
)
from autune_core.auth import current_user
from autune_extraction import service, tasks
from autune_extraction.config import ExtractionSettings
from autune_extraction.confirmations import WEAK_ASSENT
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtClassification,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionRef,
    ExtDecisionReview,
    ExtDecisionSource,
    ExtEditEvent,
    ExtExternalRef,
    ExtNotionTarget,
)
from autune_extraction.router import router

from .conftest import sign_in

MEETING = "mtg_1"
OTHER_MEETING = "mtg_2"
PREFIX = "/api/extraction"

TABLES = [
    Meeting.__table__,
    User.__table__,
    # Read on every list: an assignee who is not a member needs reassigning.
    TeamMember.__table__,
    Participant.__table__,
    Utterance.__table__,
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtClassification.__table__,
    ExtDecision.__table__,
    ExtDecisionSource.__table__,
    # The result leaves out decisions a person rejected (#247), so it reads this.
    ExtDecisionReview.__table__,
    ExtDecisionRef.__table__,
    ExtConfirmation.__table__,
    ExtEditEvent.__table__,
    ExtExternalRef.__table__,
    ExtNotionTarget.__table__,
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
    sign_in(app, session)
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
    sources: tuple[str | None, ...] = (),
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


def member(session: Session, user_id: str, name: str = "팀원") -> str:
    """A user on the meetings' team -- the normal case for an assignee."""
    session.add(User(id=user_id, email=f"{user_id}@example.com", display_name=name))
    session.add(TeamMember(team_id="team_1", user_id=user_id))
    session.flush()
    return user_id


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
    member(session, "user_a", "박지영")
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
    nothing, rather than raising on a user that used to exist -- and, being on
    no team, reads as cleared, which is what Postgres would have stored."""
    action_item(session, "act_1", assignee_id="user_ghost")

    body = client.get(f"{PREFIX}/action-items", params={"meeting_id": MEETING}).json()

    assert body[0]["assignee_id"] is None
    assert body[0]["assignee_name"] is None


# --- ADR 0007: departure and deleted sources ---------------------------------


def test_an_open_item_whose_assignee_left_the_team_needs_reassigning(
    client: TestClient, session: Session
) -> None:
    """ "An open commitment is reassigned, never orphaned." Leaving a team
    removes a ``team_members`` row and nothing else, so that row is the signal."""
    member(session, "user_a", "박지영")
    session.add(User(id="user_gone", email="gone@example.com", display_name="이건우"))
    session.flush()
    action_item(session, "act_stays", status="todo", assignee_id="user_a")
    action_item(session, "act_open", status="in_progress", assignee_id="user_gone")
    action_item(session, "act_closed", status="done", assignee_id="user_gone")

    body = client.get(f"{PREFIX}/action-items", params={"meeting_id": MEETING}).json()

    by_id = {item["id"]: item for item in body}
    assert {key: by_id[key]["needs_reassignment"] for key in by_id} == {
        "act_stays": False,
        "act_open": True,
        "act_closed": False,
    }
    # "Its assignee clears" -- on the closed one too; it just needs nothing.
    for key in ("act_open", "act_closed"):
        assert (by_id[key]["assignee_id"], by_id[key]["assignee_name"]) == (None, None)
    assert by_id["act_stays"]["assignee_name"] == "박지영"

    detail = client.get(f"{PREFIX}/action-items/act_open").json()
    assert detail["needs_reassignment"] is True
    assert detail["assignee_id"] is None


def test_membership_is_read_against_the_meetings_own_team(
    client: TestClient, session: Session
) -> None:
    """A member of some other team is not a member of this one."""
    session.add(User(id="user_x", email="x@example.com", display_name="다른 팀"))
    session.add(TeamMember(team_id="team_other", user_id="user_x"))
    session.flush()
    action_item(session, "act_1", status="todo", assignee_id="user_x")

    body = client.get(f"{PREFIX}/action-items", params={"meeting_id": MEETING}).json()

    assert body[0]["needs_reassignment"] is True


def test_rejoining_the_team_gives_the_item_back(client: TestClient, session: Session) -> None:
    """The stored column keeps the id; clearing happens on the way out."""
    session.add(User(id="user_back", email="back@example.com", display_name="복귀"))
    session.flush()
    action_item(session, "act_1", status="todo", assignee_id="user_back")
    assert client.get(f"{PREFIX}/action-items").json()[0]["needs_reassignment"] is True

    session.add(TeamMember(team_id="team_1", user_id="user_back"))
    session.flush()

    item = client.get(f"{PREFIX}/action-items").json()[0]
    assert (item["assignee_id"], item["needs_reassignment"]) == ("user_back", False)


def test_a_deleted_source_is_counted_and_not_listed(client: TestClient, session: Session) -> None:
    """ "Missing attribution is shown, not hidden." The link row outlives its
    utterance with a NULL id (Postgres ``SET NULL``; written directly here,
    since this suite's SQLite enforces no foreign keys)."""
    kept = utterance(session, "utt_kept", 1.0, "배포는 제가 할게요")
    action_item(session, "act_1", sources=(kept, None))
    action_item(session, "act_2", sources=(None,))

    body = client.get(f"{PREFIX}/action-items", params={"meeting_id": MEETING}).json()
    by_id = {item["id"]: item for item in body}

    assert (by_id["act_1"]["source_utterance_ids"], by_id["act_1"]["deleted_source_count"]) == (
        ["utt_kept"],
        1,
    )
    assert (by_id["act_2"]["source_utterance_ids"], by_id["act_2"]["deleted_source_count"]) == (
        [],
        1,
    )
    detail = client.get(f"{PREFIX}/action-items/act_1").json()
    assert [source["id"] for source in detail["sources"]] == ["utt_kept"]


def test_the_contract_never_carries_a_deleted_source(session: Session) -> None:
    """D and E read ``source_utterance_ids`` as ids that resolve; a NULL would
    not even validate."""
    action_item(session, "act_1", sources=(None,))

    result = service.result_for_meeting(session, MEETING)

    assert result.action_items[0].source_utterance_ids == []


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


def test_a_single_source_has_no_summary_because_description_already_is_it(
    client: TestClient, session: Session
) -> None:
    utterance(session, "utt_1", 1.0, "제가 금요일까지 정리할게요")
    action_item(session, "act_1", sources=("utt_1",))

    (entry,) = client.get(f"{PREFIX}/action-items").json()

    assert entry["summary"] is None


def test_several_sources_get_a_summary_of_the_longest_one(
    client: TestClient, session: Session
) -> None:
    """Rule-based prototype: the longest source, truncated. Not a model --
    checked against the drawer's full quotation, not generated prose."""
    utterance(session, "utt_1", 1.0, "네")
    utterance(session, "utt_2", 2.0, "일정이 밀리면 다음 주 화요일로 옮기는 게 낫겠어요")
    utterance(session, "utt_3", 3.0, "좋아요")
    action_item(session, "act_1", sources=("utt_1", "utt_2", "utt_3"))

    (entry,) = client.get(f"{PREFIX}/action-items").json()

    assert entry["summary"] == "일정이 밀리면 다음 주 화요일로 옮기는 게 낫겠어요"


def test_a_long_source_is_truncated() -> None:
    from autune_extraction.service import SUMMARY_MAX_CHARS, _truncate

    text = "가" * (SUMMARY_MAX_CHARS + 20)

    truncated = _truncate(text)

    assert len(truncated) == SUMMARY_MAX_CHARS
    assert truncated.endswith("…")


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

    assert {k: v for k, v in detail.items() if k not in ("sources", "context", "history")} == listed


def test_a_confirmed_items_notion_status_reaches_both_the_card_and_the_drawer(
    client: TestClient, session: Session
) -> None:
    """Unlike `sources`, `sync_refs` is not meeting content -- a system
    name, a url and an id -- so it rides the list the way `description` and
    `assignee_label` already do (a separate concern from #313's `summary`,
    which stays a curated line rather than the raw fact). Not named
    `external_refs`: the contract's `ActionItem` already has a field by that
    name (the outbound one, stricter-typed), and this module's own response
    extends it."""
    action_item(session, "act_1")
    session.add(
        ExtExternalRef(
            action_item_id="act_1",
            system="notion",
            meeting_id=MEETING,
            url="https://www.notion.so/page1",
            external_id="page1",
        )
    )
    session.flush()

    listed = client.get(f"{PREFIX}/action-items").json()[0]
    detail = client.get(f"{PREFIX}/action-items/act_1").json()

    want = [{"system": "notion", "url": "https://www.notion.so/page1", "external_id": "page1"}]
    assert listed["sync_refs"] == want
    assert detail["sync_refs"] == [
        {"system": "notion", "url": "https://www.notion.so/page1", "external_id": "page1"}
    ]


def test_an_item_never_synced_has_no_sync_refs(client: TestClient, session: Session) -> None:
    action_item(session, "act_1")

    detail = client.get(f"{PREFIX}/action-items/act_1").json()

    assert detail["sync_refs"] == []


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
    sign_in(app, session)
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


# --- who may read what (#189) -----------------------------------------------------

FOREIGN_MEETING = "mtg_x"
"""Held by a team the signed-in caller is not on."""

# (method, path, body) with ``{m}``/``{a}``/``{d}`` for a meeting, item, decision id.
ROUTES = [
    ("get", "/results/{m}", None),
    ("get", "/reviews/{m}", None),
    ("get", "/reviews/{m}/outbound", None),
    ("get", "/action-items/{a}", None),
    ("patch", "/action-items/{a}", {"description": "고친 설명"}),
    ("delete", "/action-items/{a}", None),
    ("post", "/action-items", {"meeting_id": "{m}", "description": "새 항목"}),
    ("patch", "/decisions/{d}", {"status": "confirmed"}),
    ("delete", "/decisions/{d}", None),
    ("post", "/decisions", {"meeting_id": "{m}", "statement": "새 결정"}),
]


@pytest.fixture
def foreign(session: Session) -> None:
    session.add(Meeting(id=FOREIGN_MEETING, team_id="team_other", title="다른 팀 회의"))
    session.flush()
    action_item(session, "act_x", meeting_id=FOREIGN_MEETING)
    session.add(
        ExtDecision(
            id="dec_x", meeting_id=FOREIGN_MEETING, statement="다른 팀의 결정", confidence=0.9
        )
    )
    session.flush()


def _call(client: TestClient, method: str, path: str, body: dict | None, ids: dict) -> str:
    url = PREFIX + path.format(**ids)
    if body is None:
        response = client.request(method, url)
    else:
        filled = {k: v.format(**ids) if isinstance(v, str) else v for k, v in body.items()}
        response = client.request(method, url, json=filled)
    assert response.status_code == 404, (method, path, response.text)
    return response.text


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
@pytest.mark.usefixtures("foreign")
def test_another_teams_meeting_is_refused_exactly_as_an_unknown_one(
    client: TestClient, method: str, path: str, body: dict | None
) -> None:
    """A 403 would confirm the id exists; the refusal must be the same 404, with
    only the id differing, and must name nothing from the meeting."""
    theirs = {"m": FOREIGN_MEETING, "a": "act_x", "d": "dec_x"}
    missing = {"m": "mtg_missing", "a": "act_missing", "d": "dec_missing"}

    refused = _call(client, method, path, body, theirs)
    unknown = _call(client, method, path, body, missing)

    for real, fake in zip(theirs.values(), missing.values(), strict=True):
        refused = refused.replace(real, "ID")
        unknown = unknown.replace(fake, "ID")
    assert refused == unknown
    assert "다른 팀" not in refused


@pytest.mark.usefixtures("foreign")
def test_a_refused_write_changes_nothing(client: TestClient, session: Session) -> None:
    for method, path, body in ROUTES:
        if method != "get":
            _call(client, method, path, body, {"m": FOREIGN_MEETING, "a": "act_x", "d": "dec_x"})
    session.expire_all()

    items = session.query(ExtActionItem).filter_by(meeting_id=FOREIGN_MEETING).all()
    assert [(i.id, i.description) for i in items] == [("act_x", "act_x 할 일")]
    assert session.query(ExtDecision).filter_by(meeting_id=FOREIGN_MEETING).count() == 1
    assert session.query(ExtDecisionReview).count() == 0
    assert session.query(ExtEditEvent).count() == 0


@pytest.mark.usefixtures("foreign")
def test_the_list_holds_only_the_callers_teams_items(client: TestClient, session: Session) -> None:
    action_item(session, "act_1")

    everything = client.get(f"{PREFIX}/action-items").json()
    theirs = client.get(f"{PREFIX}/action-items", params={"meeting_id": FOREIGN_MEETING}).json()

    assert [i["id"] for i in everything] == ["act_1"]
    assert theirs == [], "the same empty list a meeting that does not exist gets"


def test_no_session_is_refused_before_anything_is_read(client: TestClient) -> None:
    client.app.dependency_overrides.pop(current_user)  # type: ignore[attr-defined]

    assert client.get(f"{PREFIX}/results/{MEETING}").status_code == 403
    assert client.get(f"{PREFIX}/action-items").status_code == 403
    assert client.get(f"{PREFIX}/health").status_code == 200


# --- the drawer's history (S18, #109) -------------------------------------------


@pytest.fixture
def no_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """An edit past needs_confirmation queues the Notion sync, which opens its own
    session_scope on a real database; these tests are about the history row."""
    monkeypatch.setattr(tasks, "sync_after_confirmation", lambda _action_item_id: None)


@pytest.mark.usefixtures("no_sync")
def test_history_names_the_fields_an_edit_changed_and_keeps_no_value(
    client: TestClient, session: Session
) -> None:
    action_item(session, "act_1", status="todo", due_date=date(2026, 10, 2))

    client.patch(f"{PREFIX}/action-items/act_1", json={"due_date": "2026-10-09"})
    client.patch(
        f"{PREFIX}/action-items/act_1", json={"description": "고친 설명", "status": "in_progress"}
    )
    detail = client.get(f"{PREFIX}/action-items/act_1").json()

    assert [(h["kind"], h["fields"]) for h in detail["history"]] == [
        ("edited", ["due_date"]),
        ("edited", ["description", "status"]),
    ]
    stored = [(e.kind, e.fields) for e in session.query(ExtEditEvent).order_by(ExtEditEvent.id)]
    assert "act_1 할 일" not in str(stored), "the sentence a person replaced is not kept"
    assert "2026-10-02" not in str(stored)


def test_a_hand_added_item_starts_its_history_with_being_added(
    client: TestClient, session: Session
) -> None:
    created = client.post(
        f"{PREFIX}/action-items", json={"meeting_id": MEETING, "description": "새 항목"}
    ).json()

    history = client.get(f"{PREFIX}/action-items/{created['id']}").json()["history"]

    assert [(h["kind"], h["fields"]) for h in history] == [("created", [])]


@pytest.mark.usefixtures("no_sync")
def test_an_untouched_extracted_item_has_no_history(client: TestClient, session: Session) -> None:
    action_item(session, "act_1")
    action_item(session, "act_2")
    client.patch(f"{PREFIX}/action-items/act_2", json={"status": "todo"})

    assert client.get(f"{PREFIX}/action-items/act_1").json()["history"] == []


def test_an_empty_edit_records_nothing(client: TestClient, session: Session) -> None:
    action_item(session, "act_1")

    client.patch(f"{PREFIX}/action-items/act_1", json={})

    assert client.get(f"{PREFIX}/action-items/act_1").json()["history"] == []


@pytest.mark.usefixtures("no_sync")
def test_history_carries_no_person(client: TestClient, session: Session) -> None:
    """ADR 0003: who corrected the model is per-person conduct; not stored, not sent."""
    action_item(session, "act_1")
    client.patch(f"{PREFIX}/action-items/act_1", json={"status": "todo"})

    (entry,) = client.get(f"{PREFIX}/action-items/act_1").json()["history"]

    assert set(entry) == {"kind", "fields", "at"}
