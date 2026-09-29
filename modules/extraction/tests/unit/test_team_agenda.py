"""A team's open Jira issues, published for D's pre-meeting brief (#436).

SQLite in memory; ``publish`` recorded instead of sent.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from autune_contracts import AGENDA_TITLE_MAX, EXTRACTION_AGENDA_CHANGED, TeamAgenda
from autune_core import Base, Meeting
from autune_extraction import service, tasks
from autune_extraction.models import ExtActionItem, ExtExternalRef

NOW = datetime(2026, 9, 29, 8, 50, tzinfo=UTC)
SITE = "https://example.atlassian.net"

TABLES = [Meeting.__table__, ExtActionItem.__table__, ExtExternalRef.__table__]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as session:
        session.add(Meeting(id="mtg_1", team_id="team_1", title="주간 회의"))
        session.add(Meeting(id="mtg_2", team_id="team_1", title="다음 주간 회의"))
        session.add(Meeting(id="mtg_x", team_id="team_other", title="다른 팀 회의"))
        session.flush()
        yield session


def item(
    session: Session,
    item_id: str,
    *,
    meeting_id: str = "mtg_1",
    status: str = "todo",
    due: date | None = None,
    key: str | None = "AUT-1",
    url: str | None = "",
    system: str = "jira",
    description: str | None = None,
) -> None:
    session.add(
        ExtActionItem(
            id=item_id,
            meeting_id=meeting_id,
            description=description or f"{item_id} 할 일",
            status=status,
            due_date=due,
            confidence=0.8,
            origin="model",
        )
    )
    session.flush()
    if key is not None:
        link = f"{SITE}/browse/{key}" if url == "" else url
        session.add(
            ExtExternalRef(
                action_item_id=item_id,
                system=system,
                meeting_id=meeting_id,
                external_id=key,
                url=link,
            )
        )
    session.flush()


def test_open_issues_come_most_pressing_first_with_their_link(session: Session) -> None:
    item(session, "act_later", key="AUT-2", due=date(2026, 10, 9))
    item(session, "act_undated", key="AUT-3")
    item(session, "act_soon", key="AUT-1", due=date(2026, 10, 1), status="in_progress")

    agenda = service.team_agenda(session, "team_1", now=NOW)

    assert [(i.key, i.status) for i in agenda.issues] == [
        ("AUT-1", "진행 중"),
        ("AUT-2", "할 일"),
        ("AUT-3", "할 일"),
    ]
    assert agenda.issues[0].url == f"{SITE}/browse/AUT-1"
    assert agenda.issues[0].title == "act_soon 할 일"
    assert (agenda.team_id, agenda.as_of) == ("team_1", NOW)


def test_only_open_jira_issues_of_this_team(session: Session) -> None:
    item(session, "act_open", key="AUT-1")
    item(session, "act_done", key="AUT-2", status="done")
    item(session, "act_unconfirmed", key=None, status="needs_confirmation")
    item(session, "act_notion", key="AUT-3", system="notion")
    item(session, "act_no_issue", key=None)
    item(session, "act_theirs", key="OTH-1", meeting_id="mtg_x")

    agenda = service.team_agenda(session, "team_1", now=NOW)

    assert [i.key for i in agenda.issues] == ["AUT-1"]


def test_a_link_of_the_wrong_shape_is_dropped_and_the_title_kept(session: Session) -> None:
    """D renders ``url`` as an ``href``; a stored value that is not an https
    issue page must not fail the snapshot, nor reach D."""
    item(session, "act_bad_url", key="AUT-1", url="javascript:alert(1)")
    item(session, "act_no_url", key="AUT-2", url=None)
    item(session, "act_odd_key", key="10042")

    issues = service.team_agenda(session, "team_1", now=NOW).issues

    assert [(i.key, i.url) for i in issues] == [
        ("AUT-1", None),
        ("AUT-2", None),
        (None, None),
    ]
    assert [i.title for i in issues] == [
        "act_bad_url 할 일",
        "act_no_url 할 일",
        "act_odd_key 할 일",
    ]


def test_a_title_is_one_line(session: Session) -> None:
    item(session, "act_1", description="  결제 API\n 명세   정리 ")

    (issue,) = service.team_agenda(session, "team_1", now=NOW).issues

    assert issue.title == "결제 API 명세 정리"


def test_the_list_is_capped(session: Session) -> None:
    for n in range(service.AGENDA_LIMIT + 5):
        item(session, f"act_{n:02d}", key=f"AUT-{n + 1}")

    assert len(service.team_agenda(session, "team_1", now=NOW).issues) == service.AGENDA_LIMIT


def test_a_team_whose_issues_all_closed_is_still_published_empty(session: Session) -> None:
    item(session, "act_done", key="AUT-1", status="done")
    item(session, "act_theirs", key="OTH-1", meeting_id="mtg_x")

    assert service.teams_with_jira_issues(session) == ["team_1", "team_other"]
    assert service.team_agenda(session, "team_1", now=NOW).issues == []


def test_the_task_publishes_one_snapshot_per_team(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    item(session, "act_1", key="AUT-1")
    item(session, "act_done", key="OTH-1", meeting_id="mtg_x", status="done")
    sent: list[tuple[str, dict[str, Any]]] = []

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "publish", lambda event, payload: sent.append((event, payload)))

    tasks.publish_team_agendas()

    assert [event for event, _ in sent] == [EXTRACTION_AGENDA_CHANGED] * 2
    agendas = {a.team_id: a for a in (TeamAgenda.model_validate(p) for _, p in sent)}
    assert [i.key for i in agendas["team_1"].issues] == ["AUT-1"]
    assert agendas["team_other"].issues == []


def test_no_team_with_an_issue_publishes_nothing(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    item(session, "act_1", key=None)

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "publish", lambda *_: pytest.fail("nothing to publish"))

    tasks.publish_team_agendas()


def test_a_long_title_is_cut_to_the_contracts_bound(session: Session) -> None:
    item(session, "act_long", description="가" * 500)

    (issue,) = service.team_agenda(session, "team_1", now=NOW).issues

    assert len(issue.title) == AGENDA_TITLE_MAX
    assert issue.title.endswith("…")
