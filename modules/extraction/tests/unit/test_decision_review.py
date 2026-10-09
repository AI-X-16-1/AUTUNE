"""Nothing about a decision leaves before a person confirms it (#246).

SQLite in memory and the router on a bare app, as ``test_read_endpoints`` does.
The rules under test: a decision starts pending, a verdict and a rewording are
kept, a rebuild keeps a decision a person marked while its lines can be read and
replaces one nobody marked, and the outbound list carries exactly what was
confirmed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_contracts.enums import UtteranceKind
from autune_core import (
    AutuneError,
    Base,
    Meeting,
    Participant,
    PrivacyViolationError,
    TeamMember,
    Utterance,
    get_session,
)
from autune_core.integrations_config import IntegrationConfig
from autune_extraction import service, tasks
from autune_extraction.config import ExtractionSettings
from autune_extraction.confirmations import WEAK_ASSENT
from autune_extraction.decisions import ClassifiedUtterance, decision_id
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtCalendarEvent,
    ExtClassification,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionRef,
    ExtDecisionRelated,
    ExtDecisionReview,
    ExtDecisionSource,
    ExtEditEvent,
    ExtExternalRef,
    ExtNotionTarget,
    ExtSyncFailure,
)
from autune_extraction.pipeline.base import Resolution
from autune_extraction.router import router
from autune_extraction.schemas import DecisionReviewUpdate
from autune_integrations.errors import TransientIntegrationError
from autune_integrations.fakes import FakeNotion

from .conftest import sign_in

MEETING = "mtg_1"
PREFIX = "/api/extraction"
K = UtteranceKind

TABLES = [
    Meeting.__table__,
    TeamMember.__table__,
    Participant.__table__,
    Utterance.__table__,
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtClassification.__table__,
    ExtDecision.__table__,
    ExtDecisionRelated.__table__,
    ExtDecisionRef.__table__,
    ExtDecisionSource.__table__,
    ExtDecisionReview.__table__,
    ExtConfirmation.__table__,
    ExtEditEvent.__table__,
    ExtExternalRef.__table__,
    # Every read of an item looks these up (#680): its failed copies, its event.
    ExtCalendarEvent.__table__,
    ExtSyncFailure.__table__,
    ExtNotionTarget.__table__,
]


@pytest.fixture(autouse=True)
def notion_syncs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Confirming a decision starts its Notion sync after the response (#30).
    Here that is recorded, not run: the sync opens its own session on the real
    database, and ``test_notion_sync.py`` is where it is tested."""
    calls: list[str] = []
    monkeypatch.setattr(tasks, "sync_decision_after_confirmation", calls.append)
    return calls


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
    sign_in(app, session)
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


def test_a_confirmed_decisions_notion_status_reaches_the_review_screen(
    client: TestClient, session: Session
) -> None:
    """S15 has no drawer, so `sync_refs` rides the list here too -- the same
    call `ActionItemRead.sync_refs` makes."""
    first, second = two_decisions(session)
    session.add(
        ExtDecisionRef(
            decision_id=first.id,
            system="notion",
            meeting_id=MEETING,
            url="https://www.notion.so/page1",
            external_id="page1",
        )
    )
    session.flush()

    body = client.get(f"{PREFIX}/reviews/{MEETING}").json()

    by_id = {d["id"]: d for d in body["decisions"]}
    assert by_id[first.id]["sync_refs"] == [
        {"system": "notion", "url": "https://www.notion.so/page1", "external_id": "page1"}
    ]
    assert by_id[second.id]["sync_refs"] == []


def test_a_decisions_summary_is_its_longest_source_utterance(
    client: TestClient, session: Session
) -> None:
    """Rule-based prototype (not a model): the point of a summary here is a
    preview of the *sources*, distinct from the assembled ``statement`` --
    checked against the drawer's full evidence, never generated prose."""
    for uid, text in [
        ("utt_1", "네"),
        ("utt_2", "일정이 밀리면 다음 주 화요일로 옮기는 게 낫겠어요"),
        ("utt_3", "좋아요 그렇게 하죠"),
    ]:
        session.execute(Utterance.__table__.update().where(Utterance.id == uid).values(text=text))
    session.commit()
    service.build_decisions(
        session,
        meeting_id=MEETING,
        utterances=[
            ClassifiedUtterance(id="utt_1", kind=K.DECISION, confidence=0.9, text="네"),
            ClassifiedUtterance(
                id="utt_2",
                kind=K.DECISION,
                confidence=0.9,
                text="일정이 밀리면 다음 주 화요일로 옮기는 게 낫겠어요",
            ),
            ClassifiedUtterance(
                id="utt_3", kind=K.DECISION, confidence=0.9, text="좋아요 그렇게 하죠"
            ),
            *(
                ClassifiedUtterance(id=f"utt_{i}", kind=None, confidence=0.0, text="")
                for i in range(4, 9)
            ),
        ],
    )

    body = client.get(f"{PREFIX}/reviews/{MEETING}").json()

    (decision,) = body["decisions"]
    assert decision["summary"] == "일정이 밀리면 다음 주 화요일로 옮기는 게 낫겠어요"


