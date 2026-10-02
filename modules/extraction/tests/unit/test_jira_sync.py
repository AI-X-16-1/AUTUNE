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
from autune_extraction.jira_sync import (
    DELETED_NOTE,
    JIRA,
    close_for_deleted_item,
    sync_action_item_to_jira,
)
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtExternalRef,
    ExtNotionTarget,
)
from autune_integrations.fakes import FakeJira

from .conftest import CLOSE_JIRA_ISSUE, SYNC_ACTION_ITEM_JIRA

MEETING, ME, GONE = "mtg_1", "user_me", "user_gone"
SAID = "제가 금요일까지 스펙 초안 공유하겠습니다"
SITE = "https://acme.atlassian.net"
CLOUD = "cloud-1"

TABLES = [
    Meeting.__table__,
    User.__table__,
    TeamMember.__table__,
    Utterance.__table__,
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtExternalRef.__table__,
    ExtNotionTarget.__table__,
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
        session, jira, action_item_id=row.id, project_key="AUT", site=CLOUD, site_url=SITE
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
        "description": "",
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


def test_an_item_moved_back_keeps_its_issue_and_the_issue_follows_its_text(
    session: Session,
) -> None:
    """Confirmed once, then moved back to 확인 필요 (#657): the issue is the
    team's by then, so it stays, in the status they have it in -- but a line
    deleted or corrected in Autune must not stay behind in it."""
    jira = FakeJira(accounts={"me@example.com": "acc-me"})
    row = item(session)
    sync(session, jira, row)

    row.status = "needs_confirmation"
    row.description = "삭제된 발화에서 만든 항목"
    session.flush()
    ref = sync(session, jira, row)

    assert ref is not None and ref.external_id == "AUT-1"
    assert list(jira.tasks) == ["AUT-1"], "no second issue for a draft"
    assert jira.tasks["AUT-1"]["summary"] == "삭제된 발화에서 만든 항목"
    assert "스펙 초안 공유" not in str(jira.tasks)
    assert jira.categories["AUT-1"] == "new", "the status is left where the team has it"


def test_a_moved_back_item_whose_issue_is_gone_gets_no_new_one(session: Session) -> None:
    """A confirmed item whose issue was deleted in Jira is made again on the
    next edit. A draft is not: no issue is created for an unconfirmed item,
    whether it never had one or lost it."""
    jira = FakeJira()
    row = item(session)
    sync(session, jira, row)
    del jira.tasks["AUT-1"]

    row.status = "needs_confirmation"
    session.flush()

    assert sync(session, jira, row) is None

    assert jira.tasks == {}
    # #672: with the issue gone the ref row goes too, so the item is a draft
    # like any other and stops counting as one with a copy outside.
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


LONG = "스펙 초안을 정리하고\n공유 폴더에 올린 다음  리뷰를 받는다 " + "자세히 " * 60


def test_an_update_rewrites_the_description_create_wrote(session: Session) -> None:
    """#601 review: a long or multi-line line went into Jira's description at
    creation, and an update rewrote only the summary -- so a person deleting
    their own speech (#587) left their words in Jira. What Jira holds after the
    re-sync is checked, not what B meant to send."""
    jira = FakeJira(accounts={"me@example.com": "acc-me"})
    row = item(session, description=LONG)
    sync(session, jira, row)
    assert jira.tasks["AUT-1"]["description"] == LONG, "the full line at creation"

    row.description = "삭제된 발화에서 만든 항목"  # what forget_speech leaves
    sync(session, jira, row)

    task = jira.tasks["AUT-1"]
    assert (task["summary"], task["description"]) == ("삭제된 발화에서 만든 항목", "")
    assert "공유 폴더" not in str(jira.tasks)


def test_a_long_description_edited_is_rewritten_in_full(session: Session) -> None:
    jira = FakeJira(accounts={"me@example.com": "acc-me"})
    row = item(session, description=LONG)
    sync(session, jira, row)

    row.description = LONG.replace("스펙", "API 문서")
    sync(session, jira, row)

    assert jira.tasks["AUT-1"]["description"] == row.description


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
        tasks, "jira_access", lambda team, **kw: JiraAccess("acc-token", "cloud-1", "AUT")
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
    monkeypatch.setattr(tasks, "jira_access", lambda team, **kw: access)
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


# --- deleting an item closes its issue (#458, option C) -----------------------------


def test_deleting_an_item_closes_its_issue_with_a_note(session: Session) -> None:
    jira = FakeJira()
    row = item(session)
    sync(session, jira, row)

    assert close_for_deleted_item(session, jira, action_item_id=row.id, site=CLOUD) is True

    assert jira.categories["AUT-1"] == "done"
    assert jira.comments["AUT-1"] == [DELETED_NOTE]
    assert "AUT-1" in jira.tasks  # closed, not deleted


def test_an_item_that_never_became_an_issue_closes_nothing(session: Session) -> None:
    jira = FakeJira()
    row = item(session, status="needs_confirmation")
    assert close_for_deleted_item(session, jira, action_item_id=row.id, site=CLOUD) is False
    assert jira.comments == {}


def test_an_unreachable_jira_never_blocks_a_deletion(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(team: str, **kw: object) -> None:
        raise JiraReconnectRequiredError("refused")

    monkeypatch.setattr(tasks, "close_jira_issue", CLOSE_JIRA_ISSUE)
    monkeypatch.setattr(tasks, "jira_access", refused)

    tasks.close_jira_issue(item(wired).id)  # must not raise


# --- a new project gets everything back (#458) ---------------------------------------


def test_choosing_a_new_project_brings_every_confirmed_item_back(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old project was deleted with its issues: every confirmed item is
    made again in the new one; unconfirmed items stay in Autune."""

    class Closable(FakeJira):
        def close(self) -> None:
            pass

    fake = Closable()
    kept = item(wired, description="이미 보냈던 작업")
    wired.add(
        ExtExternalRef(
            action_item_id=kept.id,
            system=JIRA,
            meeting_id=MEETING,
            external_id="OLD-1",
            site="cloud-1",
        )
    )
    never_sent = item(wired, description="보내지 못했던 작업", status="done")
    item(wired, description="확인 대기", status="needs_confirmation")
    wired.flush()
    monkeypatch.setattr(
        tasks, "jira_access", lambda team, **kw: JiraAccess("acc-token", "cloud-1", "NEW")
    )
    monkeypatch.setattr(tasks.JiraClient, "for_cloud", staticmethod(lambda t, c: fake))

    counts = tasks.backfill_jira("team_1")

    assert counts == {"synced": 2, "failed": 0}
    assert sorted(t["summary"] for t in fake.tasks.values()) == [
        "보내지 못했던 작업",
        "이미 보냈던 작업",
    ]
    assert all(t["project"] == "NEW" for t in fake.tasks.values())
    refs = {r.action_item_id: r.external_id for r in wired.scalars(select(ExtExternalRef))}
    assert refs[kept.id].startswith("NEW-")
    assert refs[never_sent.id].startswith("NEW-")
    assert fake.categories[refs[never_sent.id]] == "done"


def test_no_project_chosen_backfills_nothing(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    item(wired)
    monkeypatch.setattr(tasks, "jira_access", lambda team, **kw: JiraAccess("t", "cloud-1", None))
    assert tasks.backfill_jira("team_1") == {"synced": 0, "failed": 0}


def test_the_sync_task_checks_the_project_still_exists(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[dict[str, object]] = []

    def access(team: str, **kw: object) -> None:
        asked.append(kw)
        return None

    monkeypatch.setattr(tasks, "jira_access", access)
    tasks.sync_action_item_jira(item(wired).id)
    assert asked == [{"check_project": True}]


def test_closing_an_issue_that_is_already_gone_is_not_a_failure(session: Session) -> None:
    """Its project was deleted, or the issue was: nothing to close (#458)."""
    from autune_integrations import PermanentIntegrationError

    class Gone(FakeJira):
        def move_to_category(self, issue_key: str, category: str) -> bool:
            raise PermanentIntegrationError("gone", upstream_status=404)

    jira = Gone()
    row = item(session)
    sync_action_item_to_jira(
        session, FakeJira(), action_item_id=row.id, project_key="AUT", site=CLOUD
    )

    assert close_for_deleted_item(session, jira, action_item_id=row.id, site=CLOUD) is False
    assert jira.comments == {}


# --- the team's workflow and the team's site are theirs (#458 review) ---------------


def test_an_edit_leaves_a_status_the_team_chose_within_the_category(session: Session) -> None:
    """A person moved the issue to "In Review" (indeterminate); an Autune edit
    to the due date of an in-progress item must not pull it back."""
    jira = FakeJira()
    row = item(session, status="in_progress")
    sync(session, jira, row)
    moved = list(jira.moves)

    row.due_date = date(2026, 10, 9)
    sync(session, jira, row)

    assert jira.moves == moved  # no second transition
    assert moved == [("AUT-1", "indeterminate")]


def test_a_new_todo_issue_is_not_moved_out_of_its_first_status(session: Session) -> None:
    jira = FakeJira()
    sync(session, jira, item(session))
    assert jira.moves == []  # created in a "new" status, left there


def test_a_key_from_another_site_is_never_written_to(session: Session) -> None:
    """After a reconnect to another site, its own KAN-1 is someone else's issue."""
    old_site, new_site = FakeJira(), FakeJira()
    row = item(session)
    sync(session, old_site, row)
    new_site.tasks["AUT-1"] = {
        "project": "AUT",
        "summary": "남의 이슈",
        "due": None,
        "assignee": "x",
    }

    ref = sync_action_item_to_jira(
        session, new_site, action_item_id=row.id, project_key="AUT", site="cloud-2"
    )

    assert new_site.tasks["AUT-1"]["summary"] == "남의 이슈"  # untouched
    assert ref is not None and ref.site == "cloud-2" and ref.external_id == "AUT-2"
    assert close_for_deleted_item(session, old_site, action_item_id=row.id, site=CLOUD) is False


def test_deleting_does_not_close_another_sites_issue(session: Session) -> None:
    jira = FakeJira()
    row = item(session)
    sync(session, jira, row)

    assert close_for_deleted_item(session, jira, action_item_id=row.id, site="cloud-2") is False
    assert jira.comments == {}
    assert jira.categories["AUT-1"] == "new"


def test_a_failed_move_keeps_the_issue_it_just_made(session: Session) -> None:
    """Rolling the key back would make a second issue on the next edit."""
    from autune_integrations import TransientIntegrationError

    class Busy(FakeJira):
        def move_to_category(self, issue_key: str, category: str) -> bool:
            raise TransientIntegrationError("429")

    jira = Busy()
    ref = sync(session, jira, item(session, status="done"))

    assert ref is not None and ref.external_id == "AUT-1"
    assert list(jira.tasks) == ["AUT-1"]


def test_an_assignee_jira_cannot_find_does_not_clear_one_set_by_hand(session: Session) -> None:
    jira = FakeJira()  # me@example.com is not visible
    row = item(session)
    sync(session, jira, row)
    jira.tasks["AUT-1"]["assignee"] = "acc-set-by-hand"

    row.description = "스펙 최종본 공유"
    sync(session, jira, row)

    assert jira.tasks["AUT-1"]["assignee"] == "acc-set-by-hand"


def test_no_assignee_in_autune_clears_jiras(session: Session) -> None:
    jira = FakeJira(accounts={"me@example.com": "acc-me"})
    row = item(session)
    sync(session, jira, row)

    row.assignee_id = None
    sync(session, jira, row)

    assert jira.tasks["AUT-1"]["assignee"] is None


def test_the_summary_is_one_line_within_jiras_limit(session: Session) -> None:
    jira = FakeJira()
    long = "가" * 300
    sync(session, jira, item(session, description=f"첫 줄\n둘째 줄 {long}"))

    summary = jira.tasks["AUT-1"]["summary"]
    assert "\n" not in summary
    assert len(summary) == 255 and summary.endswith("…")
