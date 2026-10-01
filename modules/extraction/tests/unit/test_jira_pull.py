"""The Jira read-back: a status a person moved an issue to comes back to the board.

SQLite in memory and ``FakeJira`` with the one read the read-back adds. Under
test: which side wins when -- a move in Jira, a board edit Jira has not seen
yet, both at once -- and that an issue deleted in Jira, another site, or an
unconfirmed item is left alone.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_core import Base, JiraAccess, Meeting, TeamMember, User, Utterance
from autune_extraction import jira_sync, service, tasks
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import ExtActionItem, ExtEditEvent, ExtExternalRef
from autune_integrations import PermanentIntegrationError, TransientIntegrationError
from autune_integrations.fakes import FakeJira

TEAM, SITE = "team_1", "cloud-1"


@dataclass
class ReadableJira(FakeJira):
    """``FakeJira`` with ``status_category``, read from what the test set."""

    gone: frozenset[str] = frozenset()
    refused: dict[str, int] = field(default_factory=dict)
    on_read: Callable[[str], None] | None = None

    def status_category(self, issue_key: str) -> str | None:
        if issue_key in self.gone:
            raise PermanentIntegrationError("gone", upstream_status=404)
        if issue_key in self.refused:
            raise PermanentIntegrationError("refused", upstream_status=self.refused[issue_key])
        if self.on_read is not None:
            self.on_read(issue_key)
        return self.categories.get(issue_key)


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    shared = {m.__tablename__ for m in (Meeting, User, TeamMember, Utterance)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Meeting(id="mtg_1", team_id=TEAM, title="주간 회의"))
        s.add(Meeting(id="mtg_9", team_id="team_2", title="다른 팀 회의"))
        s.flush()
        yield s


def issued(
    session: Session,
    item_id: str,
    *,
    status: str = "todo",
    key: str | None = None,
    synced: str | None = "new",
    site: str = SITE,
    meeting: str = "mtg_1",
) -> None:
    session.add(
        ExtActionItem(
            id=item_id,
            meeting_id=meeting,
            description=f"{item_id} 할 일",
            status=status,
            confidence=0.9,
            origin="model",
        )
    )
    session.flush()
    session.add(
        ExtExternalRef(
            action_item_id=item_id,
            system=jira_sync.JIRA,
            meeting_id=meeting,
            external_id=key or f"KAN-{item_id}",
            site=site,
            synced_category=synced,
        )
    )
    session.flush()


def status_of(session: Session, item_id: str) -> str:
    return session.scalar(select(ExtActionItem.status).where(ExtActionItem.id == item_id)) or ""


def pull(session: Session, jira: ReadableJira, *, limit: int = jira_sync.PULL_LIMIT) -> list[str]:
    """One run, every issue in the test's one session."""
    return [
        item_id
        for item_id, key in jira_sync.pull_candidates(session, team_id=TEAM, site=SITE, limit=limit)
        if jira_sync.read_back(session, jira, item_id=item_id, key=key, site=SITE)
    ]


def test_an_issue_moved_to_done_in_jira_makes_the_item_done(session: Session) -> None:
    issued(session, "act_1")
    jira = ReadableJira(categories={"KAN-act_1": "done"})

    assert pull(session, jira) == ["act_1"]

    assert status_of(session, "act_1") == "done"
    ref = session.get(ExtExternalRef, ("act_1", jira_sync.JIRA))
    assert ref is not None and ref.synced_category == "done"
    # Through the board's edit path, like a date moved on a calendar.
    (event,) = session.scalars(select(ExtEditEvent)).all()
    assert event.fields == "status"


def test_each_category_maps_to_its_status(session: Session) -> None:
    issued(session, "act_a", status="done", synced="done")
    issued(session, "act_b", status="todo", synced="new")
    jira = ReadableJira(categories={"KAN-act_a": "new", "KAN-act_b": "indeterminate"})

    pull(session, jira)

    assert (status_of(session, "act_a"), status_of(session, "act_b")) == ("todo", "in_progress")


def test_a_board_edit_jira_has_not_seen_yet_is_not_undone(session: Session) -> None:
    """The board moved to in progress; the outgoing sync has not run. Jira still
    shows what Autune last left there, so there is nothing to read back."""
    issued(session, "act_1", status="in_progress", synced="new")
    jira = ReadableJira(categories={"KAN-act_1": "new"})

    assert pull(session, jira) == []
    assert status_of(session, "act_1") == "in_progress"


def test_when_both_moved_the_board_wins(session: Session) -> None:
    issued(session, "act_1", status="in_progress", synced="new")
    jira = ReadableJira(categories={"KAN-act_1": "done"})

    assert pull(session, jira) == []
    assert status_of(session, "act_1") == "in_progress"
    assert jira.moves == [], "nothing is sent from the read-back"


