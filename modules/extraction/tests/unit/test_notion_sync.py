"""Step 7 for action items: a confirmed item becomes one Notion page (#30).

SQLite in memory, ``FakeNotion`` for the workspace, the router on a bare app.
The rules under test are when a page is sent (only after a person confirms, and
only once), what it carries (the item, never the transcript), and that a team
without Notion, a broker that is down or a failed call cost the page and nothing
else.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import (
    AutuneError,
    Base,
    Meeting,
    PrivacyViolationError,
    TeamMember,
    Utterance,
    get_session,
)
from autune_core.integrations_config import IntegrationConfig
from autune_extraction import service, tasks
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtCalendarEvent,
    ExtEditEvent,
    ExtExternalRef,
    ExtNotionTarget,
    ExtSyncFailure,
)
from autune_extraction.router import router
from autune_integrations import PermanentIntegrationError
from autune_integrations.fakes import FakeNotion

from .conftest import sign_in

MEETING = "mtg_1"
DATABASE = "db_actions"
PREFIX = "/api/extraction"

TABLES = [
    Meeting.__table__,
    TeamMember.__table__,
    Utterance.__table__,
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtEditEvent.__table__,
    ExtExternalRef.__table__,
    ExtNotionTarget.__table__,
    # ``has_copy_outside`` counts a calendar event as a copy (#672).
    ExtCalendarEvent.__table__,
    # An edit's answer carries the item's standing failures (review of #754),
    # and a create that timed out is looked for before a second one.
    ExtSyncFailure.__table__,
]


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTUNE_EXTRACTION_CANDIDATE_CONFIDENCE", raising=False)
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
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as session:
        session.add(Meeting(id=MEETING, team_id="team_1", title="스프린트 회의"))
        session.add(
            Utterance(
                id="utt_1",
                meeting_id=MEETING,
                speaker_label="SPEAKER_00",
                start_sec=0.0,
                end_sec=2.0,
                text="제가 금요일까지 정리하겠습니다",
            )
        )
        session.flush()
        yield session


def item(session: Session, *, status: str = "todo", **fields: object) -> ExtActionItem:
    row = ExtActionItem(
        meeting_id=MEETING,
        description=str(fields.pop("description", "릴리스 노트 정리")),
        assignee_label=fields.pop("assignee_label", "김개발"),
        due_date=fields.pop("due_date", date(2026, 9, 25)),
        status=status,
        confidence=0.91,
        origin="model",
    )
    row.sources = [ExtActionItemSource(utterance_id="utt_1")]
    session.add(row)
    session.flush()
    return row


def sync(session: Session, notion: FakeNotion, action_item_id: str, **kw: object) -> object:
    return service.sync_action_item_to_notion(
        session, notion, action_item_id=action_item_id, database_id=DATABASE, **kw
    )


# --- what a page carries ---------------------------------------------------------


def test_a_confirmed_item_becomes_one_page_with_the_item_and_no_transcript(
    session: Session,
) -> None:
    notion = FakeNotion()
    row = item(session)

    ref = sync(session, notion, row.id)

    assert len(notion.pages) == 1
    database, properties = notion.pages[0]
    assert database == DATABASE
    assert properties == {
        "작업": {"title": [{"type": "text", "text": {"content": "릴리스 노트 정리"}}]},
        "담당자": {"rich_text": [{"type": "text", "text": {"content": "김개발"}}]},
        "마감일": {"date": {"start": "2026-09-25"}},
        "상태": {"select": {"name": "진행 전"}},
        "신뢰도": {"number": 0.91},
        "회의": {"rich_text": [{"type": "text", "text": {"content": "스프린트 회의"}}]},
    }
    assert "제가 금요일까지" not in repr(properties), "source utterances stay in Autune"
    stored = session.get(ExtExternalRef, (row.id, "notion"))
    assert stored is ref
    assert stored is not None
    assert stored.external_id == "page_1"
    assert stored.url == "https://www.notion.so/page_1"


# --- a create that timed out may have arrived (#754 review) ---------------------------


class Counting(FakeNotion):
    def __init__(self) -> None:
        super().__init__()
        self.looked = 0

    def find_pages(self, database_id: str, **kw: Any) -> list[str]:
        self.looked += 1
        return super().find_pages(database_id, **kw)


def lost_create(session: Session, item_id: str, kind: str = "unreachable") -> None:
    session.add(
        ExtSyncFailure(
            action_item_id=item_id,
            system="notion",
            kind=kind,
            failed_at=datetime(2026, 10, 3, 1, 0, tzinfo=UTC),
        )
    )
    session.flush()


def test_a_page_a_timed_out_create_made_is_kept_not_made_twice(session: Session) -> None:
    """mkkim68 and lsh2217, reviews of #754: "다시 시도" after no answer made a
    second page when the first create had in fact arrived."""
    notion = Counting()
    row = item(session)
    # The first create reached Notion; our side saw a timeout.
    notion.create_page(
        DATABASE, {"작업": {"title": [{"type": "text", "text": {"content": "릴리스 노트 정리"}}]}}
    )
    lost_create(session, row.id)

    ref = sync(session, notion, row.id)

    assert len(notion.pages) == 1, "no second page"
    assert ref is not None and ref.external_id == "page_1"
    assert notion.updates and notion.updates[-1][0] == "page_1", "brought up to date"


def test_a_page_another_item_holds_is_never_taken(session: Session) -> None:
    """mkkim68, review of #777: sentences repeat ("회의록 공유"). Another item's
    page with the same title, made in the window, is that item's -- taken, the
    two would share one page and overwrite each other."""
    notion = Counting()
    other = item(session)
    sync(session, notion, other.id)  # page_1, held by the other item
    row = item(session)  # same sentence; its own create never arrived
    lost_create(session, row.id)

    ref = sync(session, notion, row.id)

    assert notion.looked == 1
    assert len(notion.pages) == 2, "a page of its own"
    assert ref is not None and ref.external_id == "page_2"
    held = session.get(ExtExternalRef, (other.id, "notion"))
    assert held is not None and held.external_id == "page_1"


def test_with_nothing_found_the_page_is_made(session: Session) -> None:
    notion = Counting()
    row = item(session)
    lost_create(session, row.id)

    sync(session, notion, row.id)

    assert notion.looked == 1
    assert len(notion.pages) == 1


def test_with_two_found_none_is_taken_and_a_page_is_made(session: Session) -> None:
    """Two pages with the same title: nothing here can tell which is this item's."""
    notion = Counting()
    row = item(session)
    title = {"작업": {"title": [{"type": "text", "text": {"content": "릴리스 노트 정리"}}]}}
    notion.create_page(DATABASE, title)
    notion.create_page(DATABASE, title)
    lost_create(session, row.id)

    ref = sync(session, notion, row.id)

    assert len(notion.pages) == 3
    assert ref is not None and ref.external_id == "page_3"


