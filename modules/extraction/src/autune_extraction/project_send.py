"""A meeting's minutes, one per project, sent to the team's Notion, Slack and Jira.

The second half of what the user asked for on 2026-10-04: what a meeting
settled, split by the team's projects (``projects``), goes out as
"팀-프로젝트-날짜" with the project's decisions and items under it -- by a
person pressing a button on the 요약 tab, never on its own.

**Only what a person confirmed leaves** (#246): a confirmed decision, in the
wording the reviewer settled, and an item out of 확인 필요. A project with
nothing confirmed sends nothing, and 미분류 rows are not sent at all -- they
belong to no project to send them as.

**Where each copy goes**:

- Notion: a page in the team's 회의록 database (made by the one-click setup
  for this, ``notion_setup.MINUTES_NOTION_PROPERTIES``), titled and dated,
  the minutes as its body.
- Slack: a message in the team's alert channel.
- Jira: a task in the project's own Jira project, or the team's when the
  project names none.
- Google Calendar: an all-day event on the meeting's day in the calendar of
  **the person who pressed send** -- their own calendar, by their own click.
  Team work is not copied into anybody else's (``calendar_sync``). The event
  carries its own private tag, not the due-date events' one, so the due-date
  read-back never takes it for an item. It goes when the meeting expires, the
  person's account is deleted or the project is (``ext_minutes_events``).

Sending again updates the same copy (``ext_project_sends``): the Slack message
and the Jira task are rewritten; a Notion page's body cannot be replaced in one
call, so a new page is made first and only then is the old one emptied and
trashed -- a failed send never leaves the team with no page.

**Copies follow what changes after they went** (#787 review): a person deleting
their own speech, a decision taken back, a line masked again -- ``refresh``
rewrites each copy the meeting has, and a project left with nothing confirmed
has its copies retracted: the Notion page emptied and trashed, the Slack
message deleted, the Jira task emptied and closed. A deleted meeting or project
queues its copies in ``ext_project_send_cleanup`` for the same retraction
(``tasks.drain_project_send_cleanup``).

**A refresh can be repeated, and is until it has worked.** Each copy's row
keeps a digest of the minutes it last received, and ``refresh`` leaves a copy
alone that already says what is confirmed now: it costs nothing to refresh on
every change, and a second try rewrites only what the first one missed. A
refresh that leaves a copy behind -- a tool down, a token expired, not
connected -- records the meeting in ``ext_project_refresh_owed``, and
``tasks.retry_project_minutes_refresh`` tries again.

**What leaves** is the same text the item and decision syncs already send to
the same tools -- descriptions, statements, the assignee's name and the due
date -- plus the team's and the project's names. Every request goes through
``HttpClient`` and its outbound check. One tool failing never stops the others,
and the person is told which copy went and which did not.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_core import Meeting, PrivacyViolationError, Team, get_logger
from autune_integrations.errors import PermanentIntegrationError
from autune_integrations.privacy import MAX_OUTBOUND_CHARS, strings_in

from . import service
from .models import (
    ExtActionItem,
    ExtCalendarCleanup,
    ExtDecision,
    ExtMinutesEvent,
    ExtProject,
    ExtProjectRefreshOwed,
    ExtProjectSend,
    ExtProjectSendCleanup,
)
from .notion_setup import MINUTES_NOTION_PROPERTIES
from .slots import meeting_day

log = get_logger(__name__)

TARGETS = ("notion", "slack", "jira", "calendar")
MINUTES_TAG = ("autune_minutes", "1")
"""The private property on a minutes event. Not ``calendar_sync.TAG``: the
due-date read-back asks Google for that one, and must not see these."""

REQUEST_BUDGET = MAX_OUTBOUND_CHARS - 1000
"""Text one request carries, under the outbound check's limit with room for the
title, the meeting id and Slack's or Jira's own fields. Longer minutes go to
Notion over several requests, and are cut short for Slack and Jira."""

RETRACTED_TITLE = "Autune에서 내린 회의록"
"""What a copy says on its way out: no meeting content, so the trash and Jira's
history keep no sentence of it."""

RETRACTED_NOTE = "회의 내용이 바뀌거나 삭제되어 Autune에서 내린 회의록입니다."


@dataclass(frozen=True)
class Minutes:
    project_id: str
    project_name: str
    title: str
    """"팀-프로젝트-YYYY-MM-DD"."""
    day: date | None
    decisions: tuple[str, ...]
    items: tuple[str, ...]
    jira_project_key: str | None

    @property
    def text(self) -> str:
        parts = [self.title, ""]
        if self.decisions:
            parts += ["결정", *[f"- {d}" for d in self.decisions], ""]
        if self.items:
            parts += ["할 일", *[f"- {i}" for i in self.items], ""]
        return "\n".join(parts).rstrip()


def _digest(m: Minutes) -> str:
    """What ``ExtProjectSend.content_digest`` holds for a copy that received
    ``m``: a hash of everything the copy shows, not reversible."""
    return service.source_digest([m.text])


def _fit(text: str, budget: int = REQUEST_BUDGET) -> str:
    """``text`` cut at a line so it fits one request, saying how much was left
    out. Nothing is added but a count."""
    if len(text) <= budget:
        return text
    lines = text.split("\n")
    kept: list[str] = []
    used = 0
    for n, line in enumerate(lines):
        if used + len(line) + 1 > budget - 40:
            return "\n".join([*kept, f"... 외 {len(lines) - n}줄은 Autune에서 볼 수 있습니다."])
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept)


@dataclass(frozen=True)
class Sent:
    project_id: str
    project_name: str
    target: str
    outcome: str
    """``created``, ``updated``, ``retracted``, ``not_connected``, ``no_date``
    (a calendar event for a meeting with no recorded day) or ``failed``;
    from ``refresh`` also ``unchanged``, a copy that already says the minutes."""


@dataclass
class Clients:
    """The team's tools, each with where to write, or ``None`` when not connected."""

    notion: tuple[Any, str] | None = None
    """A ``NotionClient`` and the 회의록 database id."""
    slack: tuple[Any, str] | None = None
    """A ``SlackClient`` and the alert channel id."""
    jira: tuple[Any, str | None] | None = None
    """A ``JiraClient`` and the team's default project key."""
    calendar: tuple[Any, str, str] | None = None
    """A ``CalendarClient``, the calendar id, and whose calendar it is."""
    calendar_failed: bool = False
    """The sender's calendar could not be reached for a reason other than a
    missing or refused grant -- a timeout, Google down: ``failed``, not
    ``not_connected``."""


