"""S20's notices on the team's Slack channel (#824).

Two buttons post here, each once per press, and one approval:

- **"담당자 지정해 질문"** posts the gap's question on the meeting's team
  channel, mentioning the member it is for. It replaces writing the question
  onto that member's calendar, which used their grant for something that is
  not their own work (mkkim68 on #824, 2026-10-06: "팀 채널 카드에서 멘션").
- **"다음 회의 잡기"** posts once that the meeting's open gaps were put on the
  next meeting's event, after the calendar took them, so the people who were
  in the meeting can see what comes next and when. Only the gaps whose line is
  new on that event are listed, and nothing is posted when none is new:
  pressing again does not post again.
- **Follow-up's proposal, approved** posts once that the approver put the
  follow-up meeting on their calendar, when it starts, and which of the
  meeting's open gaps are on its agenda (``followup_meeting``). There is one
  event per meeting, so there is one notice.

The channel is the team's own (``integrations``, ``config["channel"]``), the
one B and D already notify. A member is mentioned only by the Slack account
they linked themselves (``slack_member_id``); a member who has not linked one
is named in plain text instead. Nothing here says whether anybody linked
anything -- the screen is not told which way it went.

What leaves for Slack is the meeting's title, the gaps' titles and questions
(all stored masked), the presser's and the member's display names, and when
the next meeting's event starts. ``SlackClient`` runs the outbound check over
all of it. Interpolated values are escaped so a title cannot become a mention
or a link. Logs hold ids and outcomes, never text.

The event's start is the one value read from Google, and only its date and
time are sent -- never the event's title, which is whatever its organizer
typed and was never masked.

**A refusal by the outbound check is not a Slack failure.** Every other value
here is one Autune stored, and a date and a time hold nothing to refuse, so a
refusal means a stored title, question or name holds something unmasked -- a
finding about C's store, not about the network.
It is told apart (``refused``, an error log with the ids) and nothing is
posted (#872 review). It is not raised: "다음 회의 잡기" posts after the
calendar took the lines, and raising there would roll back the records that
let those lines be taken out again (``gap_agenda_events``), leaving them on
the calendar with no way back.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

from autune_core import Meeting, User, get_logger, load_integration, load_user_integration
from autune_core.errors import PrivacyViolationError
from autune_integrations import IntegrationError, SlackClient

from .models import GapGap

log = get_logger(__name__)

SlackOutcome = Literal["posted", "no_slack", "failed", "refused", "not_tried"]
"""What the team channel did. ``no_slack``: the team has not connected Slack,
or chose no channel. ``failed``: Slack did not take it. ``refused``: the
outbound check found personal data in it and nothing was sent.
``not_tried``: there was nothing to post."""

LISTED = 10
"""Gaps listed in one "다음 회의 잡기" notice; the rest are counted."""

_SLACK_ENTITIES = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}


def _escape(text: str) -> str:
    """Slack's three control characters as entities, so a stored title reads
    as text: ``<!channel>`` in one would otherwise notify the whole channel."""
    return "".join(_SLACK_ENTITIES.get(char, char) for char in text)


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _name(user: User | None) -> str:
    return _escape(user.display_name) if user is not None else "팀원"


def _mention(session: Session, user: User) -> str:
    """``<@U…>`` for a member who linked their Slack account, their name
    otherwise."""
    linked = load_user_integration(session, user.id, "slack")
    member = linked.config.get("slack_user_id") if linked is not None else None
    return f"<@{member}>" if member else _name(user)


def build_ask(
    meeting: Meeting, gap: GapGap, *, mention: str, asker: str
) -> tuple[str, list[dict[str, Any]]]:
    """The question card: the member, the question, the gap and its meeting."""
    question = _escape(gap.suggested_question or gap.title)
    title = _escape(gap.title)
    meeting_title = _escape(meeting.title)
    text = f"{mention} 확인 부탁드립니다: {question}"
    blocks = [
        _section(f"*갭 질문* · {meeting_title}"),
        _section(f"{mention} {question}"),
        _context(f"● {title} · {asker}님이 요청 · Autune 갭 분석"),
    ]
    return text, blocks


def when(starts: datetime | date) -> str:
    """``10월 15일(수) 14:00``, in the event's own time zone as Google gave it;
    the date alone for an all-day event."""
    day = f"{starts.month}월 {starts.day}일({'월화수목금토일'[starts.weekday()]})"
    return f"{day} {starts:%H:%M}" if isinstance(starts, datetime) else day


def _gap_lines(gaps: Sequence[GapGap]) -> str:
    """One line per gap, its title and question; past ``LISTED``, a count."""
    lines = []
    for gap in gaps[:LISTED]:
        question = f" — {_escape(gap.suggested_question)}" if gap.suggested_question else ""
        lines.append(f"● {_escape(gap.title)}{question}")
    if len(gaps) > LISTED:
        lines.append(f"외 {len(gaps) - LISTED}건")
    return "\n".join(lines)


def build_agenda(
    meeting: Meeting,
    gaps: Sequence[GapGap],
    *,
    presser: str,
    starts: datetime | date | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """The one notice that the meeting's open gaps are on the next meeting's
    event, and when that event starts if Google said."""
    meeting_title = _escape(meeting.title)
    next_meeting = f"다음 회의({when(starts)})" if starts is not None else "다음 회의"
    text = (
        f"{presser}님이 '{meeting_title}' 회의의 열린 갭 {len(gaps)}건을 "
        f"{next_meeting} 캘린더 일정에 안건으로 넣었습니다."
    )
    blocks = [_section(f"*다음 회의 안건* · {meeting_title}")]
    if starts is not None:
        blocks.append(_section(f"*다음 회의:* {when(starts)}"))
    blocks += [
        _section(_gap_lines(gaps)),
        _context(f"{presser}님이 다음 회의 일정 설명에 추가 · Autune 갭 분석"),
    ]
    return text, blocks


def build_followup(
    meeting: Meeting,
    gaps: Sequence[GapGap],
    *,
    approver: str,
    starts: datetime,
    invited: int,
) -> tuple[str, list[dict[str, Any]]]:
    """The one notice that the follow-up meeting is on the calendar: when, how
    many were invited, and the open gaps on its agenda."""
    meeting_title = _escape(meeting.title)
    text = f"{approver}님이 '{meeting_title}' 회의의 후속 회의를 {when(starts)}에 잡았습니다."
    blocks = [
        _section(f"*후속 회의* · {meeting_title}"),
        _section(f"*일시:* {when(starts)} · 초대 {invited}명"),
    ]
    if gaps:
        blocks.append(_section("*안건*\n" + _gap_lines(gaps)))
    blocks.append(_context(f"{approver}님이 후속 회의 제안을 승인 · Autune 갭 분석"))
    return text, blocks


def _post(
    session: Session,
    team_id: str,
    text: str,
    blocks: list[dict[str, Any]],
    *,
    ids: dict[str, str],
) -> SlackOutcome:
    """Post on the team's channel. ``ids`` name what the message is about, for
    the log of a refusal: never the text, which is what was refused."""
    config = load_integration(session, team_id, "slack")
    channel = config.config.get("channel") if config is not None else None
    if config is None or not channel or not config.secret:
        return "no_slack"
    client = SlackClient(config.require_secret())
    try:
        client.post_message(str(channel), text, blocks)
    except PrivacyViolationError:
        log.error("gap_slack_refused", team_id=team_id, **ids)
        return "refused"
    except IntegrationError as exc:
        log.warning("gap_slack_failed", team_id=team_id, error=type(exc).__name__)
        return "failed"
    finally:
        client.close()
    return "posted"


def post_ask(
    session: Session, meeting: Meeting, gap: GapGap, *, asker: User, member: User
) -> SlackOutcome:
    """Ask ``member`` the gap's question on the team channel."""
    text, blocks = build_ask(meeting, gap, mention=_mention(session, member), asker=_name(asker))
    outcome = _post(
        session, meeting.team_id, text, blocks, ids={"meeting_id": meeting.id, "gap_id": gap.id}
    )
    log.info("gap_ask_posted", gap_id=gap.id, outcome=outcome)
    return outcome


