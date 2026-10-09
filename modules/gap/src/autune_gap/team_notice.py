"""S20's notices on the team's Slack channel (#824).

Three buttons post here, each once per press, and one approval:

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
  event per meeting, so there is one notice. Nobody is DMed: a DM to the
  meeting's members waits for the team's decision after 10/12 (#1046).
- **"질문 카드 Slack 전송"**, at the top of S20, posts the meeting's open
  ``high`` gaps as question cards, one message per gap so each card stays one
  gap (plan 3 on #824, accepted by mkkim68 on 2026-10-06). At most ``SENT``
  cards; when there are more, one last message counts the rest and links to
  the report. Nobody is mentioned. Pressing again posts again: a member who
  presses twice has asked twice, as for "담당자 지정해 질문".

The channel is the team's own (``integrations``, ``config["channel"]``), the
one B and D already notify. A member is mentioned only by the Slack account
they linked themselves (``slack_member_id``); a member who has not linked one
is named in plain text instead. Nothing here says whether anybody linked
anything -- the screen is not told which way it went.

What leaves for Slack is the meeting's title, the gaps' titles and questions
(all stored masked), the presser's and the member's display names, when the
next meeting's event starts, and a link to the meeting's report on Autune.
``SlackClient`` runs the outbound check over all of it. Interpolated values
are escaped so a title cannot become a mention or a link. Logs hold ids and outcomes, never text.

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
from autune_core.settings import get_settings as get_core_settings
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

SENT = 3
"""Question cards one "질문 카드 Slack 전송" posts; the rest are counted, with a
link to the report, so one press cannot fill the channel (plan 3 on #824)."""

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


def _card(
    meeting: Meeting, gap: GapGap, *, lead: str, text: str, footer: str
) -> tuple[str, list[dict[str, Any]]]:
    """One gap's question card. ``lead`` comes before the question in the
    card's body (a mention, or nothing), ``text`` before it in the
    notification's plain text."""
    question = _escape(gap.suggested_question or gap.title)
    blocks = [
        _section(f"*갭 질문* · {_escape(meeting.title)}"),
        _section(f"{lead}{question}"),
        _context(f"● {_escape(gap.title)} · {footer} · Autune 갭 분석"),
    ]
    return f"{text}{question}", blocks


def build_ask(
    meeting: Meeting, gap: GapGap, *, mention: str, asker: str
) -> tuple[str, list[dict[str, Any]]]:
    """The question card: the member, the question, the gap and its meeting."""
    return _card(
        meeting,
        gap,
        lead=f"{mention} ",
        text=f"{mention} 확인 부탁드립니다: ",
        footer=f"{asker}님이 요청",
    )


def build_card(meeting: Meeting, gap: GapGap, *, presser: str) -> tuple[str, list[dict[str, Any]]]:
    """A question card for the team, with nobody mentioned."""
    return _card(meeting, gap, lead="", text="갭 질문: ", footer=f"{presser}님이 공유")


def report_url(meeting_id: str) -> str:
    """S20 for the meeting, on Autune's web app."""
    return f"{get_core_settings().web_base_url.rstrip('/')}/meetings/{meeting_id}/gap"


def build_rest(meeting: Meeting, count: int) -> tuple[str, list[dict[str, Any]]]:
    """The one line after the cards: how many more there are, and where."""
    text = (
        f"'{_escape(meeting.title)}' 회의의 다른 high 갭 {count}건은 "
        "Autune 갭 리포트에서 볼 수 있습니다."
    )
    return text, [_context(f"{text} <{report_url(meeting.id)}|갭 리포트 열기>")]


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
) -> tuple[str, list[dict[str, Any]]]:
    """The one notice that the follow-up meeting is on the calendar: when, and
    the open gaps on its agenda."""
    meeting_title = _escape(meeting.title)
    text = f"{approver}님이 '{meeting_title}' 회의의 후속 회의를 {when(starts)}에 잡았습니다."
    blocks = [
        _section(f"*후속 회의* · {meeting_title}"),
        _section(f"*일시:* {when(starts)}"),
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


def post_cards(
    session: Session, meeting: Meeting, gaps: Sequence[GapGap], *, presser: User
) -> tuple[SlackOutcome, int]:
    """Post up to ``SENT`` of ``gaps`` as question cards, then one line that
    counts the rest. Returns what the channel did and how many cards it took.

    The first card Slack does not take stops the rest -- a refused card says a
    stored text holds something unmasked, and a failed one that Slack is not
    answering -- and its outcome is the answer, beside the cards posted before
    it."""
    if not gaps:
        return "not_tried", 0
    name = _name(presser)
    sent = 0
    for gap in gaps[:SENT]:
        text, blocks = build_card(meeting, gap, presser=name)
        outcome = _post(
            session, meeting.team_id, text, blocks, ids={"meeting_id": meeting.id, "gap_id": gap.id}
        )
        if outcome != "posted":
            log.info("gap_cards_posted", meeting_id=meeting.id, sent=sent, outcome=outcome)
            return outcome, sent
        sent += 1
    outcome = "posted"
    if len(gaps) > SENT:
        text, blocks = build_rest(meeting, len(gaps) - SENT)
        outcome = _post(session, meeting.team_id, text, blocks, ids={"meeting_id": meeting.id})
    log.info("gap_cards_posted", meeting_id=meeting.id, sent=sent, outcome=outcome)
    return outcome, sent


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
) -> SlackOutcome:
    """Say once on the team channel that the follow-up meeting is on the
    calendar, when it starts and what is on its agenda."""
    text, blocks = build_followup(meeting, gaps, approver=_name(approver), starts=starts)
    outcome = _post(
        session,
        meeting.team_id,
        text,
        blocks,
        ids={"meeting_id": meeting.id, "gap_ids": ",".join(gap.id for gap in gaps)},
    )
    log.info("gap_followup_posted", meeting_id=meeting.id, gaps=len(gaps), outcome=outcome)
    return outcome