def test_a_move_both_sides_made_is_logged_once_not_every_run(session: Session) -> None:
    """mkkim68's #548 review: with no baseline moved, the same "both moved"
    came back every ten minutes. Jira's category is the new baseline."""
    issued(session, "act_1", status="in_progress", synced="new")
    jira = ReadableJira(categories={"KAN-act_1": "done"})

    with capture_logs() as first:
        pull(session, jira)
    with capture_logs() as second:
        pull(session, jira)

    events = [e["event"] for e in first]
    assert "extraction_jira_both_moved" in events
    assert [e["event"] for e in second if e["event"].startswith("extraction_jira")] == []
    ref = session.get(ExtExternalRef, ("act_1", jira_sync.JIRA))
    assert ref is not None and ref.synced_category == "done"
    assert status_of(session, "act_1") == "in_progress"


def test_an_issue_jira_refuses_to_show_is_skipped_and_the_rest_read(session: Session) -> None:
    """mkkim68's #548 review: a 403 raised, and stopped the team's read-back at
    the same issue every run."""
    issued(session, "act_hidden")
    issued(session, "act_1")
    jira = ReadableJira(categories={"KAN-act_1": "done"}, refused={"KAN-act_hidden": 403})

    assert pull(session, jira) == ["act_1"]
    assert status_of(session, "act_hidden") == "todo"


def test_a_refused_grant_stops_the_run(session: Session) -> None:
    issued(session, "act_1")
    jira = ReadableJira(categories={"KAN-act_1": "done"}, refused={"KAN-act_1": 401})

    with pytest.raises(PermanentIntegrationError):
        pull(session, jira)


def test_issues_past_the_limit_are_read_on_later_runs(session: Session) -> None:
    """Least recently read first: two issues, one per run, both read."""
    issued(session, "act_old")
    issued(session, "act_new")
    jira = ReadableJira(categories={"KAN-act_old": "done", "KAN-act_new": "done"})

    first = pull(session, jira, limit=1)
    second = pull(session, jira, limit=1)

    assert sorted(first + second) == ["act_new", "act_old"]


def test_a_read_overtaken_by_the_outgoing_sync_is_dropped(session: Session) -> None:
    """Jira is read with no row locked. If the outgoing sync moves the issue
    meanwhile, what was read predates it -- here "new", while the board's edit
    to in progress has just reached Jira -- and must not undo that edit."""
    issued(session, "act_1", status="in_progress", synced="new")

    def outgoing_lands(_key: str) -> None:
        session.execute(
            update(ExtExternalRef)
            .where(ExtExternalRef.action_item_id == "act_1")
            .values(synced_category="indeterminate")
        )

    jira = ReadableJira(categories={"KAN-act_1": "new"}, on_read=outgoing_lands)

    assert pull(session, jira) == []
    assert status_of(session, "act_1") == "in_progress"
    ref = session.get(ExtExternalRef, ("act_1", jira_sync.JIRA))
    assert ref is not None and ref.synced_category == "indeterminate"


def test_a_ref_with_no_baseline_records_jiras_and_leaves_the_board(session: Session) -> None:
    """A ref from before the read-back: the board may hold an edit Jira never
    got, so Jira's category becomes the baseline and nothing is read back."""
    issued(session, "act_1", status="done", synced=None)
    jira = ReadableJira(categories={"KAN-act_1": "indeterminate"})

    assert pull(session, jira) == []
    assert status_of(session, "act_1") == "done"
    ref = session.get(ExtExternalRef, ("act_1", jira_sync.JIRA))
    assert ref is not None and ref.synced_category == "indeterminate"


def test_a_move_after_the_baseline_is_read_back(session: Session) -> None:
    issued(session, "act_1", status="todo", synced=None)
    jira = ReadableJira(categories={"KAN-act_1": "new"})
    assert pull(session, jira) == []

    jira.categories["KAN-act_1"] = "done"

    assert pull(session, jira) == ["act_1"]
    assert status_of(session, "act_1") == "done"


@dataclass
class NoTransitionJira(ReadableJira):
    """A workflow with no transition into any other category."""

    def move_to_category(self, issue_key: str, category: str) -> bool:
        return self.categories.get(issue_key) == category


def test_a_board_edit_jira_has_no_transition_for_is_never_undone(session: Session) -> None:
    """#548 review: the first move finds no transition, so no baseline is written.
    The read-back must not take the board's status as Jira's and undo it --
    not on the next run, and not on every run after."""
    issued(session, "act_1", status="done", synced=None)
    jira = NoTransitionJira(categories={"KAN-act_1": "indeterminate"}, tasks={"KAN-act_1": {}})

    jira_sync.sync_action_item_to_jira(
        session, jira, action_item_id="act_1", project_key="KAN", site=SITE
    )
    ref = session.get(ExtExternalRef, ("act_1", jira_sync.JIRA))
    assert ref is not None and ref.synced_category is None

    assert pull(session, jira) == []
    assert pull(session, jira) == []
    assert status_of(session, "act_1") == "done"