def post_agenda(
    session: Session,
    meeting: Meeting,
    gaps: Sequence[GapGap],
    *,
    presser: User,
    starts: datetime | date | None = None,
) -> SlackOutcome:
    """Say once on the team channel which gaps went onto the next meeting, and
    when it starts."""
    if not gaps:
        return "not_tried"
    text, blocks = build_agenda(meeting, gaps, presser=_name(presser), starts=starts)
    outcome = _post(
        session,
        meeting.team_id,
        text,
        blocks,
        ids={"meeting_id": meeting.id, "gap_ids": ",".join(gap.id for gap in gaps)},
    )
    log.info("gap_agenda_posted", meeting_id=meeting.id, gaps=len(gaps), outcome=outcome)
    return outcome


def post_followup(
    session: Session,
    meeting: Meeting,
    gaps: Sequence[GapGap],
    *,
    approver: User,
    starts: datetime,
    invited: int,
) -> SlackOutcome:
    """Say once on the team channel that the follow-up meeting is on the
    calendar, when it starts and what is on its agenda."""
    text, blocks = build_followup(
        meeting, gaps, approver=_name(approver), starts=starts, invited=invited
    )
    outcome = _post(
        session,
        meeting.team_id,
        text,
        blocks,
        ids={"meeting_id": meeting.id, "gap_ids": ",".join(gap.id for gap in gaps)},
    )
    log.info("gap_followup_posted", meeting_id=meeting.id, gaps=len(gaps), outcome=outcome)
    return outcome
