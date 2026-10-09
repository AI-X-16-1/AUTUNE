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

from autune_core import AutuneError, Base, JiraAccess, Meeting
from autune_core.integrations_config import IntegrationConfig
from autune_core.oauth.atlassian import JiraReconnectRequiredError
from autune_extraction import tasks
from autune_extraction.jira_sync import DELETED_NOTE
from autune_extraction.models import (
    ExtActionItem,
    ExtExternalCleanup,
    ExtExternalRef,
    ExtNotionTarget,
)
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
        # A retire is given the name map in force, which the team's row decides.
        ExtNotionTarget.__table__,
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
    retitled: list[tuple[str, dict]] = []
    calls: list[str] = []
    fail: Exception | None = None

    def __init__(self, token: str) -> None:
        pass

    def update_page(self, page_id: str, properties: dict) -> None:
        Notion.calls.append("retitle")
        Notion.retitled.append((page_id, properties))

    def trash_page(self, page_id: str) -> bool:
        if Notion.fail is not None:
            raise Notion.fail
        Notion.calls.append("trash")
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
    Notion.trashed, Notion.retitled, Notion.calls, Notion.fail = [], [], [], None
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


def test_rows_of_unconnected_teams_do_not_keep_a_connected_team_waiting(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mkkim68, review of #764: more passed-over rows than one batch, first by
    id, and the connected team's page behind them is still trashed."""
    other = "team_off"
    session.add(Meeting(id="mtg_off", team_id=other, title="t"))
    for n in range(tasks.CLEANUP_BATCH + 5):
        session.add(ExtExternalCleanup(team_id=other, system="notion", external_id=f"off-{n}"))
    session.commit()
    owe(session, "notion", "page-1")
    monkeypatch.setattr(
        tasks, "load_integration", lambda _s, team, _n: NOTION if team == TEAM else None
    )

    assert tasks.drain_external_cleanup() == 1

    assert Notion.trashed == ["page-1"]
    session.expire_all()
    left = session.scalars(select(ExtExternalCleanup)).all()
    assert len(left) == tasks.CLEANUP_BATCH + 5
    assert {r.attempts for r in left} == {0}


def test_a_team_whose_connection_cannot_be_read_does_not_roll_the_run_back(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PARK, review of #764: one team's token refresh failing for now must
    not end the run -- the rows already cleaned stay cleaned, the failing
    team's rows stay owed with no attempt counted."""
    other = "team_flaky"
    session.add(Meeting(id="mtg_flaky", team_id=other, title="t"))
    session.add(ExtExternalCleanup(team_id=other, system="jira", external_id="FLK-1", site=CLOUD))
    session.add(ExtExternalCleanup(team_id=other, system="notion", external_id="flaky-page"))
    session.commit()
    owe(session, "notion", "page-1")

    def notion_config(_s: Session, team: str, _n: str) -> object:
        if team == other:
            raise ValueError("secret would not decrypt")
        return NOTION

    def access(team: str, **_kw: object) -> JiraAccess | None:
        if team == other:
            raise AutuneError("atlassian answered 503")
        return JiraAccess("token", CLOUD, "AUT")

    monkeypatch.setattr(tasks, "load_integration", notion_config)
    monkeypatch.setattr(tasks, "jira_access", access)

    assert tasks.drain_external_cleanup() == 1

    assert Notion.trashed == ["page-1"]
    session.expire_all()
    left = {
        (r.system, r.external_id, r.attempts) for r in session.scalars(select(ExtExternalCleanup))
    }
    assert left == {("jira", "FLK-1", 0), ("notion", "flaky-page", 0)}


def test_a_run_tries_at_most_one_batch(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    connected(monkeypatch)
    for n in range(tasks.CLEANUP_BATCH + 3):
        session.add(ExtExternalCleanup(team_id=TEAM, system="notion", external_id=f"page-{n}"))
    session.commit()

    assert tasks.drain_external_cleanup() == tasks.CLEANUP_BATCH
    assert len(owed(session)) == 3


def test_a_jira_key_with_no_site_is_dropped_not_closed(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mkkim68, review of #764: a key without a site could be anyone's issue on
    the site connected now."""
    connected(monkeypatch)
    owe(session, "jira", "AUT-1", None)

    tasks.drain_external_cleanup()

    assert Jira.closed == []
    assert Jira.comments == []
    assert owed(session) == []


def test_a_jira_ref_with_no_site_is_not_owed(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected(monkeypatch, jira=None)
    item_id = item_with(session, "jira", "AUT-1", site=None)

    tasks.close_jira_issue(item_id)

    assert owed(session) == []


def test_giving_up_logs_the_id_a_person_would_clean_up_by_hand(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected(monkeypatch)
    Notion.fail = PermanentIntegrationError("forbidden", upstream_status=403)
    owe(session, "notion", "page-1")

    with capture_logs() as logs:
        tasks.drain_external_cleanup()

    (entry,) = [e for e in logs if e["event"] == "extraction_external_cleanup_refused"]
    assert entry["external_id"] == "page-1"


# --- the page is retitled before it goes to the trash (#768) ---------------------------


def _title(properties: dict) -> str:
    ((_name, value),) = properties.items()
    return value["title"][0]["text"]["content"]


def test_a_deleted_items_page_is_retitled_before_the_trash(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trash keeps a page restorable for 30 days; with this title the item's
    sentence is not what it keeps -- as for a decision (#669)."""
    connected(monkeypatch)
    item_id = item_with(session, "notion", "page-1")

    tasks.trash_notion_page(item_id)

    assert Notion.calls == ["retitle", "trash"]
    ((page, properties),) = Notion.retitled
    assert page == "page-1"
    assert _title(properties) == tasks.service.ITEM_DELETED_TEXT
    assert "릴리스 노트 정리" not in repr(properties)


def test_the_retry_retitles_too(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    connected(monkeypatch)
    owe(session, "notion", "page-1")

    tasks.drain_external_cleanup()

    assert Notion.calls == ["retitle", "trash"]


def test_a_teams_own_title_property_is_the_one_retitled(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    own = IntegrationConfig(
        service="notion", team_id=TEAM, secret="t", config={"action_properties": {"title": "Name"}}
    )
    monkeypatch.setattr(tasks, "load_integration", lambda _s, _t, _n: own)
    item_id = item_with(session, "notion", "page-1")

    tasks.trash_notion_page(item_id)

    ((_page, properties),) = Notion.retitled
    assert list(properties) == ["Name"]


def test_a_map_with_no_title_trashes_the_page_as_it_is_and_says_so(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    own = IntegrationConfig(
        service="notion", team_id=TEAM, secret="t", config={"action_properties": {"due": "Due"}}
    )
    monkeypatch.setattr(tasks, "load_integration", lambda _s, _t, _n: own)
    item_id = item_with(session, "notion", "page-1")

    with capture_logs() as logs:
        tasks.trash_notion_page(item_id)

    assert Notion.calls == ["trash"]
    assert "extraction_notion_item_trashed_without_retitle" in [e["event"] for e in logs]


def test_a_page_notion_will_not_edit_is_still_trashed(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page a person already archived: Notion refuses the retitle, and the
    trash call that follows treats it as done."""
    connected(monkeypatch)

    class Archived(Notion):
        def update_page(self, page_id: str, properties: dict) -> None:
            raise PermanentIntegrationError("archived", upstream_status=400)

    monkeypatch.setattr(tasks, "NotionClient", Archived)
    item_id = item_with(session, "notion", "page-1")

    tasks.trash_notion_page(item_id)

    assert Notion.trashed == ["page-1"]
    assert owed(session) == []


def test_a_retitle_that_times_out_is_owed_and_retried(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    connected(monkeypatch)

    class Slow(Notion):
        def update_page(self, page_id: str, properties: dict) -> None:
            raise TransientIntegrationError("timed out")

    monkeypatch.setattr(tasks, "NotionClient", Slow)
    item_id = item_with(session, "notion", "page-1")

    tasks.trash_notion_page(item_id)

    assert Notion.trashed == []
    assert owed(session) == [(TEAM, "notion", "page-1", None)]