def _item_line(item: Any) -> str:
    who = item.assignee_name or item.assignee_label
    tail = [f"담당 {who}"] if who else []
    if item.due_date:
        tail.append(f"기한 {item.due_date.isoformat()}")
    return item.description + (f" ({', '.join(tail)})" if tail else "")


def minutes(session: Session, meeting_id: str) -> tuple[list[Minutes], int]:
    """Each project's minutes for the meeting, and how many confirmed rows have
    no project (미분류) and so are not sent."""
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        return [], 0
    team = session.get(Team, meeting.team_id)
    day = meeting_day(meeting.started_at)
    stamp = day.isoformat() if day else "날짜 미상"
    placed = {
        decision_id: project_id
        for decision_id, project_id in session.execute(
            select(ExtDecision.id, ExtDecision.project_id).where(
                ExtDecision.meeting_id == meeting_id
            )
        ).tuples()
    }
    review = service.review_for_meeting(session, meeting_id)
    decisions: dict[str | None, list[str]] = {}
    for d in review.decisions:
        if d.status == "confirmed":
            decisions.setdefault(placed.get(d.id), []).append(d.statement)
    items: dict[str | None, list[str]] = {}
    for item in service.list_action_items(session, meeting_id=meeting_id):
        if item.status != ActionStatus.NEEDS_CONFIRMATION.value:
            items.setdefault(item.project_id, []).append(_item_line(item))
    out: list[Minutes] = []
    for project in session.scalars(
        select(ExtProject)
        .where(ExtProject.team_id == meeting.team_id)
        .order_by(ExtProject.created_at, ExtProject.id)
    ):
        ds, its = decisions.get(project.id, []), items.get(project.id, [])
        if not ds and not its:
            continue
        out.append(
            Minutes(
                project_id=project.id,
                project_name=project.name,
                title=f"{team.name if team else '팀'}-{project.name}-{stamp}",
                day=day,
                decisions=tuple(ds),
                items=tuple(its),
                jira_project_key=project.jira_project_key,
            )
        )
    unsorted = len(decisions.get(None, [])) + len(items.get(None, []))
    return out, unsorted


