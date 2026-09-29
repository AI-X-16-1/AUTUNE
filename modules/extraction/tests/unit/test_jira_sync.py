"""A confirmed item as one Jira issue (#82): ``jira_sync`` and its task.

SQLite in memory, ``FakeJira`` for the team's site.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import Base, JiraAccess, Meeting, TeamMember, User, Utterance
from autune_core.integrations_config import IntegrationConfig
from autune_core.oauth.atlassian import JiraReconnectRequiredError
from autune_extraction import tasks
from autune_extraction.jira_sync import JIRA, sync_action_item_to_jira
from autune_extraction.models import ExtActionItem, ExtActionItemSource, ExtExternalRef
from autune_integrations.fakes import FakeJira

from .conftest import SYNC_ACTION_ITEM_JIRA

MEETING, ME, GONE = "mtg_1", "user_me", "user_gone"
SAID = "제가 금요일까지 스펙 초안 공유하겠습니다"
SITE = "https://acme.atlassian.net"

TABLES = [
    Meeting.__table__,
    User.__table__,
    TeamMember.__table__,
    Utterance.__table__,
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtExternalRef.__table__,
]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id="team_1", title="스프린트 회의"))
        s.add(User(id=ME, email="me@example.com", display_name="박지영"))
        s.add(User(id=GONE, email="gone@example.com", display_name="이건우"))
        s.add(TeamMember(team_id="team_1", user_id=ME))
        s.add(
            Utterance(
                id="utt_1",
                meeting_id=MEETING,
                speaker_label="S0",
                start_sec=0.0,
                end_sec=1.0,
                text=SAID,
            )
        )
        s.flush()
        yield s


def item(session: Session, *, status: str = "todo", **fields: Any) -> ExtActionItem:
    row = ExtActionItem(
        meeting_id=MEETING,
        description=fields.pop("description", "스펙 초안 공유"),
        assignee_id=fields.pop("assignee_id", ME),
        assignee_label=fields.pop("assignee_label", None),
        due_date=fields.pop("due_date", date(2026, 10, 7)),
        status=status,
        confidence=0.9,
        origin="model",
    )
    row.sources = [ExtActionItemSource(utterance_id="utt_1")]
    session.add(row)
    session.flush()
    return row


def sync(session: Session, jira: FakeJira, row: ExtActionItem) -> ExtExternalRef | None:
    return sync_action_item_to_jira(
        session, jira, action_item_id=row.id, project_key="AUT", site_url=SITE
    )


def test_a_confirmed_item_becomes_one_issue_assigned_and_dated(session: Session) -> None:
    jira = FakeJira(accounts={"me@example.com": "acc-me"})
    row = item(session)

    ref = sync(session, jira, row)
    sync(session, jira, row)

    assert ref is not None
    assert list(jira.tasks) == ["AUT-1"]
    assert ref.external_id == "AUT-1"
    assert ref.url == f"{SITE}/browse/AUT-1"
    task = jira.tasks["AUT-1"]
    assert task == {
        "project": "AUT",
        "summary": "스펙 초안 공유",
        "due": date(2026, 10, 7),
        "assignee": "acc-me",
    }
    assert jira.categories["AUT-1"] == "new"
    assert SAID not in str(jira.tasks)
    assert "스프린트 회의" not in str(jira.tasks)


def test_nothing_is_sent_before_confirmation(session: Session) -> None:
    jira = FakeJira()
    assert sync(session, jira, item(session, status="needs_confirmation")) is None
    assert jira.tasks == {}
    assert session.scalars(select(ExtExternalRef)).all() == []


@pytest.mark.parametrize(
    "fields",
    [
        {"assignee_id": None, "assignee_label": "김개발"},  # a spoken name, no account
        {"assignee_id": GONE},  # not on the team (ADR 0007)
    ],
)
def test_no_identified_team_member_means_unassigned_and_no_lookup(
    session: Session, fields: dict[str, Any]
) -> None:
    jira = FakeJira(accounts={"gone@example.com": "acc-gone"})
    sync(session, jira, item(session, **fields))
    assert jira.tasks["AUT-1"]["assignee"] is None
    assert jira.searched == []


def test_a_person_jira_will_not_reveal_leaves_it_unassigned(session: Session) -> None:
    jira = FakeJira()  # no accounts visible
    sync(session, jira, item(session))
    assert jira.tasks["AUT-1"]["assignee"] is None
    assert jira.searched == ["me@example.com"]


def test_edits_rewrite_the_same_issue_and_move_its_status(session: Session) -> None:
    jira = FakeJira(accounts={"me@example.com": "acc-me"})
    row = item(session)
    sync(session, jira, row)

    row.description = "스펙 최종본 공유"
    row.due_date = date(2026, 10, 9)
    row.status = "in_progress"
    sync(session, jira, row)
    assert jira.tasks["AUT-1"]["summary"] == "스펙 최종본 공유"
    assert jira.tasks["AUT-1"]["due"] == date(2026, 10, 9)
    assert jira.categories["AUT-1"] == "indeterminate"

    row.status = "done"
    sync(session, jira, row)
    assert jira.categories["AUT-1"] == "done"
    assert list(jira.tasks) == ["AUT-1"]


def test_an_issue_deleted_in_jira_is_made_again(session: Session) -> None:
    jira = FakeJira()
    row = item(session)
    ref = sync(session, jira, row)
    jira.tasks.clear()

    sync(session, jira, row)

    assert ref is not None
    assert ref.external_id == "AUT-1"
    assert list(jira.tasks) == ["AUT-1"]


# --- the task ------------------------------------------------------------------------


@pytest.fixture
def wired(session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    monkeypatch.setattr(tasks, "sync_action_item_jira", SYNC_ACTION_ITEM_JIRA)
    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(
        tasks,
        "load_integration",
        lambda _s, _t, _svc: IntegrationConfig(JIRA, "team_1", "r", {"site_url": SITE}),
    )
    return session


def test_the_task_uses_the_teams_fresh_access_and_chosen_project(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    jira = FakeJira()
    tokens: list[tuple[str, str]] = []

    class Closable(FakeJira):
        def close(self) -> None:
            pass

    fake = Closable()
    monkeypatch.setattr(
        tasks, "jira_access", lambda team: JiraAccess("acc-token", "cloud-1", "AUT")
    )

    def for_cloud(token: str, cloud: str) -> FakeJira:
        tokens.append((token, cloud))
        return fake

    monkeypatch.setattr(tasks.JiraClient, "for_cloud", staticmethod(for_cloud))

    tasks.sync_action_item_jira(item(wired).id)

    assert tokens == [("acc-token", "cloud-1")]
    assert list(fake.tasks) == ["AUT-1"]
    assert jira.tasks == {}


@pytest.mark.parametrize("access", [None, JiraAccess("t", "cloud-1", None)])
def test_a_team_not_connected_or_without_a_project_is_skipped(
    wired: Session, monkeypatch: pytest.MonkeyPatch, access: JiraAccess | None
) -> None:
    monkeypatch.setattr(tasks, "jira_access", lambda team: access)
    tasks.sync_action_item_jira(item(wired).id)
    assert wired.scalars(select(ExtExternalRef)).all() == []


def test_a_refused_grant_never_fails_the_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(_: str) -> None:
        raise JiraReconnectRequiredError("refused")

    monkeypatch.setattr(tasks, "sync_action_item", lambda _: None)
    monkeypatch.setattr(tasks, "sync_action_item_calendar", lambda _: None)
    monkeypatch.setattr(tasks, "sync_action_item_jira", refused)

    tasks.sync_after_confirmation("act_1")  # must not raise


def test_the_jira_task_does_not_retry_itself() -> None:
    assert not getattr(SYNC_ACTION_ITEM_JIRA, "autoretry_for", ())