def test_the_item_row_is_locked_and_before_its_ref(session: Session) -> None:
    """Deleting an item locks it, then its ref by cascade; the read-back takes
    the same order, and a board edit cannot land between its read and write."""
    issued(session, "act_1")
    locked: list[str] = []

    @event.listens_for(session, "do_orm_execute")
    def _record(state: ORMExecuteState) -> None:
        sql = str(state.statement.compile(dialect=postgresql.dialect()))
        if state.is_select and "FOR UPDATE" in sql and state.bind_mapper is not None:
            locked.append(state.bind_mapper.class_.__name__)

    pull(session, ReadableJira(categories={"KAN-act_1": "new"}))

    assert locked == ["ExtActionItem", "ExtExternalRef"]


def test_what_is_not_this_teams_confirmed_issue_on_this_site_is_left_alone(
    session: Session,
) -> None:
    issued(session, "act_draft", status="needs_confirmation")
    issued(session, "act_elsewhere", site="cloud-2")
    issued(session, "act_other_team", meeting="mtg_9")
    jira = ReadableJira(
        categories={
            "KAN-act_draft": "done",
            "KAN-act_elsewhere": "done",
            "KAN-act_other_team": "done",
        }
    )

    assert pull(session, jira) == []


def test_an_issue_deleted_in_jira_is_left_for_the_next_edit(session: Session) -> None:
    issued(session, "act_gone")
    issued(session, "act_1")
    jira = ReadableJira(categories={"KAN-act_1": "done"}, gone=frozenset({"KAN-act_gone"}))

    assert pull(session, jira) == ["act_1"]
    assert status_of(session, "act_gone") == "todo"


def test_one_teams_failure_stops_nobody_else_and_notion_follows(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    def pull_team(team_id: str) -> list[str]:
        if team_id == "team_a":
            raise RuntimeError("token refused")
        return {"team_b": ["act_b1"], "team_c": []}[team_id]

    synced: list[str] = []
    monkeypatch.setattr(tasks, "session_scope", scope)
    # team_integrations holds JSONB, which SQLite cannot create.
    monkeypatch.setattr(tasks, "_jira_teams", lambda _s: ["team_a", "team_b", "team_c"])
    monkeypatch.setattr(tasks, "_pull_jira_team", pull_team)
    monkeypatch.setattr(tasks, "sync_action_item", synced.append)

    tasks.pull_jira_changes()

    assert synced == ["act_b1"]


def test_the_outgoing_sync_records_the_category_it_left_the_issue_in(session: Session) -> None:
    issued(session, "act_1", status="in_progress", synced="new")
    jira = ReadableJira(categories={"KAN-act_1": "new"}, tasks={"KAN-act_1": {}})

    jira_sync.sync_action_item_to_jira(
        session, jira, action_item_id="act_1", project_key="KAN", site=SITE
    )

    ref = session.get(ExtExternalRef, ("act_1", jira_sync.JIRA))
    assert ref is not None and ref.synced_category == "indeterminate"
    assert pull(session, jira) == [], "Jira now shows what the board shows"


def test_a_failure_part_way_keeps_what_was_read_before_it(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One transaction per issue: a 429 on the second issue neither rolls the
    first back nor keeps it from Notion."""
    issued(session, "act_first")
    issued(session, "act_second")
    session.execute(
        update(ExtExternalRef)
        .where(ExtExternalRef.action_item_id == "act_second")
        .values(pulled_at=datetime(2026, 9, 30, tzinfo=UTC))
    )
    session.commit()

    @dataclass
    class ThrottledJira(ReadableJira):
        def status_category(self, issue_key: str) -> str | None:
            if issue_key == "KAN-act_second":
                raise TransientIntegrationError("slow down", upstream_status=429)
            return super().status_category(issue_key)

        def close(self) -> None:
            pass

    jira = ThrottledJira(categories={"KAN-act_first": "done"})

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    notion: list[str] = []
    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "_jira_teams", lambda _s: [TEAM])
    monkeypatch.setattr(tasks, "jira_access", lambda _t: JiraAccess("token", SITE, "KAN"))
    monkeypatch.setattr(tasks.JiraClient, "for_cloud", lambda *_a: jira)
    monkeypatch.setattr(tasks, "sync_action_item", notion.append)

    tasks.pull_jira_changes()

    assert notion == ["act_first"]
    assert status_of(session, "act_first") == "done"