def test_a_decisions_detail_quotes_its_sources_in_spoken_order(
    client: TestClient, session: Session
) -> None:
    """The list carries ids and one preview line; the whole quotation comes from
    the row's own endpoint, oldest utterance first, so the person confirming the
    noun-ended line reads what was said beneath it."""
    for uid, text in [("utt_2", "A안으로 진행합시다"), ("utt_3", "네 그렇게 하죠")]:
        session.execute(Utterance.__table__.update().where(Utterance.id == uid).values(text=text))
    session.commit()
    service.build_decisions(
        session,
        meeting_id=MEETING,
        utterances=[
            ClassifiedUtterance(
                id="utt_2", kind=K.DECISION, confidence=0.9, text="A안으로 진행합시다"
            ),
            ClassifiedUtterance(id="utt_3", kind=K.DECISION, confidence=0.9, text="네 그렇게 하죠"),
            *(
                ClassifiedUtterance(id=f"utt_{i}", kind=None, confidence=0.0, text="")
                for i in (1, 4, 5, 6, 7, 8)
            ),
        ],
    )
    (listed,) = client.get(f"{PREFIX}/reviews/{MEETING}").json()["decisions"]

    detail = client.get(f"{PREFIX}/decisions/{listed['id']}").json()

    assert "sources" not in listed
    assert [s["text"] for s in detail["sources"]] == ["A안으로 진행합시다", "네 그렇게 하죠"]
    assert detail["statement"] == listed["statement"] == "A안으로 진행함"


def test_a_decision_a_person_added_has_no_sources(client: TestClient, session: Session) -> None:
    created = client.post(
        f"{PREFIX}/decisions", json={"meeting_id": MEETING, "statement": "예산 동결"}
    ).json()

    detail = client.get(f"{PREFIX}/decisions/{created['id']}").json()

    assert detail["sources"] == []


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


def test_a_rejected_decision_leaves_what_d_and_e_read(client: TestClient, session: Session) -> None:
    """``ExtractionResult`` is what D and E consume and ``GET /results`` returns;
    a decision a person rejected is in neither. A pending one still is -- when D
    and E hear an unreviewed decision is #246's question 2. Raised in review of
    #247."""
    first, second = two_decisions(session)

    client.patch(f"{PREFIX}/decisions/{first.id}", json={"status": "rejected"})

    assert [d.id for d in service.result_for_meeting(session, MEETING).decisions] == [second.id]
    assert [d["id"] for d in client.get(f"{PREFIX}/results/{MEETING}").json()["decisions"]] == [
        second.id
    ]


def test_the_wording_a_person_confirmed_is_what_d_and_e_read(
    client: TestClient, session: Session
) -> None:
    """The rewording goes to Notion through ``outbound_for_meeting``; it has to
    reach D and E the same way, or one decision has two texts. Raised in review
    of #247."""
    first, _ = two_decisions(session)

    client.patch(
        f"{PREFIX}/decisions/{first.id}",
        json={"status": "confirmed", "statement": "출시는 금요일로 확정"},
    )

    result = service.result_for_meeting(session, MEETING)
    sent = next(d for d in result.decisions if d.id == first.id)
    assert sent.statement == "출시는 금요일로 확정"
    outbound = client.get(f"{PREFIX}/reviews/{MEETING}/outbound").json()
    assert [d["statement"] for d in outbound["decisions"]] == ["출시는 금요일로 확정"]


def test_a_rejection_taken_back_returns_the_decision(client: TestClient, session: Session) -> None:
    first, second = two_decisions(session)

    client.patch(f"{PREFIX}/decisions/{first.id}", json={"status": "rejected"})
    client.patch(f"{PREFIX}/decisions/{first.id}", json={"status": "pending"})

    assert {d.id for d in service.result_for_meeting(session, MEETING).decisions} == {
        first.id,
        second.id,
    }


def test_rejecting_by_patch_drops_the_rewording_the_way_delete_does(
    client: TestClient, session: Session
) -> None:
    """A status-only PATCH used to leave the old rewording in the row, so undoing
    the rejection brought back wording nobody typed this time. Raised in review
    of #247."""
    first, _ = two_decisions(session)
    model_wording = first.statement

    client.patch(
        f"{PREFIX}/decisions/{first.id}",
        json={"status": "confirmed", "statement": "출시는 금요일로 확정"},
    )
    client.patch(f"{PREFIX}/decisions/{first.id}", json={"status": "rejected"})
    client.patch(f"{PREFIX}/decisions/{first.id}", json={"status": "pending"})

    result = service.result_for_meeting(session, MEETING)
    assert next(d for d in result.decisions if d.id == first.id).statement == model_wording


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


def test_a_decision_nobody_marked_whose_lines_are_grouped_differently_is_replaced(
    session: Session,
) -> None:
    """Still a rebuild for a row no person touched: a pending row has nothing to lose."""
    first_id = two_decisions(session)[0].id

    rebuilt = service.build_decisions(
        session, meeting_id=MEETING, utterances=labelled({3: K.DECISION, 7: K.DECISION})
    )

    assert first_id not in {decision.id for decision in rebuilt}
    assert session.get(ExtDecision, first_id) is None


# --- a row a person marked is kept through a rerun (2026-10-09) ---------------------

LOGIN = "로그인은 소셜 로그인만 지원하기로 했습니다"
PRICE = "가격은 월 9900원으로 갑니다"
SEARCH = "검색 기능은 다음 분기로 미루기로 했습니다"
ASSENT = "네 그렇게 하죠"


def spoken(
    session: Session, texts: dict[int, str], kinds: dict[int, UtteranceKind]
) -> list[ClassifiedUtterance]:
    """The meeting's eight utterances as a run reads them, with these texts
    stored first -- module A writes a line before B is told of it."""
    for number, text in texts.items():
        stored = session.get(Utterance, f"utt_{number}")
        assert stored is not None
        stored.text = text
    session.flush()
    return [
        ClassifiedUtterance(
            id=f"utt_{i}",
            kind=kinds.get(i),
            confidence=0.9,
            text=session.get(Utterance, f"utt_{i}").text,  # type: ignore[union-attr]
        )
        for i in range(1, 9)
    ]


def run(session: Session, read: list[ClassifiedUtterance]) -> list[ExtDecision]:
    """The two steps of a run that touch a decision, in the run's order
    (``tasks._extract``): the rebuild, then what follows a corrected line."""
    built = service.build_decisions(session, meeting_id=MEETING, utterances=read)
    service.apply_source_corrections(
        session, meeting_id=MEETING, spoken={u.id: u.text for u in read if u.text}
    )
    session.expire_all()
    return built


