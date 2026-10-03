"""A deleted item's Notion page or Jira issue that the deleting request could
not clean up is owed, and retried (#692).

Deleting an item trashes its page and closes its issue in the request, best
effort. Before this, a failed call left the page live holding the item's
sentence with nothing that still knew its id: the item and its ref were gone.
Now the request records what it owes in ``ext_external_cleanup`` -- ids only --
and ``drain_external_cleanup`` tries again.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from autune_core import Base, JiraAccess, Meeting
from autune_core.integrations_config import IntegrationConfig
from autune_core.oauth.atlassian import JiraReconnectRequiredError
from autune_extraction import tasks
from autune_extraction.jira_sync import DELETED_NOTE
from autune_extraction.models import ExtActionItem, ExtExternalCleanup, ExtExternalRef
from autune_integrations import PermanentIntegrationError, TransientIntegrationError

from .conftest import CLOSE_JIRA_ISSUE, TRASH_NOTION_PAGE

MEETING = "mtg_1"
TEAM = "team_1"
CLOUD = "cloud-1"
NOTION = IntegrationConfig(service="notion", team_id=TEAM, secret="t", config={})


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    tables = [
        Meeting.__table__,
        ExtActionItem.__table__,
        ExtExternalRef.__table__,
        ExtExternalCleanup.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id=TEAM, title="스프린트 회의"))
        s.flush()

        @contextmanager
        def scope() -> Iterator[Session]:
            try:
                yield s
                s.commit()
            except BaseException:
                s.rollback()
                raise

        monkeypatch.setattr(tasks, "session_scope", scope)
        monkeypatch.setattr(tasks, "trash_notion_page", TRASH_NOTION_PAGE)
        monkeypatch.setattr(tasks, "close_jira_issue", CLOSE_JIRA_ISSUE)
        yield s


def item_with(session: Session, system: str, external_id: str, site: str | None = None) -> str:
    row = ExtActionItem(meeting_id=MEETING, description="릴리스 노트 정리", confidence=0.9)
    session.add(row)
    session.flush()
    session.add(
        ExtExternalRef(
            action_item_id=row.id,
            system=system,
            meeting_id=MEETING,
            external_id=external_id,
            site=site,
        )
    )
    session.commit()
    return row.id


def owed(session: Session) -> list[tuple[str, str, str, str | None]]:
    session.expire_all()
    return [
        (r.team_id, r.system, r.external_id, r.site)
        for r in session.scalars(select(ExtExternalCleanup).order_by(ExtExternalCleanup.id))
    ]


class Notion:
    """Stands in for ``NotionClient``: trashes, or raises what it is told to."""

    trashed: list[str] = []
    fail: Exception | None = None

    def __init__(self, token: str) -> None:
        pass

    def trash_page(self, page_id: str) -> bool:
        if Notion.fail is not None:
            raise Notion.fail
        Notion.trashed.append(page_id)
        return True

    def close(self) -> None:
        pass


class Jira:
    closed: list[str] = []
    comments: list[tuple[str, str]] = []
    fail: Exception | None = None

    def move_to_category(self, key: str, category: str) -> bool:
        if Jira.fail is not None:
            raise Jira.fail
        Jira.closed.append(key)
        return True

    def add_comment(self, key: str, body: str) -> None:
        Jira.comments.append((key, body))

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    Notion.trashed, Notion.fail = [], None
    Jira.closed, Jira.comments, Jira.fail = [], [], None
    monkeypatch.setattr(tasks, "NotionClient", Notion)
    monkeypatch.setattr(tasks.JiraClient, "for_cloud", staticmethod(lambda t, c: Jira()))


def connected(monkeypatch: pytest.MonkeyPatch, *, notion: bool = True, jira: Any = "ok") -> None:
    monkeypatch.setattr(tasks, "load_integration", lambda _s, _t, _n: NOTION if notion else None)

    def access(team: str, **kw: object) -> JiraAccess | None:
        if isinstance(jira, Exception):
            raise jira
        return JiraAccess("token", CLOUD, "AUT") if jira == "ok" else None

    monkeypatch.setattr(tasks, "jira_access", access)


# --- what the deleting request owes ----------------------------------------------------


def test_a_page_notion_would_not_trash_is_owed_by_its_ids(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected(monkeypatch)
    Notion.fail = TransientIntegrationError("notion timed out")
    item_id = item_with(session, "notion", "page-1")

    tasks.trash_notion_page(item_id)  # must not raise

    assert owed(session) == [(TEAM, "notion", "page-1", None)]


def test_a_page_whose_team_has_no_notion_now_is_owed_too(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected(monkeypatch, notion=False)
    item_id = item_with(session, "notion", "page-1")

    tasks.trash_notion_page(item_id)

    assert owed(session) == [(TEAM, "notion", "page-1", None)]


def test_a_page_trashed_at_once_owes_nothing(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected(monkeypatch)
    item_id = item_with(session, "notion", "page-1")

    tasks.trash_notion_page(item_id)

    assert Notion.trashed == ["page-1"]
    assert owed(session) == []


@pytest.mark.parametrize(
    "jira", [JiraReconnectRequiredError("refused"), None, "down"], ids=["refused", "gone", "down"]
)
def test_an_issue_jira_would_not_close_is_owed_with_its_site(
    session: Session, monkeypatch: pytest.MonkeyPatch, jira: object
) -> None:
    connected(monkeypatch, jira="ok" if jira == "down" else jira)
    if jira == "down":
        Jira.fail = TransientIntegrationError("jira timed out")
    item_id = item_with(session, "jira", "AUT-1", site=CLOUD)

    tasks.close_jira_issue(item_id)  # must not raise

    assert owed(session) == [(TEAM, "jira", "AUT-1", CLOUD)]


def test_an_item_never_sent_owes_nothing(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    connected(monkeypatch, notion=False, jira=None)
    row = ExtActionItem(meeting_id=MEETING, description="초안", confidence=0.9)
    session.add(row)
    session.commit()

    tasks.trash_notion_page(row.id)
    tasks.close_jira_issue(row.id)

    assert owed(session) == []


def test_owing_the_same_page_twice_keeps_one_row(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected(monkeypatch, notion=False)
    item_id = item_with(session, "notion", "page-1")

    tasks.trash_notion_page(item_id)
    tasks.trash_notion_page(item_id)

    assert len(owed(session)) == 1


def test_what_is_owed_and_logged_is_ids_only(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected(monkeypatch)
    Notion.fail = PermanentIntegrationError("notion said: 릴리스 노트 정리")
    item_id = item_with(session, "notion", "page-1")

    with capture_logs() as logs:
        tasks.trash_notion_page(item_id)

    assert {c.name for c in ExtExternalCleanup.__table__.columns} == {
        "id",
        "team_id",
        "system",
        "external_id",
        "site",
        "attempts",
        "created_at",
    }
    assert "릴리스 노트 정리" not in repr(logs)


# --- the retry -------------------------------------------------------------------------


def owe(session: Session, system: str, external_id: str, site: str | None = None) -> None:
    session.add(ExtExternalCleanup(team_id=TEAM, system=system, external_id=external_id, site=site))
    session.commit()


def test_the_next_run_trashes_the_page_and_closes_the_issue(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected(monkeypatch)
    owe(session, "notion", "page-1")
    owe(session, "jira", "AUT-1", CLOUD)

    assert tasks.drain_external_cleanup() == 2

    assert Notion.trashed == ["page-1"]
    assert Jira.closed == ["AUT-1"]
    assert Jira.comments == [("AUT-1", DELETED_NOTE)]
    assert owed(session) == []


def test_a_page_or_issue_already_gone_is_done(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected(monkeypatch)
    Jira.fail = PermanentIntegrationError("gone", upstream_status=404)
    owe(session, "jira", "AUT-1", CLOUD)

    tasks.drain_external_cleanup()

    assert owed(session) == []


def test_a_team_not_connected_now_is_kept_without_counting_an_attempt(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected(monkeypatch, notion=False, jira=JiraReconnectRequiredError("refused"))
    owe(session, "notion", "page-1")
    owe(session, "jira", "AUT-1", CLOUD)

    assert tasks.drain_external_cleanup() == 0

    session.expire_all()
    rows = session.scalars(select(ExtExternalCleanup)).all()
    assert [(r.external_id, r.attempts) for r in rows] == [("page-1", 0), ("AUT-1", 0)]


def test_a_key_from_another_jira_site_is_dropped_not_closed(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a reconnect to another site the same key names someone else's issue."""
    connected(monkeypatch)
    owe(session, "jira", "AUT-1", "an-older-site")

    tasks.drain_external_cleanup()

    assert Jira.closed == []
    assert owed(session) == []