def _text(content: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": content[: service.NOTION_TEXT_LIMIT]}}]


def _notion_blocks(m: Minutes) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = []
    for heading, lines in (("결정", m.decisions), ("할 일", m.items)):
        if not lines:
            continue
        children.append({"type": "heading_3", "heading_3": {"rich_text": _text(heading)}})
        children += [
            {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _text(line)}}
            for line in lines
        ]
    return children


def _block_chars(block: dict[str, Any]) -> int:
    """What a block costs the outbound check, which counts every string in a
    body -- ``"bulleted_list_item"`` and ``"text"`` too, not only the line."""
    return len("".join(strings_in(block)))


def _chunks(blocks: list[dict[str, Any]], first: int) -> list[list[dict[str, Any]]]:
    """Blocks in runs that each fit one request; the first run shares its
    request with the page's properties (``first`` characters of them)."""
    runs: list[list[dict[str, Any]]] = [[]]
    used = first
    for block in blocks:
        size = _block_chars(block)
        if runs[-1] and (used + size > REQUEST_BUDGET or len(runs[-1]) >= 100):
            runs.append([])
            used = 0
        runs[-1].append(block)
        used += size
    return runs


def _create_notion_page(notion: Any, m: Minutes, meeting_id: str, database_id: str) -> str:
    """The page, its first blocks in the same request and the rest appended a
    request at a time, each under the outbound limit. A page whose appends
    fail is emptied and trashed before the error goes on: no half a page."""
    names = MINUTES_NOTION_PROPERTIES
    properties: dict[str, Any] = {
        names["title"]: {"title": _text(m.title)},
        names["meeting"]: {"rich_text": _text(meeting_id)},
    }
    if m.day is not None:
        properties[names["date"]] = {"date": {"start": m.day.isoformat()}}
    page = {"parent": {"database_id": database_id}, "properties": properties}
    first, *rest = _chunks(_notion_blocks(m), len("".join(strings_in(page))))
    made = notion.request("POST", "/pages", json={**page, "children": first})
    page_id = str(made.get("id", ""))
    try:
        for run in rest:
            notion.request("PATCH", f"/blocks/{page_id}/children", json={"children": run})
    except Exception as failed:
        try:
            _retract_notion(notion, page_id)
        except Exception as exc:  # noqa: BLE001 -- owed, not lost
            # Half a page is live and no row will ever name it: its address
            # goes up with the error so the caller can queue the retraction.
            log.warning("extraction_project_partial_page_kept", error=type(exc).__name__)
            raise _PartialPageError(page_id) from failed
        raise
    return page_id


def _retract_notion(notion: Any, page_id: str) -> str:
    """Empty a minutes page -- title and every block -- then trash it, so the
    trash keeps no sentence (the way a decision's page goes, #670). ``gone``
    for a page already deleted, or already in the trash where nothing can be
    edited."""
    title = MINUTES_NOTION_PROPERTIES["title"]
    try:
        notion.update_page(page_id, {title: {"title": _text(RETRACTED_TITLE)}})
    except PermanentIntegrationError:
        if notion.page_state(page_id) == "live":
            raise
        return "gone"
    cursor: str | None = None
    while True:
        listed = notion.request(
            "GET",
            f"/blocks/{page_id}/children",
            params={"page_size": 100, **({"start_cursor": cursor} if cursor else {})},
        )
        for block in listed.get("results", []):
            notion.request("DELETE", f"/blocks/{block['id']}")
        cursor = listed.get("next_cursor")
        if not listed.get("has_more") or not cursor:
            break
    notion.trash_page(page_id)
    return "retracted"


