"""Notion and Jira are independent claims (#30): one failing, or one team never
having connected it, must not stop the other from being attempted.

SQLite in memory, ``FakeNotion``/``FakeJira`` for the workspaces, the task
called directly -- see ``test_notion_sync.py`` and ``test_jira_sync.py`` for
each service on its own.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import Base, Meeting, Utterance
from autune_core.integrations_config import IntegrationConfig
from autune_extraction import service, tasks
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import ExtActionItem, ExtActionItemSource, ExtExternalRef
from autune_integrations import PermanentIntegrationError
from autune_integrations.fakes import FakeJira, FakeNotion

MEETING = "mtg_1"

TABLES = [
    Meeting.__table__,
    Utterance.__table__,
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtExternalRef.__table__,
]

NOTION_CONFIG = IntegrationConfig(
    service="notion", team_id="team_1", secret="t", config={"action_db_id": "db_actions"}
)
JIRA_CONFIG = IntegrationConfig(
    service="jira",
    team_id="team_1",
    secret="t",
    config={
        "project_key": "AUT",
        "issue_type": "Task",
        "base_url": "https://team.atlassian.net",
        "assignee_mapping": {"김개발": "acct_123"},
    },
)


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


def item(session: Session, *, status: str = "todo") -> ExtActionItem:
    row = ExtActionItem(
        meeting_id=MEETING,
        description="릴리스 노트 정리",
        assignee_label="김개발",
        due_date=date(2026, 9, 25),
        status=status,
        confidence=0.91,
        origin="model",
    )
    row.sources = [ExtActionItemSource(utterance_id="utt_1")]
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def wired(session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    """Real ``session_scope`` opens a fresh session per call, so one call's
    rollback can never leak into the next. This fixture reuses one session
    across both services' calls (so a test can inspect what each left
    behind), which means it has to roll back on exception itself -- matching
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

    monkeypatch.setattr(tasks, "session_scope", scope)
    return session


def by_service(**configs: IntegrationConfig | None):  # noqa: ANN201
    def load(_session: object, _team_id: str, service: str) -> IntegrationConfig | None:
        return configs.get(service)

    return load


def test_both_services_are_attempted_when_both_are_connected(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = item(wired)
    wired.commit()
    monkeypatch.setattr(
        tasks, "load_integration", by_service(notion=NOTION_CONFIG, jira=JIRA_CONFIG)
    )
    monkeypatch.setattr(tasks, "NotionClient", lambda _secret: FakeNotion())
    monkeypatch.setattr(tasks, "JiraClient", lambda *_a, **_kw: FakeJira())

    tasks.sync_action_item(row.id)

    refs = {r.system for r in wired.scalars(select(ExtExternalRef))}
    assert refs == {"notion", "jira"}


def test_a_notion_only_team_still_gets_nothing_from_jira(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = item(wired)
    wired.commit()
    monkeypatch.setattr(tasks, "load_integration", by_service(notion=NOTION_CONFIG))
    monkeypatch.setattr(tasks, "NotionClient", lambda _secret: FakeNotion())

    tasks.sync_action_item(row.id)

    refs = {r.system for r in wired.scalars(select(ExtExternalRef))}
    assert refs == {"notion"}


def test_a_failed_notion_call_does_not_stop_jira_from_being_attempted(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug this guards against: catching only around the pair would let
    Notion's exception skip Jira every time, not just this once."""
    row = item(wired)
    wired.commit()  # the item is already durable, the way an earlier task left it
    monkeypatch.setattr(
        tasks, "load_integration", by_service(notion=NOTION_CONFIG, jira=JIRA_CONFIG)
    )

    def refuses(_secret: str) -> FakeNotion:
        class Refusing(FakeNotion):
            def create_page(self, database_id: str, properties: dict) -> str:
                raise PermanentIntegrationError("notion rejected the page")

        return Refusing()

    monkeypatch.setattr(tasks, "NotionClient", refuses)
    monkeypatch.setattr(tasks, "JiraClient", lambda *_a, **_kw: FakeJira())

    tasks.sync_action_item(row.id)  # must not raise

    refs = {r.system for r in wired.scalars(select(ExtExternalRef))}
    assert refs == {"jira"}, "notion's claim rolled back on its own failure; jira still ran"


def test_a_failed_jira_call_does_not_stop_notion_from_having_already_run(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = item(wired)
    wired.commit()
    monkeypatch.setattr(
        tasks, "load_integration", by_service(notion=NOTION_CONFIG, jira=JIRA_CONFIG)
    )
    monkeypatch.setattr(tasks, "NotionClient", lambda _secret: FakeNotion())

    def refuses(*_a: object, **_kw: object) -> FakeJira:
        class Refusing(FakeJira):
            def create_issue(self, *args: object, **kwargs: object) -> str:
                raise PermanentIntegrationError("jira rejected the issue")

        return Refusing()

    monkeypatch.setattr(tasks, "JiraClient", refuses)

    tasks.sync_action_item(row.id)  # must not raise

    refs = {r.system for r in wired.scalars(select(ExtExternalRef))}
    assert refs == {"notion"}