def test_a_transient_failure_is_counted_and_given_up_at_the_limit(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected(monkeypatch)
    Notion.fail = TransientIntegrationError("timed out")
    owe(session, "notion", "page-1")

    for _ in range(tasks.CLEANUP_MAX_ATTEMPTS - 1):
        tasks.drain_external_cleanup()
    session.expire_all()
    (row,) = session.scalars(select(ExtExternalCleanup)).all()
    assert row.attempts == tasks.CLEANUP_MAX_ATTEMPTS - 1

    tasks.drain_external_cleanup()

    assert owed(session) == []


def test_a_refusal_drops_the_row(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    connected(monkeypatch)
    Notion.fail = PermanentIntegrationError("forbidden", upstream_status=403)
    owe(session, "notion", "page-1")

    tasks.drain_external_cleanup()

    assert owed(session) == []


def test_one_row_that_breaks_does_not_block_the_rest(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected(monkeypatch)
    calls: list[str] = []

    class Breaks(Notion):
        def trash_page(self, page_id: str) -> bool:
            calls.append(page_id)
            if page_id == "page-1":
                raise RuntimeError("unexpected")
            return True

    monkeypatch.setattr(tasks, "NotionClient", Breaks)
    owe(session, "notion", "page-1")
    owe(session, "notion", "page-2")

    assert tasks.drain_external_cleanup() == 1

    assert calls == ["page-1", "page-2"]
    assert [r[2] for r in owed(session)] == ["page-1"]