def retract(target: str, external_id: str, clients: Clients) -> str:
    """Take one copy out of its tool: ``retracted``, ``gone`` (nothing left to
    take out) or ``not_connected``. Raises what the tool raises."""
    if not external_id:
        return "gone"
    if target == "notion":
        if clients.notion is None:
            return "not_connected"
        return _retract_notion(clients.notion[0], external_id)
    if target == "slack":
        if clients.slack is None:
            return "not_connected"
        channel, _, ts = external_id.partition(":")
        answer = clients.slack[0].request(
            "POST", "/chat.delete", json={"channel": channel, "ts": ts}
        )
        if not answer.get("ok", True):
            if answer.get("error") in ("message_not_found", "channel_not_found"):
                return "gone"
            raise PermanentIntegrationError(f"slack refused the delete: {answer.get('error')}")
        return "retracted"
    if clients.jira is None:
        return "not_connected"
    jira = clients.jira[0]
    if not jira.update_task(
        external_id,
        RETRACTED_TITLE,
        due_date=None,
        assignee_account_id=None,
        keep_assignee=True,
        description=RETRACTED_NOTE,
    ):
        return "gone"
    jira.move_to_category(external_id, "done")
    return "retracted"


def _connected(target: str, m: Minutes, clients: Clients) -> bool:
    if target == "notion":
        return clients.notion is not None
    if target == "slack":
        return clients.slack is not None
    return clients.jira is not None and bool(m.jira_project_key or clients.jira[1])


def _write(m: Minutes, meeting_id: str, target: str, old: str, clients: Clients) -> str:
    """The copy written, over ``old`` when there is one; its new address."""
    if target == "notion":
        assert clients.notion is not None
        notion, database_id = clients.notion
        page_id = _create_notion_page(notion, m, meeting_id, database_id)
        if old:
            try:
                _retract_notion(notion, old)
            except Exception as exc:  # noqa: BLE001 -- owed, not lost
                # The new page is there; the old one is retracted later.
                log.warning("extraction_project_old_page_kept", error=type(exc).__name__)
                raise _OldCopyOwedError(page_id) from exc
        return page_id
    if target == "slack":
        assert clients.slack is not None
        slack, channel = clients.slack
        if old and ":" in old:
            at, ts = old.split(":", 1)
            slack.update_message(at, ts, _fit(m.text))
            return old
        return f"{channel}:{slack.post_message(channel, _fit(m.text))}"
    assert clients.jira is not None
    jira, default_key = clients.jira
    description = _fit(m.text)
    if old and jira.update_task(
        old,
        m.title,
        due_date=None,
        assignee_account_id=None,
        keep_assignee=True,
        description=description,
    ):
        return old
    key = m.jira_project_key or default_key
    return str(jira.create_task(key, m.title, description=description))


class _OldCopyOwedError(Exception):
    """A new Notion page went up but the old one could not be retracted."""

    def __init__(self, page_id: str) -> None:
        super().__init__(page_id)
        self.page_id = page_id


class _PartialPageError(Exception):
    """A new Notion page got only part of its body and could not be retracted."""

    def __init__(self, page_id: str) -> None:
        super().__init__(page_id)
        self.page_id = page_id


def _event_body(m: Minutes) -> dict[str, Any]:
    """An all-day event on the meeting's day that leaves the day free:
    ``transparency: transparent`` -- minutes are a note, not a commitment, and
    must not mark the sender busy. Google's all-day end date is exclusive."""
    assert m.day is not None
    return {
        "summary": m.title,
        "description": _fit(m.text),
        "start": {"date": m.day.isoformat()},
        "end": {"date": (m.day + timedelta(days=1)).isoformat()},
        "transparency": "transparent",
    }


def _write_event(calendar: Any, calendar_id: str, m: Minutes, old: str) -> str:
    """The event written over ``old`` when it is still there; otherwise a new
    one, tagged. One deleted by hand -- 404, 410, or kept by Google as
    ``cancelled`` -- is replaced, never revived (as ``update_all_day_event``)."""
    body = _event_body(m)
    if old:
        try:
            answer: dict[str, Any] | None = calendar.request(
                "PATCH", f"/calendars/{calendar_id}/events/{old}", json=body
            )
        except PermanentIntegrationError as exc:
            if exc.details.get("upstream_status") not in (404, 410):
                raise
            answer = None
        if answer is not None and answer.get("status") != "cancelled":
            return old
    body["extendedProperties"] = {"private": {MINUTES_TAG[0]: MINUTES_TAG[1]}}
    made = calendar.request("POST", f"/calendars/{calendar_id}/events", json=body)
    return str(made.get("id", ""))


