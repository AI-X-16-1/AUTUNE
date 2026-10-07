"""The "오늘 업무 보고" draft: a person's own day, sent to that person (the user, 2026-10-07).

Late on a working afternoon in Korea a person who finished or started something
today is sent one Slack DM per team: a short report of their own items on that
team, written so that they can paste it to the team themselves -- or not. What
they finished, what they moved to 진행 중, what goes on to tomorrow, what is
late.

**To the person and nobody else.** The same rule as the morning DM
(``service.daily_digest_content``; privacy.md section 6): no channel, no
manager, nobody else's items, and nothing counted about a person. Whether the
team ever sees it is the person's own paste. Sending a collected version to a
team lead was not asked for and is not built.

**Plain text from the rows.** No model reads or rewrites it.

**What "today" can say.** ``ext_edit_events`` keeps that an item was edited,
which fields, and when -- never a value and never who. So:

- *끝낸 일* is an item of theirs that is done now and whose status was edited
  today;
- *진행한 일* is one that is in progress now and whose status was edited today.
  That is all "progress" can mean here: an item worked on all day without a
  change on the board is not seen;
- *내일로 넘어가는 일* is what stays open and is in hand: still in progress
  from before, or due today and not finished;
- *늦은 일* is what is open and was due before today;
- the rest of their open items is a count.

Each item is in one part only. Whoever made an edit is not known and not said:
an item of theirs somebody else marked done reads as finished, as it does in
the morning DM.

**When.** Monday to Friday, 16:00 to 17:00 Korea time, never on a public
holiday, once per person, team and day (``ext_work_reports``). The user,
2026-10-07: the DMs made from now on go between 09:00 and 17:00, so this takes
the last hour of that -- the day it reports is as full as it can be, and not
over: what is finished after it went is not in it, and 내일로 넘어가는 일 is
what was in hand at that hour. A report that did not go inside its hour is not
sent later. Only when
there is something of today to report -- an item finished or moved; a day with
no change on the board sends nothing. Not to somebody who turned their
reminders off ("마감 알림 받기") or whose own leave dates cover the day, and
only about items whose assignee is on the meeting's team now. A deployment
sends none until ``AUTUNE_EXTRACTION_WORK_REPORT`` is on.

**Nothing is kept of which day a person worked** (mkkim68, review of #954).
The report goes only on a day something of the person's was finished or
moved, so that it went says so much about them: kept, the rows of
``ext_work_reports`` would be a calendar of each person's working days, which
ADR 0003 forbids as it forbids any per-person record of conduct. The row is
needed for one thing -- not sending twice in a day -- so it lives for its day:
``forget_past_days`` deletes every earlier day's row, and the task calls it
first on every run, whatever the setting and the hour. For the same reason the
task returns a count and no ids, logs a failed send by team and error type,
and raises a refusal by team: no result, log line or error names a person
beside a day.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_core import Meeting, Team, TeamMember
from autune_core.settings import get_settings as get_core_settings
from autune_integrations import SlackApi

from . import days_off, reminders, service
from .models import ExtActionItem, ExtDueReminderOptOut, ExtEditEvent, ExtWorkReport
from .slots import KST

REPORT_WEEKDAYS = (0, 1, 2, 3, 4)
"""Monday to Friday, in Korea. The morning DM leaves Monday to the weekly
digest; the day's report has nothing to leave it to."""

REPORT_FROM = time(16, 0)
REPORT_UNTIL = time(17, 0)
"""Korea time. The last hour of 09:00-17:00, outside which no DM made from
2026-10-07 on is sent (the user). A worker that came back at six does not
send it then, and it is not sent the next morning either."""

MAX_LINES = 5
"""Items listed under each heading; the rest are counted."""


def report_day(now: datetime) -> date | None:
    """The day a work report sent at ``now`` is for -- or ``None`` when
    ``now`` is not a Monday-to-Friday afternoon in Korea, and nothing goes."""
    local = now.astimezone(KST)
    if not REPORT_FROM <= local.time() < REPORT_UNTIL:
        return None
    return local.date() if local.weekday() in REPORT_WEEKDAYS else None


def day_start(day: date) -> datetime:
    """When "today" starts for a report on ``day``: midnight in Korea. In UTC,
    so that the comparison reads the same on every database."""
    return datetime.combine(day, time.min, tzinfo=KST).astimezone(UTC)


@dataclass(frozen=True)
class WorkReportOwed:
    """One work report owed: whose, through which team's Slack, for which
    day. As ``service.DailyDigestOwed``: the recipient is the person whose
    items they are, and there is no field a caller could put another person
    in."""

    user_id: str
    team_id: str
    day: date


@dataclass(frozen=True)
class WorkReport:
    """What one person's work report says, already chosen and ordered."""

    day: date
    team_name: str | None = None
    done: Sequence[reminders.DigestLine] = ()
    moved: Sequence[reminders.DigestLine] = ()
    carried: Sequence[reminders.DigestLine] = ()
    late: Sequence[reminders.DigestLine] = ()
    others: int = 0

    @property
    def empty(self) -> bool:
        """Nothing was finished and nothing moved today: there is no day to
        report, whatever is still open."""
        return not (self.done or self.moved)


