"""A team's projects, and which project each decision and item of a meeting is about.

Asked for by the user (2026-10-04): one meeting often covers several projects,
and what it settled should be summarised and sent out project by project. A
team lists its projects (``ext_projects``) with the other names people say for
them; ``assign`` reads what was said and points each decision and item at one.

**Rules, not a model.** Nothing leaves our infrastructure, so it runs on real
meetings whatever #392 decides. A person corrects what the rules get wrong on
the 요약 tab, and a project a person chose is never changed by them again
(``project_by_person``). In order:

1. A team with one project: everything is that project.
2. The row's own lines name exactly one project: that one.
3. Otherwise the topic in force: the latest line before the row's first line,
   within ``LOOKBACK`` lines, that names exactly one project. Meetings move
   from one project to the next ("이제 오튠 얘기할게요"), and what follows
   belongs to it until another is named. A line naming two projects at once
   settles nothing, and the search stops there rather than reaching past it.
4. Otherwise none -- "미분류" on the tab, for a person to choose.

Only consented speakers' lines are read, the same lines the classifier reads
(privacy.md section 5).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autune_core import Meeting, get_logger
from autune_core.errors import ConflictError, NotFoundError, ValidationError

from . import project_send, service
from .models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtDecision,
    ExtDecisionSource,
    ExtProject,
)

log = get_logger(__name__)

LOOKBACK = 40
"""How many lines back the topic in force may have been named. Far enough for
a stretch of discussion on one project; not so far that a project named at the
start of an hour-long meeting claims everything after it."""

MAX_PROJECTS = 30
MAX_ALIASES = 10
MAX_ALIAS_CHARS = 50


@dataclass(frozen=True)
class Project:
    id: str
    names: tuple[str, ...]
    """The name and the aliases, as matched."""


def _pattern(name: str) -> re.Pattern[str]:
    """A name as it may be said: case ignored, and a Latin name only as a whole
    word ("app" must not match "happy"). Korean is matched as written, since a
    particle follows it directly ("오튠은", "오튠에서")."""
    escaped = re.escape(name)
    if re.fullmatch(r"[A-Za-z0-9 ._-]+", name):
        return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(escaped, re.IGNORECASE)


class Matcher:
    """Which projects a line names."""

    def __init__(self, projects: Sequence[Project]) -> None:
        self._patterns = [
            (project.id, [_pattern(n) for n in project.names if n.strip()]) for project in projects
        ]

    def named(self, text: str) -> set[str]:
        return {pid for pid, patterns in self._patterns if any(p.search(text) for p in patterns)}


def choose(
    projects: Sequence[Project],
    lines: Sequence[tuple[str, str]],
    row_lines: Iterable[str],
) -> str | None:
    """The project one row is about, by the rules in the module docstring.

    ``lines`` is the meeting as ``(utterance id, text)`` in spoken order;
    ``row_lines`` the ids of the lines the row was drawn from."""
    if not projects:
        return None
    if len(projects) == 1:
        return projects[0].id
    matcher = Matcher(projects)
    own = set(row_lines)
    text = {uid: t for uid, t in lines}
    in_own = set().union(*(matcher.named(text[uid]) for uid in own if uid in text))
    if len(in_own) == 1:
        return next(iter(in_own))
    positions = [i for i, (uid, _) in enumerate(lines) if uid in own]
    if not positions:
        return None
    first = min(positions)
    for i in range(first - 1, max(-1, first - 1 - LOOKBACK), -1):
        named = matcher.named(lines[i][1])
        if len(named) == 1:
            return next(iter(named))
        if len(named) > 1:
            return None
    return None


def team_projects(session: Session, team_id: str) -> list[ExtProject]:
    return list(
        session.scalars(
            select(ExtProject)
            .where(ExtProject.team_id == team_id)
            .order_by(ExtProject.created_at, ExtProject.id)
        )
    )


def _as_projects(rows: Sequence[ExtProject]) -> list[Project]:
    return [
        Project(row.id, (row.name, *[a for a in row.aliases.split("\n") if a.strip()]))
        for row in rows
    ]


def assign_meeting(session: Session, meeting_id: str) -> int:
    """Point every decision and item of the meeting that no person has placed
    at the project the rules choose. Returns how many changed. Safe to repeat."""
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        return 0
    projects = _as_projects(team_projects(session, meeting.team_id))
    consented = service.consented_utterance_ids(session, meeting_id)
    lines = [
        (u.id, u.text)
        for u in service.stored_transcript(session, meeting_id)
        if u.id in consented and u.text
    ]
    changed = 0
    for item in session.scalars(
        select(ExtActionItem).where(
            ExtActionItem.meeting_id == meeting_id, ExtActionItem.project_by_person.is_(False)
        )
    ):
        sources = session.scalars(
            select(ExtActionItemSource.utterance_id).where(
                ExtActionItemSource.action_item_id == item.id
            )
        )
        chosen = choose(projects, lines, [s for s in sources if s])
        if chosen != item.project_id:
            item.project_id = chosen
            changed += 1
    for decision in session.scalars(
        select(ExtDecision).where(
            ExtDecision.meeting_id == meeting_id, ExtDecision.project_by_person.is_(False)
        )
    ):
        sources = session.scalars(
            select(ExtDecisionSource.utterance_id).where(
                ExtDecisionSource.decision_id == decision.id
            )
        )
        chosen = choose(projects, lines, [s for s in sources if s])
        if chosen != decision.project_id:
            decision.project_id = chosen
            changed += 1
    session.flush()
    log.info("extraction_projects_assigned", meeting_id=meeting_id, changed=changed)
    return changed


def _clean_aliases(aliases: Sequence[str], name: str) -> str:
    kept: list[str] = []
    for alias in aliases:
        text = " ".join(alias.split())
        if not text or text == name or text in kept:
            continue
        if len(text) > MAX_ALIAS_CHARS:
            raise ValidationError(
                f"an alias is at most {MAX_ALIAS_CHARS} characters", field="aliases"
            )
        kept.append(text)
    if len(kept) > MAX_ALIASES:
        raise ValidationError(f"at most {MAX_ALIASES} aliases", field="aliases")
    return "\n".join(kept)


def save_project(
    session: Session,
    team_id: str,
    *,
    name: str,
    aliases: Sequence[str],
    jira_project_key: str | None,
    project_id: str | None = None,
) -> ExtProject:
    """Create a project, or rename and re-alias one of this team's."""
    clean = " ".join(name.split())
    if not clean:
        raise ValidationError("a project needs a name", field="name")
    key = (jira_project_key or "").strip().upper() or None
    if key is not None and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", key):
        raise ValidationError("not a Jira project key", field="jira_project_key")
    taken = session.scalar(
        select(ExtProject.id).where(
            ExtProject.team_id == team_id,
            ExtProject.name == clean,
            ExtProject.id != (project_id or ""),
        )
    )
    if taken is not None:
        raise ConflictError("the team already has a project of that name")
    if project_id is None:
        if len(team_projects(session, team_id)) >= MAX_PROJECTS:
            raise ValidationError(f"a team has at most {MAX_PROJECTS} projects", field="name")
        row = ExtProject(team_id=team_id)
    else:
        found = session.get(ExtProject, project_id)
        if found is None or found.team_id != team_id:
            raise NotFoundError("project", project_id)
        row = found
    row.name = clean
    row.aliases = _clean_aliases(aliases, clean)
    row.jira_project_key = key
    session.add(row)
    # Two members adding the same name at once: the unique constraint answers
    # the second, as a conflict rather than a 500.
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        raise ConflictError("the team already has a project of that name") from exc
    return row