def _to_calendar(session: Session, meeting_id: str, m: Minutes, clients: Clients) -> str:
    """The minutes as an all-day event on the sender's own calendar, claimed
    the same way as a team copy (``_send_one``): the row first, the event
    after, in one savepoint -- a double click makes one event, and an event is
    never made without a row that will take it out again."""
    if clients.calendar_failed:
        return "failed"
    if clients.calendar is None:
        return "not_connected"
    if m.day is None:
        return "no_date"
    calendar, calendar_id, user_id = clients.calendar
    key = {"meeting_id": meeting_id, "project_id": m.project_id, "user_id": user_id}
    with session.begin_nested():
        session.execute(
            service._insert_if_absent_into(session, ExtMinutesEvent)
            .values(**key, event_id="")
            .on_conflict_do_nothing(index_elements=["meeting_id", "project_id", "user_id"])
        )
        row = session.execute(
            select(ExtMinutesEvent)
            .filter_by(**key)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
        old = row.event_id
        row.event_id = _write_event(calendar, calendar_id, m, old)
        row.content_digest = _digest(m)
        session.flush()
    return "updated" if old else "created"


def _retract_event(session: Session, row: ExtMinutesEvent, calendar: Any, calendar_id: str) -> str:
    """A minutes event off its owner's calendar, and its row with it."""
    with session.begin_nested():
        if row.event_id:
            calendar.delete_event(calendar_id, row.event_id)
        session.delete(row)
        session.flush()
    return "retracted"


def _send_one(session: Session, meeting_id: str, m: Minutes, target: str, clients: Clients) -> str:
    """One copy out, in its own savepoint; returns the outcome.

    The row is claimed before anything is sent -- ``INSERT ... ON CONFLICT DO
    NOTHING`` and then a locked read -- so a second person sending at the same
    moment waits for the first and updates the copy the first one made,
    instead of making another. A failure rolls back this copy's row only, and
    with it the digest: the copy still counts as not saying these minutes."""
    if target == "calendar":
        return _to_calendar(session, meeting_id, m, clients)
    if not _connected(target, m, clients):
        return "not_connected"
    key = {"meeting_id": meeting_id, "project_id": m.project_id, "target": target}
    try:
        with session.begin_nested():
            session.execute(
                service._insert_if_absent_into(session, ExtProjectSend)
                .values(**key, external_id="")
                .on_conflict_do_nothing(index_elements=["meeting_id", "project_id", "target"])
            )
            row = session.execute(
                select(ExtProjectSend)
                .filter_by(**key)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one()
            old = row.external_id
            try:
                row.external_id = _write(m, meeting_id, target, old, clients)
            except _OldCopyOwedError as owed:
                row.external_id = owed.page_id
                _queue(session, meeting_team(session, meeting_id), [(target, old)])
            row.content_digest = _digest(m)
            session.flush()
    except _PartialPageError as kept:
        # Outside the savepoint that just rolled back: this must be recorded.
        _queue(session, meeting_team(session, meeting_id), [(target, kept.page_id)])
        raise
    return "updated" if old else "created"


def meeting_team(session: Session, meeting_id: str) -> str | None:
    meeting = session.get(Meeting, meeting_id)
    return meeting.team_id if meeting is not None else None


def _queue(session: Session, team_id: str | None, copies: Sequence[tuple[str, str]]) -> int:
    """Copies to retract later, into ``ext_project_send_cleanup``. Safe to run
    twice (unique per team, tool and address)."""
    values = [
        {"team_id": team_id, "target": target, "external_id": external}
        for target, external in copies
        if external
    ]
    if team_id is None or not values:
        return 0
    session.execute(
        service._insert_if_absent_into(session, ExtProjectSendCleanup)
        .values(values)
        .on_conflict_do_nothing(index_elements=["team_id", "target", "external_id"])
    )
    return len(values)


def _queue_events(session: Session, events: Sequence[tuple[str, str]]) -> int:
    """Minutes events to take off their owners' calendars, into the due-date
    events' own queue (``ext_calendar_cleanup``) -- each goes with its owner's
    grant. Safe to run twice."""
    values = [{"user_id": user, "event_id": event} for user, event in events if event]
    if not values:
        return 0
    session.execute(
        service._insert_if_absent_into(session, ExtCalendarCleanup)
        .values(values)
        .on_conflict_do_nothing(index_elements=["user_id", "event_id"])
    )
    return len(values)


def queue_meeting(session: Session, meeting_id: str) -> int:
    """Before a meeting goes: every copy of its minutes, queued for retraction
    -- the team's tools and the senders' own calendars."""
    rows = session.execute(
        select(ExtProjectSend.target, ExtProjectSend.external_id).where(
            ExtProjectSend.meeting_id == meeting_id
        )
    ).tuples()
    events = session.execute(
        select(ExtMinutesEvent.user_id, ExtMinutesEvent.event_id).where(
            ExtMinutesEvent.meeting_id == meeting_id
        )
    ).tuples()
    return _queue(session, meeting_team(session, meeting_id), list(rows)) + _queue_events(
        session, list(events)
    )


def queue_project(session: Session, project_id: str) -> int:
    """Before a project goes: every copy of its minutes, queued for retraction
    -- the team's tools and the senders' own calendars."""
    project = session.get(ExtProject, project_id)
    if project is None:
        return 0
    rows = session.execute(
        select(ExtProjectSend.target, ExtProjectSend.external_id).where(
            ExtProjectSend.project_id == project_id
        )
    ).tuples()
    events = session.execute(
        select(ExtMinutesEvent.user_id, ExtMinutesEvent.event_id).where(
            ExtMinutesEvent.project_id == project_id
        )
    ).tuples()
    return _queue(session, project.team_id, list(rows)) + _queue_events(session, list(events))


BEHIND = ("failed", "held", "not_connected")
"""The outcomes of a copy that does not say what the meeting now says."""


def _try(
    session: Session,
    meeting_id: str,
    project: tuple[str, str],
    target: str,
    attempt: Any,
) -> Sent:
    """One copy's work, whatever it raises costing that copy only. Ids and the
    error's class in the log: the text is meeting content.

    A copy the outbound check refused is ``held``, not ``failed``: the sender
    is told why, since sending again meets the same refusal and only a
    rewording ends it. Nothing left; the log line says which copy, by id."""
    try:
        outcome = attempt()
    except PrivacyViolationError:
        log.warning(
            "extraction_project_send_blocked_by_privacy_guard",
            meeting_id=meeting_id,
            project_id=project[0],
            target=target,
        )
        outcome = "held"
    except Exception as exc:  # noqa: BLE001 -- one copy never stops the others
        log.warning(
            "extraction_project_send_failed",
            meeting_id=meeting_id,
            project_id=project[0],
            target=target,
            error=type(exc).__name__,
        )
        outcome = "failed"
    return Sent(project[0], project[1], target, outcome)


def _retract_row(session: Session, row: ExtProjectSend, clients: Clients) -> str:
    with session.begin_nested():
        outcome = retract(row.target, row.external_id, clients)
        if outcome == "not_connected":
            return outcome
        session.delete(row)
        session.flush()
    return "retracted"


def _emptied(
    session: Session, meeting_id: str, kept: set[str], targets: Sequence[str]
) -> list[ExtProjectSend]:
    """The meeting's copies whose project has nothing confirmed any more, in
    the order ``refresh`` takes them (``_in_send_order``)."""
    query = select(ExtProjectSend).where(
        ExtProjectSend.meeting_id == meeting_id, ExtProjectSend.target.in_(targets)
    )
    if kept:
        query = query.where(ExtProjectSend.project_id.not_in(kept))
    return _in_send_order(session.scalars(query), [])


def _in_send_order(
    rows: Iterable[ExtProjectSend], projects: Sequence[Minutes]
) -> list[ExtProjectSend]:
    """``rows`` in the order ``send`` locks them: the projects that have
    minutes in ``minutes``' order, each tool in ``TARGETS``' order, then the
    emptied ones by project id. A row's lock is held to the end of the
    transaction, so a send and a refresh of one meeting that took its rows in
    different orders could each wait on the other (#787 review)."""
    place = {m.project_id: n for n, m in enumerate(projects)}
    return sorted(
        rows,
        key=lambda r: (
            r.project_id not in place,
            place.get(r.project_id, 0),
            r.project_id,
            TARGETS.index(r.target) if r.target in TARGETS else len(TARGETS),
        ),
    )


def send(
    session: Session, meeting_id: str, targets: Sequence[str], clients: Clients
) -> tuple[list[Sent], int]:
    """Every project's minutes to every chosen tool, and the chosen tools'
    copies of a project left with nothing confirmed retracted. Returns what
    happened to each copy, and how many confirmed rows were left out as
    미분류."""
    chosen = [t for t in TARGETS if t in targets]
    out: list[Sent] = []
    projects, unsorted = minutes(session, meeting_id)
    for m in projects:
        for target in chosen:
            out.append(
                _try(
                    session,
                    meeting_id,
                    (m.project_id, m.project_name),
                    target,
                    lambda m=m, target=target: _send_one(session, meeting_id, m, target, clients),
                )
            )
    names = _names(session, meeting_id)
    for row in _emptied(session, meeting_id, {m.project_id for m in projects}, chosen):
        out.append(
            _try(
                session,
                meeting_id,
                (row.project_id, names.get(row.project_id, "")),
                row.target,
                lambda row=row: _retract_row(session, row, clients),
            )
        )
    if "calendar" in chosen and clients.calendar is not None:
        calendar, calendar_id, user_id = clients.calendar
        kept = {m.project_id for m in projects if m.day is not None}
        for event in _events(session, meeting_id, user_id=user_id):
            if event.project_id in kept:
                continue
            out.append(
                _try(
                    session,
                    meeting_id,
                    (event.project_id, names.get(event.project_id, "")),
                    "calendar",
                    lambda event=event: _retract_event(session, event, calendar, calendar_id),
                )
            )
    log.info("extraction_project_minutes_sent", meeting_id=meeting_id, copies=len(out))
    return out, unsorted


def _events(
    session: Session, meeting_id: str, *, user_id: str | None = None
) -> list[ExtMinutesEvent]:
    query = select(ExtMinutesEvent).where(ExtMinutesEvent.meeting_id == meeting_id)
    if user_id is not None:
        query = query.where(ExtMinutesEvent.user_id == user_id)
    return list(session.scalars(query.order_by(ExtMinutesEvent.user_id)))


def _names(session: Session, meeting_id: str) -> dict[str, str]:
    """The names of the meeting's team's projects, for the report."""
    return {
        project_id: name
        for project_id, name in session.execute(
            select(ExtProject.id, ExtProject.name).where(
                ExtProject.team_id == meeting_team(session, meeting_id)
            )
        ).tuples()
    }


def meetings_with_sends(
    session: Session, item_ids: Iterable[str], decision_ids: Iterable[str]
) -> set[str]:
    """The meetings of these items and decisions whose minutes went out."""
    items, decisions = set(item_ids), set(decision_ids)
    meetings: set[str] = set()
    if items:
        meetings |= set(
            session.scalars(select(ExtActionItem.meeting_id).where(ExtActionItem.id.in_(items)))
        )
    if decisions:
        meetings |= set(
            session.scalars(select(ExtDecision.meeting_id).where(ExtDecision.id.in_(decisions)))
        )
    if not meetings:
        return set()
    sent = set(
        session.scalars(
            select(ExtProjectSend.meeting_id)
            .where(ExtProjectSend.meeting_id.in_(meetings))
            .distinct()
        )
    )
    return sent | set(
        session.scalars(
            select(ExtMinutesEvent.meeting_id)
            .where(ExtMinutesEvent.meeting_id.in_(meetings))
            .distinct()
        )
    )


def has_events(session: Session, meeting_id: str) -> bool:
    """Whether anyone put this meeting's minutes on their own calendar."""
    return (
        session.scalar(
            select(ExtMinutesEvent.user_id).where(ExtMinutesEvent.meeting_id == meeting_id).limit(1)
        )
        is not None
    )


def sent_targets(session: Session, meeting_id: str) -> list[str]:
    """The tools this meeting's minutes went to, for ``refresh``."""
    return sorted(
        set(
            session.scalars(
                select(ExtProjectSend.target).where(ExtProjectSend.meeting_id == meeting_id)
            )
        )
    )


CalendarFor = Callable[[str], "tuple[Any, str] | None"]
"""A person's own calendar client and calendar id, or ``None`` (tasks' ``_calendars``)."""


def refresh(
    session: Session,
    meeting_id: str,
    clients: Clients,
    calendar_for: CalendarFor | None = None,
) -> list[Sent]:
    """After the meeting's confirmed rows changed with nobody pressing send --
    speech deleted, a decision taken back, a line masked again: every copy it
    already has rewritten, or retracted when its project has nothing confirmed
    left. Makes no copy that was not there. A tool not connected keeps its
    copy until it is (it cannot be reached either way).

    The minutes events on people's own calendars too, each through its owner's
    grant (``calendar_for``): nobody else can touch another person's calendar,
    so this is the only way an event there loses a deleted sentence.

    A copy that already says the minutes as they are now is left alone and
    reported ``unchanged`` (``content_digest`` on its row, an event's too):
    nothing is asked of its tool, and no grant is asked for. So this can run
    after every change, and again after a failure, without a new Notion page
    each time."""
    existing = list(
        session.scalars(select(ExtProjectSend).where(ExtProjectSend.meeting_id == meeting_id))
    )
    events = _events(session, meeting_id)
    if not existing and not events:
        return []
    projects, _ = minutes(session, meeting_id)
    by_id = {m.project_id: m for m in projects}
    names = _names(session, meeting_id)
    out: list[Sent] = []
    for row in _in_send_order(existing, projects):
        m = by_id.get(row.project_id)
        project = (row.project_id, names.get(row.project_id, ""))
        if m is not None and row.external_id and row.content_digest == _digest(m):
            out.append(Sent(project[0], project[1], row.target, "unchanged"))
        elif m is None:
            out.append(
                _try(
                    session,
                    meeting_id,
                    project,
                    row.target,
                    lambda row=row: _retract_row(session, row, clients),
                )
            )
        else:
            out.append(
                _try(
                    session,
                    meeting_id,
                    project,
                    row.target,
                    lambda m=m, row=row: _send_one(session, meeting_id, m, row.target, clients),
                )
            )
    for event in events:
        project = (event.project_id, names.get(event.project_id, ""))
        out.append(
            _try(
                session,
                meeting_id,
                project,
                "calendar",
                lambda event=event: _refresh_event(
                    session, meeting_id, event, by_id.get(event.project_id), calendar_for
                ),
            )
        )
    log.info("extraction_project_minutes_refreshed", meeting_id=meeting_id, copies=len(out))
    return out


def _refresh_event(
    session: Session,
    meeting_id: str,
    event: ExtMinutesEvent,
    m: Minutes | None,
    calendar_for: CalendarFor | None,
) -> str:
    if (
        m is not None
        and m.day is not None
        and event.event_id
        and event.content_digest == _digest(m)
    ):
        return "unchanged"
    mine = calendar_for(event.user_id) if calendar_for is not None else None
    if mine is None:
        return "not_connected"
    calendar, calendar_id = mine
    if m is None or m.day is None:
        return _retract_event(session, event, calendar, calendar_id)
    return _to_calendar(
        session, meeting_id, m, Clients(calendar=(calendar, calendar_id, event.user_id))
    )


def in_line(sent: Iterable[Sent]) -> bool:
    """Whether a refresh left no copy behind. ``not_connected`` counts as left
    behind: the copy is still out there saying what it said. So does ``held``,
    as it did while it was reported as ``failed``."""
    return all(s.outcome not in BEHIND for s in sent)


def owe_refresh(session: Session, meeting_ids: Iterable[str]) -> list[str]:
    """Record that these meetings' copies still have to be refreshed
    (``ext_project_refresh_owed``). Safe to repeat: a meeting already owed
    keeps its row and its count. Returns the ids, sorted."""
    ids = sorted(set(meeting_ids))
    if ids:
        session.execute(
            service._insert_if_absent_into(session, ExtProjectRefreshOwed)
            .values([{"meeting_id": meeting_id} for meeting_id in ids])
            .on_conflict_do_nothing(index_elements=["meeting_id"])
        )
    return ids


def settle_refresh(session: Session, meeting_id: str) -> None:
    """The meeting's copies are in line: nothing is owed any more."""
    owed = session.get(ExtProjectRefreshOwed, meeting_id)
    if owed is not None:
        session.delete(owed)
        session.flush()
