"""A confirmed decision as one Jira issue (the user, 2026-10-04).

SQLite in memory, ``FakeJira`` for the team's site. The rules: nothing before
a person confirms (#246); "[결정] " and the statement that stands, filed as
done; a rewording rewrites the same issue; a decision put back, deleted or
dropped retires its issue -- retitled, emptied, a note, closed -- and the row
forgets it; a key from another site is never written to.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import Base, JiraAccess, Meeting
from autune_extraction import service, tasks
from autune_extraction.jira_sync import (
    DECISION_RETIRED_NOTE,
    JIRA,
    decision_issues_to_retire,
    sync_decision_to_jira,
)
from autune_extraction.models import (
    ExtDecision,
    ExtDecisionRef,
    ExtDecisionRelated,
    ExtDecisionReview,
    ExtDecisionSource,
)
from autune_integrations import PermanentIntegrationError
from autune_integrations.fakes import FakeJira

from .conftest import SYNC_DECISION_JIRA

MEETING = "mtg_1"
CLOUD = "cloud-1"
SITE = "https://acme.atlassian.net"
STATEMENT = "배포는 금요일 오후로 한다"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[
            Meeting.__table__,
            ExtDecision.__table__,
            ExtDecisionReview.__table__,
            ExtDecisionRef.__table__,
            ExtDecisionSource.__table__,
            ExtDecisionRelated.__table__,
        ],
    )
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id="team_1", title="스프린트 회의"))
        s.add(
            ExtDecision(
                id="dec_1",
                meeting_id=MEETING,
                statement=STATEMENT,
                confidence=0.9,
                origin="model",
            )
        )
        s.flush()
        yield s


def review(session: Session, status: str, statement: str | None = None) -> None:
    row = session.get(ExtDecisionReview, "dec_1")
    if row is None:
        row = ExtDecisionReview(decision_id="dec_1", meeting_id=MEETING)
        session.add(row)
    row.status = status
    row.statement = statement
    row.reviewed_at = datetime(2026, 10, 4, tzinfo=UTC)
    session.flush()


def sync(session: Session, jira: FakeJira, *, site: str = CLOUD) -> ExtDecisionRef | None:
    return sync_decision_to_jira(
        session, jira, decision_id="dec_1", project_key="TEAM", site=site, site_url=SITE
    )


def test_nothing_goes_before_a_person_confirms(session: Session) -> None:
    jira = FakeJira()
    review(session, "pending")

    assert sync(session, jira) is None
    assert jira.tasks == {}
    assert session.get(ExtDecisionRef, ("dec_1", JIRA)) is None


def test_a_confirmed_decision_is_one_issue_filed_as_done(session: Session) -> None:
    jira = FakeJira()
    review(session, "confirmed")

    ref = sync(session, jira)

    assert ref is not None and ref.external_id == "TEAM-1"
    assert (ref.site, ref.url) == (CLOUD, f"{SITE}/browse/TEAM-1")
    assert jira.tasks["TEAM-1"]["summary"] == f"[결정] {STATEMENT}"
    assert jira.tasks["TEAM-1"]["description"] == "", "the summary holds it all"
    assert jira.categories["TEAM-1"] == "done", "a decision is settled, not work"

    sync(session, jira)
    assert list(jira.tasks) == ["TEAM-1"], "a second sync makes no second issue"


def test_a_rewording_rewrites_the_same_issue(session: Session) -> None:
    jira = FakeJira()
    review(session, "confirmed")
    sync(session, jira)

    review(session, "confirmed", "배포는 다음 주 월요일로 한다")
    sync(session, jira)

    assert list(jira.tasks) == ["TEAM-1"]
    assert jira.tasks["TEAM-1"]["summary"] == "[결정] 배포는 다음 주 월요일로 한다"


def test_a_long_statement_goes_whole_into_the_description(session: Session) -> None:
    jira = FakeJira()
    long = "가" * 300
    review(session, "confirmed", long)

    sync(session, jira)

    issue = jira.tasks["TEAM-1"]
    assert len(issue["summary"]) == 255 and issue["summary"].endswith("…")
    assert issue["description"] == long


@pytest.mark.parametrize("how", ["put_back", "deleted"])
def test_a_decision_no_longer_settled_retires_its_issue(session: Session, how: str) -> None:
    jira = FakeJira()
    review(session, "confirmed")
    sync(session, jira)

    if how == "put_back":
        review(session, "rejected")
    else:
        decision = session.get(ExtDecision, "dec_1")
        session.delete(decision)
        session.flush()
    ref = sync(session, jira)

    issue = jira.tasks["TEAM-1"]
    assert issue["summary"] == service.DECISION_PUT_BACK_TEXT
    assert issue["description"] == "", "the statement is not what the issue keeps"
    assert jira.comments["TEAM-1"] == [DECISION_RETIRED_NOTE]
    assert jira.categories["TEAM-1"] == "done"
    assert ref is not None and ref.external_id is None and ref.url is None

    if how == "put_back":
        review(session, "confirmed")
        again = sync(session, jira)
        assert again is not None and again.external_id == "TEAM-2", "confirming again: a new one"


def test_an_issue_deleted_in_jira_is_only_forgotten(session: Session) -> None:
    class Gone(FakeJira):
        def update_task(self, *_: Any, **__: Any) -> bool:
            raise PermanentIntegrationError("gone", upstream_status=404)

    review(session, "confirmed")
    sync(session, FakeJira())
    review(session, "rejected")

    ref = sync(session, Gone())

    assert ref is not None and ref.external_id is None


def test_a_key_from_another_site_is_never_written(session: Session) -> None:
    jira = FakeJira()
    review(session, "confirmed")
    sync(session, jira, site="cloud-old")

    ref = sync(session, jira)

    assert ref is not None and ref.site == CLOUD and ref.external_id == "TEAM-2"
    review(session, "rejected")
    other = FakeJira()
    sync(session, other, site="cloud-elsewhere")
    assert other.tasks == {} and other.comments == {}, "nothing written on a site not its own"


def test_the_sweep_lists_issues_of_decisions_no_longer_settled(session: Session) -> None:
    review(session, "confirmed")
    sync(session, FakeJira())
    assert decision_issues_to_retire(session) == []

    review(session, "pending")

    assert decision_issues_to_retire(session) == [("dec_1", MEETING)]
    assert service.decision_has_page(session, "dec_1"), "a Jira issue counts as a copy"


def test_the_task_makes_the_issue_with_the_teams_access(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    jira = FakeJira()
    review(session, "confirmed")

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(
        tasks,
        "jira_access",
        lambda _team, **_kw: JiraAccess(access_token="t", cloud_id=CLOUD, project_key="TEAM"),
    )
    monkeypatch.setattr(tasks, "load_integration", lambda *_: None)
    monkeypatch.setattr(tasks.JiraClient, "for_cloud", classmethod(lambda *_: jira))
    monkeypatch.setattr(jira, "close", lambda: None, raising=False)

    SYNC_DECISION_JIRA("dec_1")

    assert jira.tasks["TEAM-1"]["summary"] == f"[결정] {STATEMENT}"


def test_a_notion_failure_never_costs_jira_its_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []

    def notion_down(_decision_id: str) -> None:
        raise PermanentIntegrationError("notion refused")

    monkeypatch.setattr(tasks, "_sync_decision_notion", notion_down)
    monkeypatch.setattr(tasks, "sync_decision_jira", ran.append)

    tasks.sync_decision_after_confirmation("dec_1")
    assert ran == ["dec_1"]

    with pytest.raises(PermanentIntegrationError):
        tasks.sync_decision("dec_1")
    assert ran == ["dec_1", "dec_1"]
