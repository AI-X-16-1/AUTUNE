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

Sending again updates the same copy (``ext_project_sends``): the Slack message
and the Jira task are rewritten; a Notion page's body cannot be replaced in one
call, so the old page goes to the trash and a new one takes its place.

**What leaves** is the same text the item and decision syncs already send to
the same tools -- descriptions, statements, the assignee's name and the due
date -- plus the team's and the project's names. Every request goes through
``HttpClient`` and its outbound check. One tool failing never stops the others,
and the person is told which copy went and which did not.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_core import Meeting, Team, get_logger
from autune_core.errors import PrivacyViolationError
from autune_integrations import IntegrationError

from . import service
from .models import ExtDecision, ExtProject, ExtProjectSend
from .notion_setup import MINUTES_NOTION_PROPERTIES
from .slots import meeting_day

log = get_logger(__name__)

TARGETS = ("notion", "slack", "jira")
NOTION_TEXT_LIMIT = 2000
"""Notion's limit on one text object; a longer line is cut, never sent whole."""


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


@dataclass(frozen=True)
class Sent:
    project_id: str
    project_name: str
    target: str
    outcome: str
    """``created``, ``updated``, ``not_connected`` or ``failed``."""


@dataclass
class Clients:
    """The team's tools, each with where to write, or ``None`` when not connected."""

    notion: tuple[Any, str] | None = None
    """A ``NotionClient`` and the 회의록 database id."""
    slack: tuple[Any, str] | None = None
    """A ``SlackClient`` and the alert channel id."""
    jira: tuple[Any, str | None] | None = None
    """A ``JiraClient`` and the team's default project key."""


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
    return [{"type": "text", "text": {"content": content[:NOTION_TEXT_LIMIT]}}]


def _notion_page(m: Minutes, meeting_id: str, database_id: str) -> dict[str, Any]:
    names = MINUTES_NOTION_PROPERTIES
    properties: dict[str, Any] = {
        names["title"]: {"title": _text(m.title)},
        names["meeting"]: {"rich_text": _text(meeting_id)},
    }
    if m.day is not None:
        properties[names["date"]] = {"date": {"start": m.day.isoformat()}}
    children: list[dict[str, Any]] = []
    for heading, lines in (("결정", m.decisions), ("할 일", m.items)):
        if not lines:
            continue
        children.append({"type": "heading_3", "heading_3": {"rich_text": _text(heading)}})
        children += [
            {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _text(line)}}
            for line in lines
        ]
    return {"parent": {"database_id": database_id}, "properties": properties, "children": children}


def _send_one(session: Session, meeting_id: str, m: Minutes, target: str, clients: Clients) -> str:
    """One copy out; returns the outcome. Raises only what the caller catches."""
    row = session.get(ExtProjectSend, (meeting_id, m.project_id, target))
    if target == "notion":
        if clients.notion is None:
            return "not_connected"
        notion, database_id = clients.notion
        if row is not None:
            notion.trash_page(row.external_id)
        made = notion.request("POST", "/pages", json=_notion_page(m, meeting_id, database_id))
        external = str(made.get("id", ""))
    elif target == "slack":
        if clients.slack is None:
            return "not_connected"
        slack, channel = clients.slack
        if row is not None and ":" in row.external_id:
            at, ts = row.external_id.split(":", 1)
            slack.update_message(at, ts, m.text)
            external = row.external_id
        else:
            external = f"{channel}:{slack.post_message(channel, m.text)}"
    else:
        if clients.jira is None:
            return "not_connected"
        jira, default_key = clients.jira
        key = m.jira_project_key or default_key
        if not key:
            return "not_connected"
        kept = row is not None and jira.update_task(
            row.external_id,
            m.title,
            due_date=None,
            assignee_account_id=None,
            keep_assignee=True,
            description=m.text,
        )
        external = row.external_id if kept and row is not None else ""
        if not kept:
            external = jira.create_task(key, m.title, description=m.text)
    if row is None:
        session.add(
            ExtProjectSend(
                meeting_id=meeting_id,
                project_id=m.project_id,
                target=target,
                external_id=external,
            )
        )
        session.flush()
        return "created"
    row.external_id = external
    session.flush()
    return "updated"


def send(
    session: Session, meeting_id: str, targets: Sequence[str], clients: Clients
) -> tuple[list[Sent], int]:
    """Every project's minutes to every chosen tool. Returns what happened to
    each copy, and how many confirmed rows were left out as 미분류."""
    out: list[Sent] = []
    projects, unsorted = minutes(session, meeting_id)
    for m in projects:
        for target in [t for t in TARGETS if t in targets]:
            try:
                outcome = _send_one(session, meeting_id, m, target, clients)
            except (IntegrationError, PrivacyViolationError) as exc:
                # Ids and the error's class only: the text is meeting content.
                log.warning(
                    "extraction_project_send_failed",
                    meeting_id=meeting_id,
                    project_id=m.project_id,
                    target=target,
                    error=type(exc).__name__,
                )
                outcome = "failed"
            out.append(Sent(m.project_id, m.project_name, target, outcome))
    log.info("extraction_project_minutes_sent", meeting_id=meeting_id, copies=len(out))
    return out, unsorted