def welded(session: Session, *numbers: int, statement: str = "가격은 월 9900원으로 진행") -> str:
    """A row as the grouping made it before 2026-10-09: one decision of several
    lines that each say something, with the digest a run leaves on it."""
    ids = [f"utt_{number}" for number in numbers]
    row = ExtDecision(
        id=decision_id(MEETING, ids),
        meeting_id=MEETING,
        statement=statement,
        original_statement=PRICE,
        confidence=0.9,
        origin="model",
    )
    session.add(row)
    session.add_all(
        ExtDecisionSource(decision_id=row.id, utterance_id=utterance_id, position=position)
        for position, utterance_id in enumerate(ids)
    )
    session.flush()
    texts = {u.id: u.text for u in session.scalars(select(Utterance))}
    service.apply_source_corrections(session, meeting_id=MEETING, spoken=texts)
    return row.id


WRAP_UP = {2: LOGIN, 3: PRICE, 4: ASSENT, 7: SEARCH}
ALL_DECISIONS = dict.fromkeys(WRAP_UP, K.DECISION)


def rows(session: Session) -> dict[str, str]:
    return {d.id: d.statement for d in session.scalars(select(ExtDecision))}


def test_a_welded_decision_nobody_marked_is_split_at_the_next_run(session: Session) -> None:
    read = spoken(session, WRAP_UP, ALL_DECISIONS)
    old = welded(session, 2, 3)

    run(session, read)

    assert rows(session) == {
        decision_id(MEETING, ["utt_2"]): "로그인은 소셜 로그인만 지원하기로 함",
        decision_id(MEETING, ["utt_3", "utt_4"]): "가격은 월 9900원으로 진행",
        decision_id(MEETING, ["utt_7"]): "검색 기능은 다음 분기로 미루기로 함",
    }
    assert old not in rows(session)


@pytest.mark.parametrize(
    "mark",
    [{"status": "confirmed"}, {"status": "rejected"}, {"statement": "가격은 월 9,900원"}],
    ids=["confirmed", "rejected", "reworded"],
)
def test_a_decision_a_person_marked_stays_as_it_is_however_its_lines_are_grouped_now(
    client: TestClient, session: Session, mark: dict[str, str]
) -> None:
    """The owner's rule (2026-10-09): what a person confirmed, rejected or
    reworded keeps its id, its sentence and its review while the lines it was
    made from are there. New rows are made only from lines no such row holds:
    not from its own two, and not from the assent that followed them."""
    read = spoken(session, WRAP_UP, ALL_DECISIONS)
    old = welded(session, 2, 3)
    assert client.patch(f"{PREFIX}/decisions/{old}", json=mark).status_code == 200
    review = session.get(ExtDecisionReview, old)
    assert review is not None
    before = (review.status, review.statement)

    for _ in range(2):  # and the run after that finds the same rows
        built = run(session, read)

        assert rows(session) == {
            old: "가격은 월 9900원으로 진행",
            decision_id(MEETING, ["utt_7"]): "검색 기능은 다음 분기로 미루기로 함",
        }
        assert {decision.id for decision in built} == set(rows(session))
        kept = session.get(ExtDecisionReview, old)
        assert kept is not None and (kept.status, kept.statement) == before
        sources = session.scalars(
            select(ExtDecisionSource.utterance_id)
            .where(ExtDecisionSource.decision_id == old)
            .order_by(ExtDecisionSource.position)
        )
        assert list(sources) == ["utt_2", "utt_3"]


def test_a_decision_put_back_to_pending_is_rebuilt_like_one_nobody_marked(
    client: TestClient, session: Session
) -> None:
    """The review row is still there, saying nothing: no verdict, no rewording."""
    read = spoken(session, WRAP_UP, ALL_DECISIONS)
    old = welded(session, 2, 3)
    client.patch(f"{PREFIX}/decisions/{old}", json={"status": "confirmed"})
    client.patch(f"{PREFIX}/decisions/{old}", json={"status": "pending"})
    assert session.get(ExtDecisionReview, old) is not None

    run(session, read)

    assert old not in rows(session)
    assert len(rows(session)) == 3


def test_a_confirmed_sentence_is_not_written_again_by_a_later_run(
    client: TestClient, session: Session
) -> None:
    """Also when the row's id is one the run would make anyway. Before, the
    upsert put the run's sentence under the confirmation."""
    read = spoken(session, {2: LOGIN}, {2: K.DECISION})
    first = run(session, read)[0]
    first.statement, first.statement_resolved = "소셜 로그인만 지원함", True
    session.flush()
    client.patch(f"{PREFIX}/decisions/{first.id}", json={"status": "confirmed"})

    run(session, read)

    kept = session.get(ExtDecision, first.id)
    assert kept is not None
    assert (kept.statement, kept.statement_resolved) == ("소셜 로그인만 지원함", True)


def test_a_kept_decision_follows_a_line_corrected_since(
    client: TestClient, session: Session
) -> None:
    """#586 still holds for a kept row: the model's text is read again from the
    row's own lines, so a word masked since is not kept in it, and a person's
    rewording is flagged. The id and the review stay."""
    read = spoken(session, WRAP_UP, ALL_DECISIONS)
    old = welded(session, 2, 3)
    client.patch(
        f"{PREFIX}/decisions/{old}", json={"status": "confirmed", "statement": "가격은 월 9,900원"}
    )

    run(session, spoken(session, {3: "가격은 월 [금액]으로 갑니다"}, ALL_DECISIONS))

    kept = session.get(ExtDecision, old)
    assert kept is not None
    assert kept.statement == "가격은 월 [금액]으로 진행"
    assert kept.original_statement == "가격은 월 [금액]으로 갑니다"
    assert kept.needs_recheck
    review = session.get(ExtDecisionReview, old)
    assert review is not None and review.status == "confirmed"
    assert set(rows(session)) == {old, decision_id(MEETING, ["utt_7"])}
    assert read[2].text == PRICE, "the run before had read the line as it was"


