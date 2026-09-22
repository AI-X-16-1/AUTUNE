"""Step 7 for action items: a confirmed item becomes one Jira issue (#30).

SQLite in memory, ``FakeJira`` for the workspace. Mirrors ``test_notion_
sync.py``'s shape: when an issue is sent (only after a person confirms, only
once), what it carries (the item, never the transcript), and the one rule
Jira has that Notion does not -- creation held back until the assignee maps
to a Jira account.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import Base, Meeting, Utterance
from autune_extraction import service
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import ExtActionItem, ExtActionItemSource, ExtExternalRef
from autune_integrations import PermanentIntegrationError
from autune_integrations.fakes import FakeJira

MEETING = "mtg_1"
PROJECT = "AUT"
ISSUE_TYPE = "Task"
BASE_URL = "https://team.atlassian.net"

TABLES = [
    Meeting.__table__,
    Utterance.__table__,
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtExternalRef.__table__,
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
        assignee_id=fields.pop("assignee_id", None),
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


def sync(session: Session, jira: FakeJira, action_item_id: str, **kw: object) -> object:
    kw.setdefault("assignee_mapping", {"김개발": "acct_123"})
    return service.sync_action_item_to_jira(
        session,
        jira,
        action_item_id=action_item_id,
        project_key=PROJECT,
        issue_type=ISSUE_TYPE,
        base_url=BASE_URL,
        **kw,
    )


# --- what an issue carries -----------------------------------------------------


def test_a_confirmed_item_becomes_one_issue_with_the_item_and_no_transcript(
    session: Session,
) -> None:
    jira = FakeJira()
    row = item(session)

    ref = sync(session, jira, row.id)

    assert len(jira.issues) == 1
    issue = jira.issues[0]
    assert issue["project"] == PROJECT
    assert issue["type"] == ISSUE_TYPE
    assert issue["summary"] == "릴리스 노트 정리"
    assert issue["description"] == "릴리스 노트 정리"
    assert issue["assignee_account_id"] == "acct_123"
    assert issue["due_date"] == "2026-09-25"
    assert "제가 금요일까지" not in repr(issue), "source utterances stay in Autune"

    stored = session.get(ExtExternalRef, (row.id, "jira"))
    assert stored is ref
    assert stored is not None
    assert stored.external_id == "AUT-1"
    assert stored.url == "https://team.atlassian.net/browse/AUT-1"


def test_the_summary_is_cut_the_description_is_not(session: Session) -> None:
    long = "매우 긴 설명" * 60  # well over JIRA_SUMMARY_MAX
    jira = FakeJira()
    row = item(session, description=long)

    sync(session, jira, row.id)

    issue = jira.issues[0]
    assert len(issue["summary"]) == service.JIRA_SUMMARY_MAX
    assert issue["description"] == long


def test_no_due_date_sends_none(session: Session) -> None:
    jira = FakeJira()
    row = item(session, due_date=None)

    sync(session, jira, row.id)

    assert jira.issues[0]["due_date"] is None


# --- when an issue is sent ------------------------------------------------------


def test_an_item_still_waiting_for_confirmation_sends_nothing(session: Session) -> None:
    jira = FakeJira()
    row = item(session, status="needs_confirmation")

    assert sync(session, jira, row.id) is None
    assert jira.issues == []
    assert session.scalars(select(ExtExternalRef)).all() == []


def test_the_second_sync_of_an_item_sends_nothing(session: Session) -> None:
    jira = FakeJira()
    row = item(session)

    sync(session, jira, row.id)
    row.status = "done"
    assert sync(session, jira, row.id) is None

    assert len(jira.issues) == 1


def test_a_failed_call_takes_the_claim_back_so_a_later_sync_can_send(session: Session) -> None:
    row = item(session)
    session.commit()

    class Refusing(FakeJira):
        def create_issue(self, *args: object, **kwargs: object) -> str:
            raise PermanentIntegrationError("jira rejected the issue: unknown project")

    with pytest.raises(PermanentIntegrationError):
        sync(session, Refusing(), row.id)
    session.rollback()

    jira = FakeJira()
    sync(session, jira, row.id)
    assert len(jira.issues) == 1


def test_a_gone_item_sends_nothing(session: Session) -> None:
    assert sync(session, FakeJira(), "act_missing") is None


# --- the assignee has to be mapped, or nothing is created (#30) -----------------


def test_an_unmapped_assignee_holds_creation_back(session: Session) -> None:
    jira = FakeJira()
    row = item(session, assignee_label="박기획")  # not in the default mapping

    assert sync(session, jira, row.id) is None
    assert jira.issues == []
    assert session.scalars(select(ExtExternalRef)).all() == [], (
        "no claim either -- a later sync, once the mapping covers this person, "
        "must still be able to try"
    )


def test_no_assignee_at_all_holds_creation_back(session: Session) -> None:
    jira = FakeJira()
    row = item(session, assignee_label=None)

    assert sync(session, jira, row.id) is None
    assert jira.issues == []


def test_an_empty_mapping_holds_every_item_back(session: Session) -> None:
    jira = FakeJira()
    row = item(session)

    assert sync(session, jira, row.id, assignee_mapping={}) is None
    assert jira.issues == []


def test_assignee_id_is_tried_before_the_label(session: Session) -> None:
    """The stable key wins when both would resolve to different accounts --
    the label is only a fallback for when there is no id yet (#70)."""
    jira = FakeJira()
    row = item(session, assignee_id="user_1", assignee_label="김개발")

    sync(
        session,
        jira,
        row.id,
        assignee_mapping={"user_1": "acct_by_id", "김개발": "acct_by_label"},
    )

    assert jira.issues[0]["assignee_account_id"] == "acct_by_id"


def test_the_label_is_used_when_there_is_no_id(session: Session) -> None:
    jira = FakeJira()
    row = item(session, assignee_id=None, assignee_label="김개발")

    sync(session, jira, row.id, assignee_mapping={"김개발": "acct_by_label"})

    assert jira.issues[0]["assignee_account_id"] == "acct_by_label"


# --- jira_assignee, in isolation --------------------------------------------------


def test_jira_assignee_returns_none_with_no_mapping_at_all() -> None:
    row = ExtActionItem(meeting_id=MEETING, description="x", status="todo", confidence=0.9)
    assert service.jira_assignee(row, {}) is None
