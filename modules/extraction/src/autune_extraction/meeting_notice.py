"""The DM right after a meeting: that work has landed on a person (the user, 2026-10-07).

Until this, a person learned that a meeting had left them something only by
opening the app, or the next morning -- and the morning DM names confirmed
items only, so a draft waiting for their confirmation was said nowhere.

**What it says is a count and a link** (decided with the user the same day).
Right after a meeting almost everything this module has made is a draft a
model wrote, waiting for a person (``needs_confirmation``), and unconfirmed
content from B does not reach an outbound surface (#246; agent-layer.md, rule
3). So the message carries the meeting's title, **how many** drafts wait for
this person, and the address of that meeting's 액션 tab -- never a draft's
text, its date, or anything else of it. An item of theirs that a person has
already confirmed is named with its date, as the morning DM names one.

**Who gets one.** A person with an item of the meeting assigned to them that
the model made since the last working day began (``tellable_since``), who is
on the meeting's team now. An
item's assignee is its speaker once that speaker is identified, which may be
an hour after the meeting: this is a sweep, so they are told then. Once per
person and meeting (``ext_meeting_notices``); later items of the same meeting
are the board's and the morning DM's to show.

**Stopped by what stops the others**: "마감 알림 받기" off, or a day inside the
person's own leave dates.

**From 09:00 to 17:00 in Korea, on a working day** (the user, 2026-10-07:
"시간은 09-17시 까지 전송. 넘으면 다음날 09시 전송", and of "다음날": "다음
근무일"). What becomes tellable after 17:00, on a weekend or on a public
holiday is told at 09:00 on the next working day -- Monday to Friday less the
holidays the morning DM also keeps (``days_off``). These are this message's
own hours: the reminders' are 09:00 to 20:00 on any day and are not changed
by it.

It is a count of *items waiting*, said to the person they wait for. Nothing is
counted about a person, nothing of the meeting's speech is read, and Autune
keeps only that the message went.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_core import Meeting, TeamMember
from autune_integrations import SlackApi

from . import days_off, reminders, service
from .models import ExtActionItem, ExtDueReminderOptOut, ExtMeetingNotice
from .service import _insert_if_absent_into
from .slots import KST

SEND_FROM = time(9, 0)
SEND_UNTIL = time(17, 0)
"""Korea time: a notice goes from nine in the morning until five in the
afternoon (the user, 2026-10-07)."""

LOOK_BACK_DAYS = 14
"""How far back the previous working day is looked for. Longer than any run of
holidays and weekends in the calendar; a bound for the loop, not a rule."""

_OPEN = (ActionStatus.TODO.value, ActionStatus.IN_PROGRESS.value)
_WAITING = ActionStatus.NEEDS_CONFIRMATION.value


@dataclass(frozen=True)
class NoticeOwed:
    """One person to tell about one meeting: ids only."""

    meeting_id: str
    team_id: str
    user_id: str


@dataclass(frozen=True)
class MeetingNotice:
    """What one person's notice says, already chosen.

    ``waiting`` is a number and nothing else, on purpose: the drafts it counts
    are not confirmed, and no field here could carry one out."""

    meeting_title: str
    waiting: int
    confirmed: Sequence[reminders.DigestLine] = ()

    @property
    def empty(self) -> bool:
        return self.waiting == 0 and not self.confirmed


def _items_of(meeting_id: str, user_id: str, *, since: datetime):
    """The person's items of this meeting that the notice is about: made by
    the pipeline or the chat, not by a person's own hand, since ``since``."""
    return select(ExtActionItem).where(
        ExtActionItem.meeting_id == meeting_id,
        ExtActionItem.assignee_id == user_id,
        ExtActionItem.origin != "user",
        ExtActionItem.status.in_((_WAITING, *_OPEN)),
        ExtActionItem.created_at >= since,
    )


