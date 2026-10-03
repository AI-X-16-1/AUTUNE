"""Due-date reminders: the assignee is told, once, the day before and after a miss.

Asked for by the user (2026-10-02). An action item with a due date sat on the
board and nothing said when it was about to lapse; the only people who learned
it had were the ones who opened the board.

**Only the assignee, and only by direct message.** A reminder is about one
person's work and goes to that person's own Slack account, through the bot of
the team that held the meeting. Nobody else is told -- not the team's channel,
not a manager, not whoever made the item -- and nothing here counts or ranks
what a person has missed. An item with no account behind its assignee (a typed
name) has nobody to tell and is skipped.

**Two moments, each once -- and rarely twice.** Once is the claim in
``ext_due_reminders``, written with the send in one transaction. A send Slack
accepted followed by a commit that failed, or a delivery that timed out on
our side, takes the claim back with it, and the next run sends again. That
is the price of never keeping a claim for a message that did not go -- with
one exception: a send the outbound check refused keeps its claim, written
afterwards in its own transaction (``service.settle_refused_due_reminder``),
so the refusal is reported once and not every ten minutes.

**The two moments:**

- ``due_soon``: the day before the due date.
- ``overdue``: the day after it, or up to ``OVERDUE_DAYS`` after -- a window, so
  a worker that was down on the first day still says it, and an item that has
  been late for a month when this first runs is not announced as news.

Nothing on the due date itself: the calendar event the item already has is
that day's reminder. Once is kept by ``ext_due_reminders`` (item, kind, due
date); moving the date makes it a new date, so a new reminder.

**In Korea's daytime.** Days are Korea's days -- the same clock the due date
was read against (``slots.KST``) -- and nothing is sent before ``SEND_FROM`` or
from ``SEND_UNTIL``: a message at midnight that something is due tomorrow is
worse than none.

**What leaves:** the item's description, its due date, its meeting's title and
a link to that meeting's board. No utterance, no other person's name unless
the description itself carries one. Sent through ``SlackClient``, so the
outbound check reads it like every other message.
"""

from __future__ import annotations

from datetime import date, datetime, time

from .slots import KST

DUE_SOON = "due_soon"
OVERDUE = "overdue"
KINDS = (DUE_SOON, OVERDUE)

OVERDUE_DAYS = 3
"""How many days after the due date an ``overdue`` reminder may still go."""

SEND_FROM = time(9, 0)
SEND_UNTIL = time(20, 0)
"""Korea time. Reminders go out from nine in the morning until eight at night."""

_OPENING = {
    DUE_SOON: "내일까지인 액션 아이템이 있습니다.",
    OVERDUE: "기한이 지난 액션 아이템이 있습니다.",
}


def korean_day(now: datetime) -> date:
    """The date in Korea at ``now``."""
    return now.astimezone(KST).date()


def sending_hours(now: datetime) -> bool:
    """Whether ``now`` is a time of day a reminder may be sent, in Korea."""
    return SEND_FROM <= now.astimezone(KST).time() < SEND_UNTIL


def kind_for(due: date, today: date) -> str | None:
    """Which reminder an item due on ``due`` is owed on ``today``, if any."""
    days_left = (due - today).days
    if days_left == 1:
        return DUE_SOON
    if -OVERDUE_DAYS <= days_left <= -1:
        return OVERDUE
    return None


def slack_escape(text: str) -> str:
    """Slack's three control characters as entities, so text reads as text.

    A description is what a person typed or a model wrote from speech, and a
    meeting's title is a person's: ``<!channel>`` or ``<https://x|여기>`` in
    either must not go out under the bot's name as a mention or a disguised
    link (review of #751; the same three ``autune_intelligence`` escapes for
    a report body, #642)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_due_reminder(
    kind: str, *, description: str, due_date: date, meeting_title: str | None, board_url: str
) -> str:
    """The message, as plain text: what it is, when it was due, where to act."""
    where = f" · 회의: {slack_escape(meeting_title)}" if meeting_title else ""
    return "\n".join(
        [
            _OPENING[kind],
            f"• {slack_escape(description)}",
            f"기한: {due_date.isoformat()}{where}",
            board_url,
        ]
    )