def _lines(lines: Sequence[reminders.DigestLine], *, day: date) -> list[str]:
    out: list[str] = []
    for line in lines[:MAX_LINES]:
        when = ""
        if line.due_date is not None and line.due_date < day:
            when = f" (기한 {line.due_date.isoformat()} 지남)"
        elif line.due_date == day:
            when = " (오늘 기한)"
        where = f" · {reminders.slack_escape(line.meeting_title)}" if line.meeting_title else ""
        out.append(f"• {reminders.slack_escape(line.description)}{when}{where}")
    if len(lines) > MAX_LINES:
        out.append(f"• 외 {len(lines) - MAX_LINES}개")
    return out


def build_text(report: WorkReport, *, board_url: str) -> str:
    """The work report as plain text. The first line is to the person; the
    rest is the draft, headed by the team it is about, with only the parts
    that have something in them."""
    team = f"{reminders.slack_escape(report.team_name)} " if report.team_name else ""
    out = [
        "오늘 업무 보고 초안입니다. 고쳐서 팀에 붙여 넣으셔도 됩니다.",
        f"{team}업무 보고 ({report.day.isoformat()})",
    ]
    for heading, lines in (
        ("끝낸 일", report.done),
        ("진행한 일", report.moved),
        ("내일로 넘어가는 일", report.carried),
        ("늦은 일", report.late),
    ):
        if lines:
            out.append(heading)
            out += _lines(lines, day=report.day)
    if report.others:
        out.append(f"그 밖의 열린 액션 아이템 {report.others}개")
    out.append(board_url)
    return "\n".join(out)


def _status_edited(session: Session, *, since: datetime, team_id: str | None = None) -> set[str]:
    """Ids of the items whose status was edited after ``since`` -- in one
    team's meetings when given. That an edit named the status, not what it
    became: the row's status now says where it ended up."""
    query = select(ExtEditEvent.action_item_id, ExtEditEvent.fields).where(
        ExtEditEvent.kind == "edited",
        ExtEditEvent.created_at > since,
        ExtEditEvent.action_item_id.is_not(None),
    )
    if team_id is not None:
        query = query.join(Meeting, Meeting.id == ExtEditEvent.meeting_id).where(
            Meeting.team_id == team_id
        )
    return {
        item_id
        for item_id, fields in session.execute(query).tuples()
        if item_id is not None and "status" in (fields or "").split(",")
    }


def _finished_or_started(
    session: Session,
    item_ids: Collection[str],
    *,
    user_id: str | None,
    team_id: str | None,
    now: datetime,
) -> list[tuple[ExtActionItem, str, str | None]]:
    """Of ``item_ids``, the items that are done or in progress now, with
    their team and meeting title -- assigned to an account that is on the
    meeting's team, as ``service._open_items_of`` requires of an open one
    (somebody who left a team is not sent that team's items). A meeting past
    its retention window is left out."""
    if not item_ids:
        return []
    query = (
        select(ExtActionItem, Meeting.team_id, Meeting.title)
        .join(Meeting, Meeting.id == ExtActionItem.meeting_id)
        .join(
            TeamMember,
            and_(
                TeamMember.team_id == Meeting.team_id,
                TeamMember.user_id == ExtActionItem.assignee_id,
            ),
        )
        .where(
            ExtActionItem.id.in_(item_ids),
            ExtActionItem.status.in_([ActionStatus.DONE.value, ActionStatus.IN_PROGRESS.value]),
            or_(Meeting.expires_at.is_(None), Meeting.expires_at > now),
        )
        .order_by(ExtActionItem.due_date, ExtActionItem.id)
    )
    if user_id is not None:
        query = query.where(ExtActionItem.assignee_id == user_id)
    if team_id is not None:
        query = query.where(Meeting.team_id == team_id)
    return [(item, team, title) for item, team, title in session.execute(query).tuples()]


def forget_past_days(session: Session, *, today: date) -> int:
    """Delete every row of a day before ``today`` (Korea's) and say how many.

    A row is the "once" of its own day and nothing after it: yesterday's cannot
    stop or allow anything today, and what it would go on saying is that its
    person finished or started work that day (the module docstring). A text
    the outbound check refused is settled with the same row and goes the same
    way. Called first by the task on every run -- every ten minutes, with the
    feature off and outside its hour too -- so a row is gone within about ten
    minutes of the next midnight in Korea for as long as the worker runs, and
    at its first run if it was down."""
    gone = session.execute(delete(ExtWorkReport).where(ExtWorkReport.day < today))
    return int(getattr(gone, "rowcount", 0) or 0)