def _working_day(session: Session, day: date, *, now: datetime) -> bool:
    """Monday to Friday and not a public holiday, by the calendar the morning
    DM keeps (``days_off.is_public_holiday``)."""
    return day.weekday() < 5 and not days_off.is_public_holiday(session, day, now=now)


def sending_time(session: Session, now: datetime) -> bool:
    """Whether a notice may be sent at ``now``: 09:00 to 17:00 in Korea, on a
    working day."""
    local = now.astimezone(KST)
    return SEND_FROM <= local.time() < SEND_UNTIL and _working_day(session, local.date(), now=now)


def tellable_since(session: Session, now: datetime) -> datetime:
    """From when an item must have been made to be told of at ``now``: 09:00
    on the working day before today.

    So an item made after 17:00, or on a weekend or a holiday, is still there
    at 09:00 on the next working day, however long the break was. And one made
    during a working day that could not be told that day -- its holder had not
    linked Slack yet, or was identified as its speaker in the evening -- is
    told on the next one. Older than that the news is old; the morning DM and
    the board carry what is open.
    """
    day = now.astimezone(KST).date()
    for _ in range(LOOK_BACK_DAYS):
        day -= timedelta(days=1)
        if _working_day(session, day, now=now):
            break
    # In UTC, as the rows' own times are kept: a comparison must not depend on
    # how a database reads an offset.
    return datetime.combine(day, SEND_FROM, tzinfo=KST).astimezone(UTC)


def notices_to_send(session: Session, *, now: datetime) -> list[NoticeOwed]:
    """Every person and meeting a notice is owed for at ``now``, and not yet
    sent. Nothing outside the sending hours, and nobody who turned their
    reminders off or whose own leave dates cover today."""
    if not sending_time(session, now):
        return []
    since = tellable_since(session, now)
    told = (
        select(ExtMeetingNotice.meeting_id)
        .where(
            ExtMeetingNotice.meeting_id == ExtActionItem.meeting_id,
            ExtMeetingNotice.user_id == ExtActionItem.assignee_id,
        )
        .exists()
    )
    rows = session.execute(
        select(ExtActionItem.meeting_id, Meeting.team_id, ExtActionItem.assignee_id)
        .join(Meeting, Meeting.id == ExtActionItem.meeting_id)
        # On the meeting's team now: nobody who has left is told of its work.
        .join(
            TeamMember,
            and_(
                TeamMember.team_id == Meeting.team_id,
                TeamMember.user_id == ExtActionItem.assignee_id,
            ),
        )
        .where(
            ExtActionItem.origin != "user",
            ExtActionItem.status.in_((_WAITING, *_OPEN)),
            ExtActionItem.created_at >= since,
            or_(Meeting.expires_at.is_(None), Meeting.expires_at > now),
            ~told,
        )
        .distinct()
        .order_by(ExtActionItem.meeting_id, ExtActionItem.assignee_id)
    ).tuples()
    off = set(session.scalars(select(ExtDueReminderOptOut.user_id)))
    off |= service._paused_users(session, reminders.korean_day(now))
    return [
        NoticeOwed(meeting_id=meeting_id, team_id=team_id, user_id=user_id)
        for meeting_id, team_id, user_id in rows
        if user_id is not None and user_id not in off
    ]


def notice_content(session: Session, owed: NoticeOwed, *, now: datetime) -> MeetingNotice | None:
    """What this person's notice would say, read now -- or ``None`` when none
    is to go: they turned their reminders off, paused the day, left the team,
    or nothing of theirs is left to tell of.

    **An unconfirmed item contributes one to a count and nothing else.** Its
    row is loaded like the others and only its status is looked at: its
    description and date are never copied into the notice, so nothing below
    this function can send them (mminjae97, review of #953: the row is read
    whole, not its status alone)."""
    if not service.due_reminders_on(session, owed.user_id) or service.notifications_paused(
        session, owed.user_id, reminders.korean_day(now)
    ):
        return None
    meeting = session.get(Meeting, owed.meeting_id)
    if meeting is None or not service.is_team_member(session, meeting.team_id, owed.user_id):
        return None
    waiting = 0
    confirmed: list[reminders.DigestLine] = []
    since = tellable_since(session, now)
    for item in session.scalars(
        _items_of(owed.meeting_id, owed.user_id, since=since).order_by(
            ExtActionItem.due_date, ExtActionItem.id
        )
    ):
        if item.status == _WAITING:
            waiting += 1
        else:
            confirmed.append(reminders.DigestLine(item.description, item.due_date, None))
    notice = MeetingNotice(meeting_title=meeting.title, waiting=waiting, confirmed=confirmed)
    return None if notice.empty else notice