def test_a_kept_decision_is_read_from_all_its_lines_whatever_they_are_called_now(
    client: TestClient, session: Session
) -> None:
    """The run that finds the corrected line no longer calls it a decision.
    The row is still the person's, and its sentence is still that line's."""
    spoken(session, WRAP_UP, ALL_DECISIONS)
    old = welded(session, 2, 3)
    client.patch(f"{PREFIX}/decisions/{old}", json={"status": "confirmed"})

    run(session, spoken(session, {3: "가격은 월 [금액]으로 갑니다"}, {2: K.DECISION}))

    assert rows(session) == {old: "가격은 월 [금액]으로 진행"}


def test_a_decision_confirmed_as_the_model_wrote_it_stays_confirmed_through_a_correction(
    client: TestClient, session: Session
) -> None:
    """Confirmed with no rewording, then a line of it is corrected: the row is
    still confirmed, with the sentence read again, and is not flagged for
    another look -- #586's rule for a confirmed decision, kept for a kept row
    (the owner, 2026-10-09). Only a person's own wording is flagged, because
    only there can B not tell which words were the private ones. The copy
    outside follows."""
    spoken(session, WRAP_UP, ALL_DECISIONS)
    old = welded(session, 2, 3)
    client.patch(f"{PREFIX}/decisions/{old}", json={"status": "confirmed"})
    read = spoken(session, {3: "가격은 월 [금액]으로 갑니다"}, ALL_DECISIONS)

    service.build_decisions(session, meeting_id=MEETING, utterances=read)
    after = service.apply_source_corrections(
        session, meeting_id=MEETING, spoken={u.id: u.text for u in read if u.text}
    )
    session.expire_all()

    kept = session.get(ExtDecision, old)
    assert kept is not None and kept.statement == "가격은 월 [금액]으로 진행"
    assert not kept.needs_recheck
    review = session.get(ExtDecisionReview, old)
    assert review is not None and review.status == "confirmed" and not review.statement
    assert old in after.changed_decisions and after.flagged == 0


def test_a_summary_written_for_a_kept_decisions_exact_lines_is_its_sentence(
    client: TestClient, session: Session
) -> None:
    """A kept row whose line was corrected is given the run's write-up when the
    run wrote one about the same lines -- here a row of one line, whose id the
    run makes anyway."""
    first = run(session, spoken(session, {2: LOGIN}, {2: K.DECISION}))[0].id
    client.patch(f"{PREFIX}/decisions/{first}", json={"status": "confirmed"})
    read = spoken(session, {2: "로그인은 [서비스] 로그인만 지원하기로 했습니다"}, {2: K.DECISION})

    service.build_decisions(
        session,
        meeting_id=MEETING,
        utterances=read,
        summaries={first: Resolution("[서비스] 로그인만 지원", used=())},
    )

    assert rows(session) == {first: "[서비스] 로그인만 지원"}


def test_a_kept_decision_goes_when_a_speaker_of_its_lines_no_longer_consents(
    client: TestClient, session: Session
) -> None:
    """A turn whose speaker has not consented reaches the rebuild with no text
    (``classify_utterances``). The row made from it is rebuilt like any other:
    it goes, with its review, and what is left is read as a new decision."""
    read = spoken(session, WRAP_UP, ALL_DECISIONS)
    old = welded(session, 2, 3)
    client.patch(f"{PREFIX}/decisions/{old}", json={"status": "confirmed"})
    read[2] = ClassifiedUtterance(id="utt_3", kind=None, confidence=0.0, text="")

    run(session, read)

    assert old not in rows(session)
    assert session.get(ExtDecisionReview, old) is None
    assert set(rows(session)) == {
        decision_id(MEETING, ["utt_2", "utt_4"]),
        decision_id(MEETING, ["utt_7"]),
    }
    assert PRICE not in " ".join(rows(session).values())


def test_a_kept_decision_goes_when_one_of_its_lines_was_deleted(
    client: TestClient, session: Session
) -> None:
    """A deleted utterance leaves its link with no id (``ExtDecisionSource``)."""
    read = spoken(session, WRAP_UP, ALL_DECISIONS)
    old = welded(session, 2, 3)
    client.patch(f"{PREFIX}/decisions/{old}", json={"status": "confirmed"})
    link = session.scalars(
        select(ExtDecisionSource).where(
            ExtDecisionSource.decision_id == old, ExtDecisionSource.utterance_id == "utt_3"
        )
    ).one()
    link.utterance_id = None
    session.flush()

    run(session, [u for u in read if u.id != "utt_3"])

    assert old not in rows(session)
    assert session.get(ExtDecisionReview, old) is None


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
    assert first_id not in {d.id for d in service.result_for_meeting(session, MEETING).decisions}


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


WITH_A_NUMBER = "담당 연락처 010-1234-5678 로 공유"


def as_stored_before_the_rule(session: Session, decision_id: str) -> None:
    """Put a number into a decision's wording the way no endpoint does any more.

    Typed text is screened when it is saved (#1130), so a rewording or a typed
    decision that holds a number is one stored before that rule -- or a line
    module A's masker missed. The outbound check is what still stands between
    such a row and a send, and these tests are about it. The number goes on the
    rewording of a model's decision and on the statement of one a person typed,
    which is where each keeps a person's words."""
    decision = session.get(ExtDecision, decision_id)
    review = session.get(ExtDecisionReview, decision_id)
    assert decision is not None and review is not None
    if decision.origin == "user":
        decision.statement = WITH_A_NUMBER
    else:
        review.statement = WITH_A_NUMBER
    session.flush()


