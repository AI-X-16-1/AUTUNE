"""#317: a row confirmed before its team connected Notion, or before #312
shipped the sync, never gets a page from the live path -- nothing about its
state says the sync never ran. ``notion_backfill`` walks every already-
confirmed item and decision through the same claim-then-call the live sync
uses, so it must be idempotent, must skip a team that never connected, and
must never touch a row still waiting for confirmation.

SQLite in memory, ``FakeNotion`` for the workspace, ``session_scope``
monkeypatched the way ``test_notion_sync.py``'s ``wired`` fixture does.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import Base, Meeting, PrivacyViolationError, Utterance
from autune_core.integrations_config import IntegrationConfig
from autune_extraction import notion_backfill
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtDecision,
    ExtDecisionRef,
    ExtDecisionReview,
    ExtDecisionSource,
    ExtExternalRef,
)
from autune_integrations import IntegrationError
from autune_integrations.fakes import FakeNotion

TABLES = [
    Meeting.__table__,
    Utterance.__table__,
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtExternalRef.__table__,
    ExtDecision.__table__,
    ExtDecisionSource.__table__,
    ExtDecisionReview.__table__,
    ExtDecisionRef.__table__,
]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as session:
        session.add(Meeting(id="mtg_1", team_id="team_1", title="스프린트 회의"))
        session.add(Meeting(id="mtg_2", team_id="team_2", title="다른 팀 회의"))
        for meeting in ("mtg_1", "mtg_2"):
            session.add(
                Utterance(
                    id=f"utt_{meeting}",
                    meeting_id=meeting,
                    speaker_label="SPEAKER_00",
                    start_sec=0.0,
                    end_sec=2.0,
                    text="제가 금요일까지 정리하겠습니다",
                )
            )
        session.flush()
        yield session


@pytest.fixture
def wired(session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    """Real ``session_scope`` opens a fresh session per call, so one call's
    rollback can never leak into the next. This fixture reuses one session
    across every row's call (so a test can inspect what each left behind),
    which means it has to roll back on exception itself -- matching
    ``session_scope``'s own ``except Exception: session.rollback(); raise`` --
    or a failed claim's still-pending insert would ride along on whichever
    call commits next.
    """

    @contextmanager
    def scope() -> Iterator[Session]:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

    monkeypatch.setattr(notion_backfill, "session_scope", scope)
    return session


def item(session: Session, *, meeting: str = "mtg_1", status: str = "todo") -> ExtActionItem:
    row = ExtActionItem(
        meeting_id=meeting,
        description="릴리스 노트 정리",
        due_date=date(2026, 9, 25),
        status=status,
        confidence=0.9,
        origin="model",
    )
    row.sources = [ExtActionItemSource(utterance_id=f"utt_{meeting}")]
    session.add(row)
    session.flush()
    return row


def decision(session: Session, *, meeting: str = "mtg_1", status: str = "confirmed") -> ExtDecision:
    row = ExtDecision(meeting_id=meeting, statement="다음 릴리스는 금요일", confidence=0.9)
    row.sources = [ExtDecisionSource(utterance_id=f"utt_{meeting}", position=0)]
    session.add(row)
    session.flush()
    session.add(ExtDecisionReview(decision_id=row.id, meeting_id=meeting, status=status))
    session.flush()
    return row


def config_for(
    team: str, *, action_db: str | None = "db_actions", decision_db: str | None = "db_decisions"
) -> IntegrationConfig:
    cfg: dict[str, object] = {}
    if action_db:
        cfg["action_db_id"] = action_db
    if decision_db:
        cfg["decision_db_id"] = decision_db
    return IntegrationConfig(service="notion", team_id=team, secret=f"token-{team}", config=cfg)


class _FailingNotion:
    """Every call reaches Notion and fails, the way a timeout or a 502 would."""

    def create_page(self, database_id: str, properties: dict) -> str:
        raise IntegrationError("notion is down")


def wire_notion(
    monkeypatch: pytest.MonkeyPatch,
    notion: FakeNotion,
    configs: dict[str, IntegrationConfig | None],
) -> None:
    monkeypatch.setattr(notion_backfill, "load_integration", lambda _s, team, _n: configs.get(team))
    monkeypatch.setattr(notion_backfill, "NotionClient", lambda _token: notion)


# --- counting -----------------------------------------------------------------


def test_dry_run_reports_counts_and_sends_nothing(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    item(wired)
    item(wired, status="needs_confirmation")
    decision(wired)
    decision(wired, status="pending")
    notion = FakeNotion()
    wire_notion(monkeypatch, notion, {"team_1": config_for("team_1")})

    code = notion_backfill.main(["--dry-run"])

    assert code == 0
    assert notion.pages == []
    assert wired.scalars(select(ExtExternalRef)).all() == []
    assert wired.scalars(select(ExtDecisionRef)).all() == []


def test_needs_confirmation_items_and_pending_decisions_are_excluded(wired: Session) -> None:
    item(wired, status="needs_confirmation")
    decision(wired, status="pending")
    decision(wired, status="rejected")

    assert notion_backfill._confirmed_action_items(None) == []
    assert notion_backfill._confirmed_decisions(None) == []


# --- sending --------------------------------------------------------------------


def test_already_confirmed_items_and_decisions_are_sent(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    it = item(wired)
    dec = decision(wired)
    notion = FakeNotion()
    wire_notion(monkeypatch, notion, {"team_1": config_for("team_1")})

    code = notion_backfill.main([])

    assert code == 0
    assert len(notion.pages) == 2
    assert wired.get(ExtExternalRef, (it.id, "notion")) is not None
    assert wired.get(ExtDecisionRef, (dec.id, "notion")) is not None


def test_running_the_backfill_twice_sends_each_page_once(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    item(wired)
    decision(wired)
    notion = FakeNotion()
    wire_notion(monkeypatch, notion, {"team_1": config_for("team_1")})

    notion_backfill.main([])
    notion_backfill.main([])

    assert len(notion.pages) == 2


def test_an_item_that_already_has_its_page_is_skipped_not_resent(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case #317 is actually for: the live sync already sent this one
    (or the backfill did, on an earlier run) -- the claim finds the row and
    the backfill must not make a second page."""
    it = item(wired)
    wired.add(ExtExternalRef(action_item_id=it.id, system="notion", meeting_id=it.meeting_id))
    wired.flush()
    notion = FakeNotion()
    wire_notion(monkeypatch, notion, {"team_1": config_for("team_1")})

    notion_backfill.main([])

    assert notion.pages == []