@pytest.mark.parametrize("kind", [None, "rejected", "privacy", "reconnect"])
def test_only_a_create_that_may_have_arrived_is_looked_for(
    session: Session, kind: str | None
) -> None:
    """Any other failure, or none, means nothing reached Notion: no query."""
    notion = Counting()
    row = item(session)
    if kind is not None:
        lost_create(session, row.id, kind)

    sync(session, notion, row.id)

    assert notion.looked == 0
    assert len(notion.pages) == 1


def test_a_field_the_item_does_not_have_is_left_off_the_page(session: Session) -> None:
    notion = FakeNotion()
    row = item(session, assignee_label=None, due_date=None)

    sync(session, notion, row.id)

    assert set(notion.pages[0][1]) == {"작업", "상태", "신뢰도", "회의"}


def test_a_team_map_replaces_the_defaults_and_can_send_only_a_title(session: Session) -> None:
    """A team whose database has two columns gets a page with two. Merged with
    the defaults it got none: Notion refuses a page naming a property the
    database does not have. Raised in review of #294."""
    notion = FakeNotion()
    row = item(session)

    sync(session, notion, row.id, property_names={"title": "Name", "assignee": "Owner"})

    assert set(notion.pages[0][1]) == {"Name", "Owner"}


# --- when a page is sent --------------------------------------------------------