def build_meeting_notice(notice: MeetingNotice, *, actions_url: str, today: date) -> str:
    """The notice as plain text: the meeting, how many drafts wait, the items
    already confirmed, and where to look. ``today`` is the day it is read on:
    a due date says its year only when it is another one."""
    title = reminders.slack_escape(notice.meeting_title)
    if notice.waiting:
        out = [f"'{title}'에서 내 담당으로 잡힌 일 {notice.waiting}건이 확인을 기다립니다."]
    else:
        out = [f"'{title}'에서 내 담당으로 정해진 일이 있습니다."]
    for line in notice.confirmed[: reminders.DAILY_MAX_LINES]:
        when = (
            f" (기한 {reminders.written_day(line.due_date, year=today.year)})"
            if line.due_date is not None
            else ""
        )
        out.append(f"• 확정: {reminders.slack_escape(line.description)}{when}")
    if len(notice.confirmed) > reminders.DAILY_MAX_LINES:
        out.append(f"• 확정 외 {len(notice.confirmed) - reminders.DAILY_MAX_LINES}개")
    out.append(actions_url)
    return "\n".join(out)


def notice_would_go(session: Session, owed: NoticeOwed, *, now: datetime) -> bool:
    """Whether this notice would be sent if it were tried now: asked before
    the person's calendar is read, in a transaction of its own."""
    return notice_content(session, owed, now=now) is not None


def send_meeting_notice(
    session: Session, slack: SlackApi, owed: NoticeOwed, *, now: datetime
) -> bool:
    """Claim the notice and send it, in that order -- or send nothing.

    ``service.send_daily_digest``'s shape: everything is read again here, the
    claim is inserted only if absent and in the caller's transaction with the
    send, so two runs cannot both send and a failed send takes the claim back.
    It goes to ``owed.user_id`` and nobody else.
    """
    content = notice_content(session, owed, now=now)
    if content is None:
        return False
    claimed = session.execute(
        _insert_if_absent_into(session, ExtMeetingNotice)
        .values(meeting_id=owed.meeting_id, user_id=owed.user_id, sent_at=now)
        .on_conflict_do_nothing(index_elements=["meeting_id", "user_id"])
        .returning(ExtMeetingNotice.meeting_id)
    ).first()
    if claimed is None:
        return False
    slack.send_dm(
        owed.user_id,
        build_meeting_notice(
            content,
            actions_url=service.answer_url(owed.meeting_id),
            today=reminders.korean_day(now),
        ),
    )
    return True


def settle_refused_notice(session: Session, owed: NoticeOwed, *, now: datetime) -> None:
    """Keep the claim of a notice the outbound check refused, so it is
    reported once and not tried again every five minutes
    (``service.settle_refused_daily_digest``'s reason). The refused send
    rolled its own claim back; this writes it again, in a transaction of its
    own. Only a refusal is settled -- a send Slack did not take, or a person
    with no linked account, stays owed until the window closes."""
    session.execute(
        _insert_if_absent_into(session, ExtMeetingNotice)
        .values(meeting_id=owed.meeting_id, user_id=owed.user_id, sent_at=now)
        .on_conflict_do_nothing(index_elements=["meeting_id", "user_id"])
    )