def reworded_before_the_rule(client: TestClient, session: Session, decision_id: str) -> None:
    """A pending decision of the model's whose rewording holds a number."""
    client.patch(f"{PREFIX}/decisions/{decision_id}", json={"statement": "담당 연락처로 공유"})
    as_stored_before_the_rule(session, decision_id)


def typed_before_the_rule(client: TestClient, session: Session) -> str:
    """A decision a person typed, confirmed as every typed one is, holding a number."""
    typed = client.post(
        f"{PREFIX}/decisions", json={"meeting_id": MEETING, "statement": "연락처 공유"}
    ).json()
    as_stored_before_the_rule(session, typed["id"])
    return str(typed["id"])


def test_a_confirmed_rewording_with_personal_data_is_held_back_by_category(
    client: TestClient, session: Session
) -> None:
    """A rewording is typed by a person and never passed module A's masker."""
    first_id, second_id = (d.id for d in two_decisions(session))
    reworded_before_the_rule(client, session, first_id)
    client.patch(f"{PREFIX}/decisions/{first_id}", json={"status": "confirmed"})
    client.patch(f"{PREFIX}/decisions/{second_id}", json={"status": "confirmed"})

    response = client.get(f"{PREFIX}/reviews/{MEETING}/outbound")
    outbound = response.json()

    assert [d["id"] for d in outbound["decisions"]] == [second_id]
    assert outbound["blocked"] == [{"id": first_id, "kind": "decision", "categories": ["phone"]}]
    assert "1234-5678" not in response.text


def _rows(client: TestClient) -> dict[str, dict]:
    return {d["id"]: d for d in client.get(f"{PREFIX}/reviews/{MEETING}").json()["decisions"]}


def test_a_decision_the_outbound_check_refuses_says_so_on_its_row(
    client: TestClient, session: Session
) -> None:
    """The copy to Notion was refused by the client's check and the person was
    told nothing: a warning in the log, by id. The row now says it, from the
    same check, and names neither the value nor its category."""
    first, second = two_decisions(session)
    reworded_before_the_rule(client, session, first.id)
    answer = client.patch(f"{PREFIX}/decisions/{first.id}", json={"status": "confirmed"})
    client.patch(f"{PREFIX}/decisions/{second.id}", json={"status": "confirmed"})

    # What the send does with each: the real check, on the real request body.
    notion = FakeNotion()
    with pytest.raises(PrivacyViolationError):
        service.sync_decision_to_notion(
            session, notion, decision_id=first.id, database_id="db_decisions"
        )
    assert notion.pages == []
    assert (
        service.sync_decision_to_notion(
            session, notion, decision_id=second.id, database_id="db_decisions"
        )
        is not None
    )

    assert answer.json()["held_back"] is True, "said at once, in the answer to the verdict"
    rows = _rows(client)
    assert rows[first.id]["held_back"] is True
    assert rows[second.id]["held_back"] is False
    assert "categories" not in rows[first.id] and "phone" not in str(
        {k: v for k, v in rows[first.id].items() if k != "statement"}
    )


def test_the_row_stops_saying_so_once_it_is_reworded_and_its_page_then_goes(
    client: TestClient, session: Session
) -> None:
    first, _ = two_decisions(session)
    reworded_before_the_rule(client, session, first.id)
    client.patch(f"{PREFIX}/decisions/{first.id}", json={"status": "confirmed"})

    answer = client.patch(
        f"{PREFIX}/decisions/{first.id}", json={"statement": "담당자 연락처는 따로 공유"}
    )

    assert answer.json()["held_back"] is False
    assert _rows(client)[first.id]["held_back"] is False
    notion = FakeNotion()
    service.sync_decision_to_notion(
        session, notion, decision_id=first.id, database_id="db_decisions"
    )
    assert len(notion.pages) == 1


def test_only_a_confirmed_decision_is_said_to_be_held_back(
    client: TestClient, session: Session
) -> None:
    """Nothing unconfirmed is sent anyway (#246): a pending or rejected row with
    the same text was not held back by this, and does not say it was."""
    first, second = two_decisions(session)
    reworded_before_the_rule(client, session, first.id)
    typed = typed_before_the_rule(client, session)

    rows = _rows(client)
    assert (rows[first.id]["status"], rows[first.id]["held_back"]) == ("pending", False)
    assert rows[second.id]["held_back"] is False
    assert rows[typed]["held_back"] is True, (
        "a typed decision is confirmed from the moment it exists"
    )

    put_back = client.patch(f"{PREFIX}/decisions/{typed}", json={"status": "pending"})
    assert put_back.json()["held_back"] is False


def test_the_rows_that_say_so_are_the_held_back_list(client: TestClient, session: Session) -> None:
    """One check, read two ways: the row's mark and ``blocked`` never disagree."""
    first, second = two_decisions(session)
    reworded_before_the_rule(client, session, first.id)
    client.patch(f"{PREFIX}/decisions/{first.id}", json={"status": "confirmed"})
    client.patch(f"{PREFIX}/decisions/{second.id}", json={"status": "confirmed"})
    typed_before_the_rule(client, session)

    marked = {id_ for id_, row in _rows(client).items() if row["held_back"]}
    blocked = client.get(f"{PREFIX}/reviews/{MEETING}/outbound").json()["blocked"]

    assert len(marked) == 2
    assert marked == {b["id"] for b in blocked if b["kind"] == "decision"}


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


# --- a confirmed decision's Notion page (#30) ------------------------------------