def test_an_item_still_waiting_for_confirmation_sends_nothing(session: Session) -> None:
    notion = FakeNotion()
    row = item(session, status="needs_confirmation")

    assert sync(session, notion, row.id) is None
    assert notion.pages == []
    assert session.scalars(select(ExtExternalRef)).all() == []


def test_moving_an_item_back_to_confirmation_updates_its_pages_status(session: Session) -> None:
    """Decided with the user (2026-10-01): the page stays and its 상태 reads
    확인 필요, as the board does. Before, the sync skipped the item and Notion
    kept "진행 전"."""
    notion = FakeNotion()
    row = item(session)
    ref = sync(session, notion, row.id)
    assert ref is not None
    row.status = "needs_confirmation"

    sync(session, notion, row.id)

    assert len(notion.pages) == 1, "no second page"
    (page_id, properties), *_ = notion.updates
    assert page_id == ref.external_id
    assert properties["상태"] == {"select": {"name": "확인 필요"}}


def test_a_moved_back_item_whose_page_was_deleted_gets_no_new_page(session: Session) -> None:
    """A confirmed item whose page was deleted in Notion gets a new one (#403).
    A draft does not (#672): the team deleted that page, and no page is made
    for an item that is not confirmed. The ref row goes, so the item stops
    counting as one with a copy outside."""
    notion = FakeNotion()
    row = item(session)
    ref = sync(session, notion, row.id)
    assert ref is not None
    notion.deleted.add(ref.external_id)
    row.status = "needs_confirmation"

    assert sync(session, notion, row.id) is None

    assert len(notion.pages) == 1, "no second page"
    assert session.scalars(select(ExtExternalRef)).all() == []
    assert service.has_copy_outside(session, row.id) is False


def test_a_moved_back_item_whose_page_was_archived_keeps_its_ref(session: Session) -> None:
    """Archived is not gone: a person put it away and can take it back out."""
    notion = FakeNotion()
    row = item(session)
    ref = sync(session, notion, row.id)
    assert ref is not None
    notion.archived.add(ref.external_id)
    row.status = "needs_confirmation"

    assert sync(session, notion, row.id) is not None
    assert service.has_copy_outside(session, row.id) is True


def test_the_second_sync_of_an_item_updates_its_page_not_a_new_one(session: Session) -> None:
    """A confirmation delivered twice, or an item moved on to done: still one
    page, kept in step -- a later edit updates it rather than being silently
    skipped."""
    notion = FakeNotion()
    row = item(session)

    ref = sync(session, notion, row.id)
    assert ref is not None
    page_id = ref.external_id
    row.status = "done"

    again = sync(session, notion, row.id)

    assert again is not None
    assert again.action_item_id == ref.action_item_id
    assert len(notion.pages) == 1, "still one page created"
    assert len(notion.updates) == 1
    assert notion.updates[0][0] == page_id


def test_an_update_clears_a_field_the_edit_emptied(session: Session) -> None:
    """PARKJAEKYUNG0525's review of #342: Notion's PATCH overwrites only the
    properties it names, so an update that left the emptied due date and
    assignee off kept the old values on the page while the board showed none."""
    notion = FakeNotion()
    row = item(session)
    sync(session, notion, row.id)
    assert {"담당자", "마감일"} <= set(notion.pages[0][1])

    row.due_date = None
    row.assignee_label = None
    sync(session, notion, row.id)

    sent = notion.updates[0][1]
    assert sent["마감일"] == {"date": None}
    assert sent["담당자"] == {"rich_text": []}