def delete_project(session: Session, team_id: str, project_id: str) -> None:
    """Delete one of this team's projects; its rows become unassigned, and the
    copies of its minutes are queued to be taken out of the team's tools."""
    row = session.get(ExtProject, project_id)
    if row is None or row.team_id != team_id:
        raise NotFoundError("project", project_id)
    # Its minutes' copies go with it: queued before the rows cascade away.
    project_send.queue_project(session, project_id)
    for item in session.scalars(
        select(ExtActionItem).where(ExtActionItem.project_id == project_id)
    ):
        item.project_id = None
        item.project_by_person = False
    for decision in session.scalars(
        select(ExtDecision).where(ExtDecision.project_id == project_id)
    ):
        decision.project_id = None
        decision.project_by_person = False
    session.delete(row)
    session.flush()


def place(session: Session, row: ExtActionItem | ExtDecision, project_id: str | None) -> None:
    """A person puts a decision or an item in a project of its meeting's team --
    or in none. Either way the rules leave it there from now on, and so does
    extracting the meeting again."""
    if project_id is not None:
        project = session.get(ExtProject, project_id)
        meeting = session.get(Meeting, row.meeting_id)
        if project is None or meeting is None or project.team_id != meeting.team_id:
            raise ValidationError("not a project of this meeting's team", field="project_id")
    row.project_id = project_id
    row.project_by_person = True
    # A decision is upserted by id when the meeting is extracted again and
    # keeps the flag; a model item is deleted and rebuilt unless a correction
    # is on record, so the move is recorded as one.
    if isinstance(row, ExtActionItem):
        service.record_placement(session, row)
    session.flush()