def test_a_failed_call_leaves_no_claim_for_a_later_run_to_skip(
    wired: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The claim and the call share one transaction (``session_scope``). A call
    that fails must not leave a page nobody ever sent claimed as sent -- that
    would make ``test_an_item_that_already_has_its_page_is_skipped_not_resent``'s
    skip permanent instead of retryable."""
    it = item(wired)
    wire_notion(monkeypatch, _FailingNotion(), {"team_1": config_for("team_1")})

    code = notion_backfill.main([])

    assert code == 1
    assert "failed 1" in capsys.readouterr().out

    # ``scope()`` above never reaches its own ``session.commit()`` when the
    # call raises, the same way ``session_scope`` never reaches its. A fresh
    # ``session_scope()`` in production would open a new transaction that
    # never saw the claim; rolling back here is that transaction's equivalent,
    # not an assertion about this fixture's own bookkeeping.
    wired.rollback()
    assert wired.get(ExtExternalRef, (it.id, "notion")) is None

    notion = FakeNotion()
    wire_notion(monkeypatch, notion, {"team_1": config_for("team_1")})

    notion_backfill.main([])

    assert len(notion.pages) == 1
    assert wired.get(ExtExternalRef, (it.id, "notion")) is not None


class _PrivacyBlockedOnce(FakeNotion):
    """Blocked on the first call, like a real ``NotionClient`` would be when
    the outbound guard finds unmasked PII -- ``PrivacyViolationError``, a
    sibling of ``IntegrationError``, not a subclass. Every later call sends
    like ``FakeNotion`` normally does."""

    def __init__(self) -> None:
        super().__init__()
        self._first = True

    def create_page(self, database_id: str, properties: dict) -> str:
        if self._first:
            self._first = False
            raise PrivacyViolationError("notion: phone number pattern found")
        return super().create_page(database_id, properties)


def test_a_privacy_guard_block_costs_only_its_own_row(
    wired: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Caught the same way as ``IntegrationError`` (review of #333) -- left
    uncaught, this would crash the whole batch instead of the one row, since
    both rows share the same ``for`` loop in ``backfill_action_items``. Order
    is not asserted: whichever of the two rows the query hands the loop
    first is the one the fake blocks."""
    a = item(wired)
    b = item(wired)
    wired.commit()
    notion = _PrivacyBlockedOnce()
    wire_notion(monkeypatch, notion, {"team_1": config_for("team_1")})

    code = notion_backfill.main([])

    assert code == 1
    assert "failed 1" in capsys.readouterr().out
    wired.rollback()
    refs = {row_id for row_id in (a.id, b.id) if wired.get(ExtExternalRef, (row_id, "notion"))}
    # The row blocked first has no ref; the loop still reached the other one.
    assert len(refs) == 1
    assert len(notion.pages) == 1


# --- a team that never connected -------------------------------------------------


def test_a_team_without_notion_is_skipped_and_counted(
    wired: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    item(wired)
    decision(wired)
    notion = FakeNotion()
    wire_notion(monkeypatch, notion, {"team_1": None})

    code = notion_backfill.main([])

    assert code == 0
    assert notion.pages == []
    out = capsys.readouterr().out
    assert "not connected 1" in out


# --- the --team filter ------------------------------------------------------------


def test_the_team_filter_only_touches_that_team(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine = item(wired, meeting="mtg_1")
    theirs = item(wired, meeting="mtg_2")
    notion = FakeNotion()
    wire_notion(
        monkeypatch, notion, {"team_1": config_for("team_1"), "team_2": config_for("team_2")}
    )

    notion_backfill.main(["--team", "team_1"])

    assert wired.get(ExtExternalRef, (mine.id, "notion")) is not None
    assert wired.get(ExtExternalRef, (theirs.id, "notion")) is None