def test_an_edit_to_an_item_whose_page_was_deleted_makes_a_new_page(session: Session) -> None:
    """#403: someone deleted the page in Notion. Every later update was refused
    and rolled back, so the item never reached Notion again. The edit now makes
    a new page in the team's database and the ref points at it."""
    notion = FakeNotion()
    row = item(session)
    ref = sync(session, notion, row.id)
    assert ref is not None
    old_page = ref.external_id
    assert old_page is not None
    notion.deleted.add(old_page)

    row.due_date = None
    again = sync(session, notion, row.id)

    assert again is not None
    assert len(notion.pages) == 2
    assert again.external_id == "page_2"
    assert again.url == service.notion_url("page_2")
    assert "마감일" not in notion.pages[1][1], "a new page has no stale field to clear"
    assert notion.updates == []

    row.status = "done"
    sync(session, notion, row.id)
    assert notion.updates[0][0] == "page_2", "later edits go to the new page"


def test_an_edit_to_an_item_whose_page_was_archived_leaves_it_archived(session: Session) -> None:
    """PARKJAEKYUNG0525, review of #404: a person archives a finished item's
    page to tidy the database. The next edit must not bring it back as a new
    page -- and must not fail the sync either, or every edit after it would."""
    notion = FakeNotion()
    row = item(session)
    ref = sync(session, notion, row.id)
    assert ref is not None
    old_page = ref.external_id
    assert old_page is not None
    notion.archived.add(old_page)
    heard: list[str] = []

    row.status = "done"
    again = sync(session, notion, row.id, on_page=heard.append)

    assert again is not None
    assert len(notion.pages) == 1, "no second page"
    assert again.external_id == old_page
    assert heard == ["archived"]


def test_a_refusal_whose_page_cannot_be_looked_up_raises_and_keeps_the_ref(
    session: Session,
) -> None:
    """PARKJAEKYUNG0525, review of #404: the update is refused and so is the
    look-up (a revoked token). Nothing is made, the ref still points where it
    did, and the error reaches the caller, which rolls back."""

    class RevokedAfterCreate(FakeNotion):
        def update_page(self, page_id: str, properties: dict) -> None:
            raise PermanentIntegrationError("notion rejected the request with 401")

        def page_state(self, page_id: str) -> str:
            raise PermanentIntegrationError(
                "notion rejected the request with 401", upstream_status=401
            )

    notion = RevokedAfterCreate()
    row = item(session)
    ref = sync(session, notion, row.id)
    assert ref is not None
    old_page = ref.external_id

    with pytest.raises(PermanentIntegrationError):
        sync(session, notion, row.id)

    assert len(notion.pages) == 1
    assert ref.external_id == old_page


def test_a_refused_update_of_a_page_that_still_exists_makes_no_second_page(
    session: Session,
) -> None:
    """#403's guard: a refusal that is not a missing page (a bad property, a
    conflict) raises as before. A new page for one that still exists would
    leave two."""

    class RefusingUpdates(FakeNotion):
        def update_page(self, page_id: str, properties: dict) -> None:
            raise PermanentIntegrationError("notion rejected the request with 400")

    notion = RefusingUpdates()
    row = item(session)
    ref = sync(session, notion, row.id)
    assert ref is not None
    old_page = ref.external_id

    with pytest.raises(PermanentIntegrationError):
        sync(session, notion, row.id)

    assert len(notion.pages) == 1
    assert ref.external_id == old_page