def test_confirming_and_rewording_a_decision_each_queue_a_sync(
    client: TestClient, session: Session, notion_syncs: list[str]
) -> None:
    """The first queues a create; every edit after, while still confirmed,
    queues an update -- ``sync_decision_to_notion`` itself decides which,
    from whether the claim already exists. Rejecting queues nothing."""
    first, second = two_decisions(session)

    client.patch(f"{PREFIX}/decisions/{first.id}", json={"status": "confirmed"})
    client.patch(f"{PREFIX}/decisions/{first.id}", json={"status": "confirmed"})
    client.patch(f"{PREFIX}/decisions/{first.id}", json={"statement": "고친 문장"})
    client.patch(f"{PREFIX}/decisions/{second.id}", json={"status": "rejected"})

    assert notion_syncs == [first.id, first.id, first.id]


def test_a_decision_a_person_adds_is_queued_because_it_is_confirmed(
    client: TestClient, session: Session, notion_syncs: list[str]
) -> None:
    added = client.post(
        f"{PREFIX}/decisions", json={"meeting_id": MEETING, "statement": "회고는 격주로"}
    ).json()

    assert notion_syncs == [added["id"]]


def test_the_page_carries_the_confirmed_wording_and_no_quotation(session: Session) -> None:
    first, second = two_decisions(session)
    service.review_decision(
        session, first, DecisionReviewUpdate(status="confirmed", statement="출시는 금요일로 확정")
    )
    service.review_decision(session, second, DecisionReviewUpdate(status="confirmed"))
    notion = FakeNotion()

    ref = service.sync_decision_to_notion(
        session, notion, decision_id=first.id, database_id="db_decisions"
    )

    assert ref is not None and ref.url == "https://www.notion.so/page_1"
    database, properties = notion.pages[0]
    assert database == "db_decisions"
    assert properties["결정"] == {
        "title": [{"type": "text", "text": {"content": "출시는 금요일로 확정"}}]
    }
    assert properties["근거 발화 수"] == {"number": len(first.sources)}
    assert set(properties) == {"결정", "신뢰도", "근거 발화 수", "회의"}

    only_title = FakeNotion()
    service.sync_decision_to_notion(
        session,
        only_title,
        decision_id=second.id,
        database_id="db_decisions",
        property_names={"title": "Name"},
    )
    assert set(only_title.pages[0][1]) == {"Name"}


def test_an_unconfirmed_or_rejected_decision_sends_nothing(session: Session) -> None:
    first, second = two_decisions(session)
    notion = FakeNotion()

    assert (
        service.sync_decision_to_notion(session, notion, decision_id=first.id, database_id="db")
        is None
    )
    service.review_decision(session, second, DecisionReviewUpdate(status="rejected"))
    assert (
        service.sync_decision_to_notion(session, notion, decision_id=second.id, database_id="db")
        is None
    )
    assert notion.pages == []


def test_a_second_sync_of_a_confirmed_decision_updates_its_page(session: Session) -> None:
    """Not a second page -- the same one, kept in step with a later reword."""
    first, _second = two_decisions(session)
    notion = FakeNotion()

    service.review_decision(session, first, DecisionReviewUpdate(status="confirmed"))
    ref = service.sync_decision_to_notion(session, notion, decision_id=first.id, database_id="db")
    assert ref is not None
    page_id = ref.external_id

    again = service.sync_decision_to_notion(session, notion, decision_id=first.id, database_id="db")

    assert again is not None
    assert again.decision_id == ref.decision_id
    assert len(notion.pages) == 1, "still one page created"
    assert len(notion.updates) == 1
    assert notion.updates[0][0] == page_id


def test_a_reword_of_a_decision_whose_page_was_deleted_makes_a_new_page(
    session: Session,
) -> None:
    """#403 for decisions: the page is gone from Notion, so the reword makes a
    new one instead of being refused on every later sync."""
    first, _second = two_decisions(session)
    notion = FakeNotion()
    service.review_decision(session, first, DecisionReviewUpdate(status="confirmed"))
    ref = service.sync_decision_to_notion(session, notion, decision_id=first.id, database_id="db")
    assert ref is not None
    assert ref.external_id is not None
    notion.deleted.add(ref.external_id)

    again = service.sync_decision_to_notion(session, notion, decision_id=first.id, database_id="db")

    assert again is not None
    assert [database for database, _ in notion.pages] == ["db", "db"]
    assert again.external_id == "page_2"
    assert again.url == service.notion_url("page_2")


def test_a_decision_sync_holding_the_ref_lock_sends_the_reword_committed_after_it_started(
    session: Session,
) -> None:
    """lsh2217's second-round review of #342: ``sync_decision_to_notion`` had
    no ``with_for_update`` at all, so two rewordings in flight at once could
    reach Notion in whichever order the network delivered them rather than
    the order they committed in -- the exact bug fixed for action items, just
    missing here entirely. Same two-session simulation as the action-item
    version: session A reads the decision before doing anything else, session
    B commits a full reword-and-sync independently, and A's own sync
    afterwards must still send B's committed wording."""
    engine = session.get_bind()
    first, _second = two_decisions(session)
    service.review_decision(session, first, DecisionReviewUpdate(status="confirmed"))
    session.commit()

    with Session(engine) as session_a:
        stale = session_a.get(ExtDecision, first.id)
        assert stale is not None

        with Session(engine) as session_b:
            service.review_decision(
                session_b,
                session_b.get(ExtDecision, first.id),  # type: ignore[arg-type]
                DecisionReviewUpdate(statement="최신 문구 (B가 커밋)"),
            )
            service.sync_decision_to_notion(
                session_b, FakeNotion(), decision_id=first.id, database_id="db"
            )
            session_b.commit()

        notion_a = FakeNotion()
        service.sync_decision_to_notion(session_a, notion_a, decision_id=first.id, database_id="db")
        session_a.commit()

    assert notion_a.pages == [], "A finds B's claim already there -- it updates, not creates"
    assert len(notion_a.updates) == 1
    sent_title = notion_a.updates[0][1]["결정"]["title"][0]["text"]["content"]
    assert sent_title == "최신 문구 (B가 커밋)", "A must re-read, not send its own stale copy"