def reports_to_send(session: Session, *, now: datetime) -> list[WorkReportOwed]:
    """The work reports owed at ``now`` and not yet sent: one per person
    and team for whom an item of theirs on that team was finished or moved to
    in progress today, on a Monday-to-Friday afternoon in Korea
    (``report_day``) that is not a public holiday.

    Less the people the morning DM leaves out: anyone who turned their
    reminders off, and anyone whose own pause covers today."""
    day = report_day(now)
    if day is None or days_off.is_public_holiday(session, day, now=now):
        return []
    off = set(session.scalars(select(ExtDueReminderOptOut.user_id))) | service._paused_users(
        session, day
    )
    edited = _status_edited(session, since=day_start(day))
    owners = {
        (item.assignee_id, team)
        for item, team, _ in _finished_or_started(
            session, edited, user_id=None, team_id=None, now=now
        )
        if item.assignee_id and item.assignee_id not in off
    }
    sent = set(
        session.execute(
            select(ExtWorkReport.user_id, ExtWorkReport.team_id).where(ExtWorkReport.day == day)
        ).tuples()
    )
    return [
        WorkReportOwed(user_id=user, team_id=team, day=day)
        for user, team in sorted(owners)
        if (user, team) not in sent
    ]


def report_content(session: Session, owed: WorkReportOwed, *, now: datetime) -> WorkReport:
    """What this person's work report says, read now: their own items on
    this team, each in the first part it fits (the module docstring says what
    each part can and cannot mean)."""
    edited = _status_edited(session, since=day_start(owed.day), team_id=owed.team_id)
    done: list[reminders.DigestLine] = []
    moved: list[reminders.DigestLine] = []
    said: set[str] = set()
    for item, _team, title in _finished_or_started(
        session, edited, user_id=owed.user_id, team_id=owed.team_id, now=now
    ):
        line = reminders.DigestLine(item.description, item.due_date, title)
        (done if item.status == ActionStatus.DONE.value else moved).append(line)
        said.add(item.id)

    carried: list[reminders.DigestLine] = []
    late: list[reminders.DigestLine] = []
    others = 0
    for item, _team, title in service._open_items_of(
        session, user_id=owed.user_id, team_id=owed.team_id, now=now
    ):
        if item.id in said:
            continue
        line = reminders.DigestLine(item.description, item.due_date, title)
        if item.due_date is not None and item.due_date < owed.day:
            late.append(line)
        elif item.status == ActionStatus.IN_PROGRESS.value or item.due_date == owed.day:
            carried.append(line)
        else:
            others += 1
    team = session.get(Team, owed.team_id)
    return WorkReport(
        day=owed.day,
        team_name=team.name if team is not None else None,
        done=done,
        moved=moved,
        carried=carried,
        late=late,
        others=others,
    )


def _content_to_send(session: Session, owed: WorkReportOwed, *, now: datetime) -> WorkReport | None:
    """What this person's work report would say -- or ``None`` when none is
    to go: they turned their reminders off, paused the day, or nothing of
    theirs was finished or moved today. Read as things are now, by the send
    and by ``would_go``."""
    if not service.due_reminders_on(session, owed.user_id) or service.notifications_paused(
        session, owed.user_id, owed.day
    ):
        return None
    content = report_content(session, owed, now=now)
    return None if content.empty else content


def would_go(session: Session, owed: WorkReportOwed, *, now: datetime) -> bool:
    """Whether this report would be sent if it were tried now
    (``service.daily_digest_would_go``'s reason): asked before the person's
    calendar is read, in a transaction of its own."""
    return _content_to_send(session, owed, now=now) is not None


def send_report(session: Session, slack: SlackApi, owed: WorkReportOwed, *, now: datetime) -> bool:
    """Claim the day's work report and send it, in that order -- or send
    nothing. ``service.send_daily_digest``'s shape: the person's choice, their
    pause and their items are read again here; the claim is inserted only if
    absent, in the caller's transaction with the send, so two runs cannot both
    send and a failed send takes the claim back. It goes to ``owed.user_id``
    and nobody else."""
    content = _content_to_send(session, owed, now=now)
    if content is None:
        return False
    claimed = session.execute(
        service._insert_if_absent_into(session, ExtWorkReport)
        .values(user_id=owed.user_id, team_id=owed.team_id, day=owed.day, sent_at=now)
        .on_conflict_do_nothing(index_elements=["user_id", "team_id", "day"])
        .returning(ExtWorkReport.user_id)
    ).first()
    if claimed is None:
        return False
    slack.send_dm(
        owed.user_id,
        build_text(content, board_url=f"{get_core_settings().web_base_url.rstrip('/')}/actions"),
    )
    return True


def settle_refused(session: Session, owed: WorkReportOwed, *, now: datetime) -> None:
    """Keep the day's claim of a report the outbound check refused, so it is
    reported once and not tried again every ten minutes until five
    (``service.settle_refused_daily_digest``'s reason). The refused send
    rolled its own claim back with it; this writes it again, in a transaction
    of its own. Only a refusal is settled -- a send Slack did not take, or a
    person with no linked account, stays owed."""
    session.execute(
        service._insert_if_absent_into(session, ExtWorkReport)
        .values(user_id=owed.user_id, team_id=owed.team_id, day=owed.day, sent_at=now)
        .on_conflict_do_nothing(index_elements=["user_id", "team_id", "day"])
    )