def test_a_sync_holding_the_ref_lock_sends_the_edit_committed_after_it_started(
    session: Session,
) -> None:
    """lsh2217's second-round review of #342: the ref-row lock only orders
    *when* each sync is let past it, not *which* version of the item it
    already has in hand -- a sync that read the item before the lock and
    never re-reads would still send that stale copy even though it is the
    one sending last. Simulated with two independent sessions sharing one
    in-memory database: session A reads the item before doing anything else
    (mirroring the pre-lock read a real race would have), session B
    independently commits a full edit-and-sync in between, and A's own sync
    call afterwards must still send B's committed description -- it can only
    do that by re-reading the item after acquiring the lock, not by reusing
    what it already had."""
    engine = session.get_bind()
    row = item(session)
    session.commit()

    with Session(engine) as session_a:
        stale = session_a.get(ExtActionItem, row.id)
        assert stale is not None and stale.description == "릴리스 노트 정리"

        with Session(engine) as session_b:
            edited = session_b.get(ExtActionItem, row.id)
            assert edited is not None
            edited.description = "최신 설명 (B가 커밋)"
            service.sync_action_item_to_notion(
                session_b, FakeNotion(), action_item_id=row.id, database_id=DATABASE
            )
            session_b.commit()

        notion_a = FakeNotion()
        service.sync_action_item_to_notion(
            session_a, notion_a, action_item_id=row.id, database_id=DATABASE
        )
        session_a.commit()

    assert notion_a.pages == [], "A finds B's claim already there -- it updates, not creates"
    assert len(notion_a.updates) == 1
    sent_title = notion_a.updates[0][1]["작업"]["title"][0]["text"]["content"]
    assert sent_title == "최신 설명 (B가 커밋)", "A must re-read, not send its own stale copy"