def test_a_decision_claim_that_lands_mid_flight_gets_an_update_not_a_dropped_edit(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decision-side equivalent of the action-item claim-race fix: a
    claim that lands between this sync's lock-miss and its own insert
    attempt must not be dropped."""
    engine = session.get_bind()
    first, _second = two_decisions(session)
    service.review_decision(session, first, DecisionReviewUpdate(status="confirmed"))
    session.commit()

    real_insert_if_absent_into = service._insert_if_absent_into
    already_raced = False

    def racing_insert_if_absent_into(s: Session, model: type) -> object:
        nonlocal already_raced
        if not already_raced and model is ExtDecisionRef:
            already_raced = True
            with Session(engine) as other:
                other.add(ExtDecisionRef(decision_id=first.id, system="notion", meeting_id=MEETING))
                other.commit()
                ref = other.get(ExtDecisionRef, (first.id, "notion"))
                assert ref is not None
                ref.external_id = "page_from_other_worker"
                other.commit()
        return real_insert_if_absent_into(s, model)

    monkeypatch.setattr(service, "_insert_if_absent_into", racing_insert_if_absent_into)

    notion = FakeNotion()
    result = service.sync_decision_to_notion(
        session, notion, decision_id=first.id, database_id="db"
    )

    assert result is not None
    assert notion.pages == [], "no second create -- the race's claim already made the page"
    assert len(notion.updates) == 1
    assert notion.updates[0][0] == "page_from_other_worker"


# --- a decision that stops being confirmed does not keep its page (#669) --------------

RETITLED = {
    "결정": {"title": [{"type": "text", "text": {"content": service.DECISION_PUT_BACK_TEXT}}]}
}


def confirmed_with_a_page(session: Session, notion: FakeNotion) -> ExtDecision:
    """The first decision, confirmed in a person's wording, with its page made."""
    first, _second = two_decisions(session)
    service.review_decision(
        session, first, DecisionReviewUpdate(status="confirmed", statement="출시는 금요일로 확정")
    )
    service.sync_decision_to_notion(session, notion, decision_id=first.id, database_id="db")
    return first


def resync(session: Session, notion: FakeNotion, decision_id: str) -> ExtDecisionRef | None:
    return service.sync_decision_to_notion(
        session, notion, decision_id=decision_id, database_id="db"
    )


@pytest.mark.parametrize("verdict", ["pending", "rejected"])
def test_a_decision_put_back_has_its_page_retitled_then_trashed(
    session: Session, verdict: str
) -> None:
    """Decided with the user (2026-10-02): the decision database has no status
    column, so a page left in place would go on reading as a confirmed
    decision. The title goes first, so Notion's trash does not keep the
    statement; the row stays, without a page."""
    notion = FakeNotion()
    first = confirmed_with_a_page(session, notion)
    service.review_decision(session, first, DecisionReviewUpdate(status=verdict))  # type: ignore[arg-type]

    ref = resync(session, notion, first.id)

    assert notion.updates == [("page_1", RETITLED)]
    assert notion.archived == {"page_1"}
    assert ref is not None and (ref.external_id, ref.url) == (None, None)
    assert len(notion.pages) == 1, "no page is made for a decision that is not confirmed"


def test_a_retired_page_is_retired_once(session: Session) -> None:
    notion = FakeNotion()
    first = confirmed_with_a_page(session, notion)
    service.review_decision(session, first, DecisionReviewUpdate(status="pending"))
    resync(session, notion, first.id)

    resync(session, notion, first.id)

    assert len(notion.updates) == 1 and len(notion.pages) == 1


def test_confirming_again_after_the_page_was_retired_makes_a_new_page(session: Session) -> None:
    notion = FakeNotion()
    first = confirmed_with_a_page(session, notion)
    service.review_decision(session, first, DecisionReviewUpdate(status="pending"))
    resync(session, notion, first.id)

    service.review_decision(session, first, DecisionReviewUpdate(status="confirmed"))
    ref = resync(session, notion, first.id)

    assert ref is not None and ref.external_id == "page_2"
    assert ref.url == service.notion_url("page_2")
    assert notion.pages[1][1]["결정"] == {
        "title": [{"type": "text", "text": {"content": "출시는 금요일로 확정"}}]
    }


def test_a_trash_that_fails_leaves_the_page_to_the_next_sync(session: Session) -> None:
    """The row keeps its page until the page is in the trash, so the next sync
    finds it and tries again."""

    class TrashDown(FakeNotion):
        down = True

        def trash_page(self, page_id: str) -> bool:
            if self.down:
                raise TransientIntegrationError("notion timed out")
            return super().trash_page(page_id)

    notion = TrashDown()
    first = confirmed_with_a_page(session, notion)
    service.review_decision(session, first, DecisionReviewUpdate(status="rejected"))

    with pytest.raises(TransientIntegrationError):
        resync(session, notion, first.id)
    kept = session.get(ExtDecisionRef, (first.id, "notion"))
    assert kept is not None and kept.external_id == "page_1"

    notion.down = False
    ref = resync(session, notion, first.id)

    assert notion.archived == {"page_1"}
    assert ref is not None and ref.external_id is None


def test_a_page_a_person_already_archived_is_left_as_it_is_and_forgotten(session: Session) -> None:
    """Notion refuses to edit a page in its trash, so the title cannot be
    rewritten: the page stays as the person put it. The row forgets it (#683)
    -- kept, the id made every sweep ask Notion again about a page nothing
    more can be done to."""
    notion = FakeNotion()
    first = confirmed_with_a_page(session, notion)
    notion.archived.add("page_1")
    service.review_decision(session, first, DecisionReviewUpdate(status="pending"))

    ref = resync(session, notion, first.id)

    assert notion.updates == []
    assert ref is not None and (ref.external_id, ref.url) == (None, None)
    assert service.decision_has_page(session, first.id) is False


def test_a_page_already_deleted_in_notion_has_nothing_to_retire(session: Session) -> None:
    notion = FakeNotion()
    first = confirmed_with_a_page(session, notion)
    notion.deleted.add("page_1")
    service.review_decision(session, first, DecisionReviewUpdate(status="pending"))

    ref = resync(session, notion, first.id)

    assert notion.updates == [] and notion.archived == set()
    assert ref is not None and ref.external_id is None


def test_a_deleted_decision_of_a_persons_takes_its_page_out_too(
    client: TestClient, session: Session, notion_syncs: list[str]
) -> None:
    """A decision a person added is really deleted. Its ref row outlives it, and
    is what the sync retires the page through."""
    added = client.post(
        f"{PREFIX}/decisions", json={"meeting_id": MEETING, "statement": "회고는 격주로"}
    ).json()
    notion = FakeNotion()
    resync(session, notion, added["id"])

    client.delete(f"{PREFIX}/decisions/{added['id']}")
    ref = resync(session, notion, added["id"])

    assert notion_syncs == [added["id"], added["id"]], "the deletion queued the sync"
    assert session.get(ExtDecision, added["id"]) is None
    assert notion.updates == [("page_1", RETITLED)] and notion.archived == {"page_1"}
    assert ref is not None and ref.external_id is None


def test_taking_a_confirmation_back_queues_the_sync_only_while_there_is_a_page(
    client: TestClient, session: Session, notion_syncs: list[str]
) -> None:
    first, second = two_decisions(session)
    client.patch(f"{PREFIX}/decisions/{first.id}", json={"status": "confirmed"})
    client.patch(f"{PREFIX}/decisions/{second.id}", json={"status": "confirmed"})
    session.add(
        ExtDecisionRef(
            decision_id=first.id, system="notion", meeting_id=MEETING, external_id="page_1"
        )
    )
    session.flush()

    client.patch(f"{PREFIX}/decisions/{first.id}", json={"status": "pending"})
    client.patch(f"{PREFIX}/decisions/{second.id}", json={"status": "rejected"})

    assert notion_syncs == [first.id, second.id, first.id], "the one with no page queues nothing"


def test_a_retired_page_is_not_shown_as_a_sync_in_flight(
    client: TestClient, session: Session
) -> None:
    """A ref with no page under a confirmed decision is a send in flight and is
    shown. Under a decision put back it is a retired page: nothing to show."""
    notion = FakeNotion()
    first = confirmed_with_a_page(session, notion)
    service.review_decision(session, first, DecisionReviewUpdate(status="pending"))
    resync(session, notion, first.id)

    listed = client.get(f"{PREFIX}/reviews/{MEETING}").json()["decisions"]

    assert next(d for d in listed if d["id"] == first.id)["sync_refs"] == []


def test_the_task_finds_a_deleted_decisions_page_through_its_ref(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``tasks.sync_decision`` takes the team's Notion from the decision's
    meeting. With the decision gone, the ref row is what still names it."""

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    notion = FakeNotion()
    config = IntegrationConfig(
        service="notion", team_id="team_1", secret="t", config={"decision_db_id": "db"}
    )
    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "load_integration", lambda _s, _team, _name: config)
    monkeypatch.setattr(tasks, "NotionClient", lambda _token: notion)
    session.add(
        ExtDecisionRef(
            decision_id="dec_gone", system="notion", meeting_id=MEETING, external_id="page_7"
        )
    )
    session.flush()

    tasks.sync_decision("dec_gone")

    assert notion.updates == [("page_7", RETITLED)] and notion.archived == {"page_7"}
    ref = session.get(ExtDecisionRef, ("dec_gone", "notion"))
    assert ref is not None and ref.external_id is None


def test_a_rerun_that_drops_a_decision_keeps_the_ref_that_names_its_page(session: Session) -> None:
    """mkkim68, review of #679: ``build_decisions`` deleted the refs of
    decisions a rerun dropped, page or not. The page then stayed live in Notion
    with nothing left to find it by. A claim with no page still goes.

    A confirmed decision is dropped only when a line it was made from can no
    longer be read (2026-10-09); here its speaker's consent is gone."""
    notion = FakeNotion()
    first = confirmed_with_a_page(session, notion)
    second = session.scalars(select(ExtDecision).where(ExtDecision.id != first.id)).one()
    session.add(ExtDecisionRef(decision_id=second.id, system="notion", meeting_id=MEETING))
    session.flush()

    read = labelled({})
    read[1] = ClassifiedUtterance(id="utt_2", kind=None, confidence=0.0, text="")
    service.build_decisions(session, meeting_id=MEETING, utterances=read)

    assert session.get(ExtDecision, first.id) is None, "the rerun dropped it"
    assert session.get(ExtDecisionRef, (second.id, "notion")) is None
    assert service.decision_pages_without_a_decision(session, MEETING) == [first.id]

    resync(session, notion, first.id)

    assert notion.updates == [("page_1", RETITLED)] and notion.archived == {"page_1"}
    assert service.decision_pages_without_a_decision(session, MEETING) == []


def test_a_property_map_with_no_title_trashes_the_page_as_it_is(session: Session) -> None:
    """A team's own map replaces the default one. With no title in it nothing
    says which property holds the statement, so the page is trashed without a
    retitle -- better than leaving it live (PARK, review of #679)."""
    notion = FakeNotion()
    first = confirmed_with_a_page(session, notion)
    service.review_decision(session, first, DecisionReviewUpdate(status="pending"))

    ref = service.sync_decision_to_notion(
        session,
        notion,
        decision_id=first.id,
        database_id="db",
        property_names={"confidence": "신뢰도"},
    )

    assert notion.updates == [] and notion.archived == {"page_1"}
    assert ref is not None and ref.external_id is None