def test_a_claim_that_lands_mid_flight_gets_an_update_not_a_dropped_edit(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """lsh2217's second-round review of #342, from @mminjae97's finding:
    ``with_for_update`` only locks a row that exists, so a claim that lands
    *between* this sync's lock-miss (nothing there yet) and its own insert
    attempt is invisible to that first read -- the insert then conflicts and
    the old code returned ``None`` right there, silently never sending this
    edit at all. It must instead notice the conflict, acquire the lock on the
    row that beat it, and send an update.

    A real interleaving needs two transactions racing inside one function
    call, which a single-threaded test cannot produce on its own --
    monkeypatched here by making the *other* transaction's claim commit as a
    side effect of this sync reaching its own claim attempt, the same point
    in the real code where the race would land."""
    engine = session.get_bind()
    row = item(session)
    session.commit()

    real_insert_if_absent_into = service._insert_if_absent_into
    already_raced = False

    def racing_insert_if_absent_into(s: Session, model: type) -> object:
        nonlocal already_raced
        if not already_raced and model is ExtExternalRef:
            already_raced = True
            with Session(engine) as other:
                other.add(
                    ExtExternalRef(action_item_id=row.id, system="notion", meeting_id=MEETING)
                )
                other.commit()
                ref = other.get(ExtExternalRef, (row.id, "notion"))
                assert ref is not None
                ref.external_id = "page_from_other_worker"
                other.commit()
        return real_insert_if_absent_into(s, model)

    monkeypatch.setattr(service, "_insert_if_absent_into", racing_insert_if_absent_into)

    notion = FakeNotion()
    result = sync(session, notion, row.id)

    assert result is not None
    assert notion.pages == [], "no second create -- the race's claim already made the page"
    assert len(notion.updates) == 1
    assert notion.updates[0][0] == "page_from_other_worker"


def test_a_claim_race_onto_a_deleted_page_makes_a_new_page(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PARKJAEKYUNG0525, review of #404: the claim-race path updates too, so
    it recovers a deleted page the same way. Same race as the test above."""
    engine = session.get_bind()
    row = item(session)
    session.commit()

    real_insert_if_absent_into = service._insert_if_absent_into
    already_raced = False

    def racing_insert_if_absent_into(s: Session, model: type) -> object:
        nonlocal already_raced
        if not already_raced and model is ExtExternalRef:
            already_raced = True
            with Session(engine) as other:
                other.add(
                    ExtExternalRef(
                        action_item_id=row.id,
                        system="notion",
                        meeting_id=MEETING,
                        external_id="page_from_other_worker",
                    )
                )
                other.commit()
        return real_insert_if_absent_into(s, model)

    monkeypatch.setattr(service, "_insert_if_absent_into", racing_insert_if_absent_into)

    notion = FakeNotion(deleted={"page_from_other_worker"})
    heard: list[str] = []
    result = sync(session, notion, row.id, on_page=heard.append)

    assert result is not None
    assert heard == ["replaced"]
    assert result.external_id == "page_1"


def test_a_failed_call_takes_the_claim_back_so_a_later_sync_can_send(session: Session) -> None:
    row = item(session)
    session.commit()

    class Refusing(FakeNotion):
        def create_page(self, database_id: str, properties: dict) -> str:
            raise PermanentIntegrationError("notion rejected the page: unknown property")

    with pytest.raises(PermanentIntegrationError):
        sync(session, Refusing(), row.id)
    session.rollback()

    notion = FakeNotion()
    sync(session, notion, row.id)
    assert len(notion.pages) == 1


def test_a_gone_item_sends_nothing(session: Session) -> None:
    assert sync(session, FakeNotion(), "act_missing") is None


# --- the board's edit is the trigger ---------------------------------------------


@pytest.fixture
def client(session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    sign_in(app, session)
    yield TestClient(app)


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(tasks, "sync_after_confirmation", calls.append)
    return calls


def test_confirming_and_later_edits_each_queue_a_sync(
    client: TestClient, session: Session, queued: list[str]
) -> None:
    """The first queues a create; every edit after, while still confirmed,
    queues an update -- ``sync_action_item_to_notion`` itself decides which."""
    row = item(session, status="needs_confirmation")

    client.patch(f"{PREFIX}/action-items/{row.id}", json={"status": "todo"})
    client.patch(f"{PREFIX}/action-items/{row.id}", json={"status": "done"})
    client.patch(f"{PREFIX}/action-items/{row.id}", json={"description": "고친 설명"})

    assert queued == [row.id, row.id, row.id]


def test_an_edit_that_keeps_the_item_unconfirmed_queues_nothing(
    client: TestClient, session: Session, queued: list[str]
) -> None:
    row = item(session, status="needs_confirmation")

    client.patch(f"{PREFIX}/action-items/{row.id}", json={"assignee_label": "박디자인"})

    assert queued == []


def test_moving_a_sent_item_back_to_confirmation_queues_its_update(
    client: TestClient, session: Session, queued: list[str]
) -> None:
    """Its page shows 확인 필요 (decided with the user, 2026-10-01)."""
    row = item(session)
    session.add(
        ExtExternalRef(
            action_item_id=row.id, system="notion", meeting_id=row.meeting_id, external_id="p1"
        )
    )
    session.commit()

    client.patch(f"{PREFIX}/action-items/{row.id}", json={"status": "needs_confirmation"})

    assert queued == [row.id]


def test_a_notion_failure_does_not_fail_the_confirmation(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(_: str) -> None:
        raise PermanentIntegrationError("notion rejected the page")

    monkeypatch.setattr(tasks, "sync_action_item", refused)
    row = item(session, status="needs_confirmation")

    response = client.patch(f"{PREFIX}/action-items/{row.id}", json={"status": "todo"})

    assert response.status_code == 200
    assert session.get(ExtActionItem, row.id).status == "todo"  # type: ignore[union-attr]


def test_a_privacy_guard_block_does_not_crash_the_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``check_outbound`` raises ``PrivacyViolationError``, a sibling of
    ``IntegrationError`` -- not caught by the same except clause. Left
    uncaught, this would crash the FastAPI background task the confirming
    request already returned from (review of #333)."""

    def blocked(_: str) -> None:
        raise PrivacyViolationError("notion: phone number pattern found")

    monkeypatch.setattr(tasks, "sync_action_item", blocked)

    tasks.sync_after_confirmation("act_1")  # must not raise


# --- the task: the team's own Notion, or nothing ----------------------------------


@pytest.fixture
def wired(session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    monkeypatch.setattr(tasks, "session_scope", scope)
    return session


def test_a_team_whose_notion_has_no_action_database_is_skipped(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Connected for decisions only, or a token that is gone: skipped, not a 422
    out of the request that confirmed the item. Raised in review of #294."""
    row = item(wired)
    for config in (
        IntegrationConfig(service="notion", team_id="team_1", secret="t", config={}),
        IntegrationConfig(
            service="notion", team_id="team_1", secret=None, config={"action_db_id": "db"}
        ),
    ):
        monkeypatch.setattr(tasks, "load_integration", lambda _s, _t, _n, c=config: c)
        tasks.sync_action_item(row.id)

    assert wired.scalars(select(ExtExternalRef)).all() == []


def test_the_sync_task_does_not_retry_itself() -> None:
    """A timeout can mean the page was made and the answer lost; a retry then
    makes a second one. Raised in review of #294."""
    assert not getattr(tasks.sync_action_item, "autoretry_for", ())
    assert not getattr(tasks.sync_decision, "autoretry_for", ())


def test_a_team_without_notion_is_skipped_not_failed(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks, "load_integration", lambda *_: None)
    row = item(wired)

    tasks.sync_action_item(row.id)

    assert wired.scalars(select(ExtExternalRef)).all() == []


def test_the_task_uses_the_teams_token_and_database(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    notion = FakeNotion()
    tokens: list[str] = []

    def client_for(token: str) -> FakeNotion:
        tokens.append(token)
        return notion

    config = IntegrationConfig(
        service="notion",
        team_id="team_1",
        secret="secret-token",
        config={"action_db_id": "db_team_1"},
    )
    monkeypatch.setattr(tasks, "load_integration", lambda _s, team_id, name: config)
    monkeypatch.setattr(tasks, "NotionClient", client_for)
    row = item(wired)

    tasks.sync_action_item(row.id)
    tasks.sync_action_item(row.id)

    assert tokens == ["secret-token", "secret-token"]
    assert [database for database, _ in notion.pages] == ["db_team_1"]


# --- deleting an item trashes its page (#467, option B) -------------------------------


def test_deleting_an_item_moves_its_page_to_notions_trash(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from .conftest import TRASH_NOTION_PAGE

    class Closable(FakeNotion):
        def close(self) -> None:
            pass

    notion = Closable()
    config = IntegrationConfig(
        service="notion", team_id="team_1", secret="t", config={"action_db_id": DATABASE}
    )
    monkeypatch.setattr(tasks, "load_integration", lambda _s, _t, _n: config)
    monkeypatch.setattr(tasks, "NotionClient", lambda token: notion)
    monkeypatch.setattr(tasks, "trash_notion_page", TRASH_NOTION_PAGE)
    row = item(wired)
    tasks.sync_action_item(row.id)
    ref = wired.get(ExtExternalRef, (row.id, "notion"))
    assert ref is not None

    tasks.trash_notion_page(row.id)

    assert notion.page_state(str(ref.external_id)) == "archived"  # in the trash, restorable


def test_an_unreachable_notion_never_blocks_a_deletion(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from .conftest import TRASH_NOTION_PAGE

    def down(_s: object, _t: str, _n: str) -> None:
        raise PermanentIntegrationError("notion is down")

    row = item(wired)
    wired.add(
        ExtExternalRef(action_item_id=row.id, system="notion", meeting_id=MEETING, external_id="p1")
    )
    wired.flush()
    monkeypatch.setattr(tasks, "load_integration", down)
    monkeypatch.setattr(tasks, "trash_notion_page", TRASH_NOTION_PAGE)

    tasks.trash_notion_page(row.id)  # must not raise
