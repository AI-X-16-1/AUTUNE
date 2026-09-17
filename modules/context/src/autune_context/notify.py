"""Slack notices: what they say and how they are shaped.

Pure functions over values -- no Slack client, no database. The service layer
sends what this builds. Keeping it separate is what lets the wording be
tested without credentials, the same split module E uses for
``feedback.build_speaking_ratio_dm``.

docs/modules/context.md "Slack surface" defines two notices:

- **topic-link notice** -- an asserted topic link, posted to the team channel.
- **decision-drift warning** -- a decision changed while a key stakeholder was
  absent, posted to the team channel and by DM to each absent stakeholder.

Message shape follows docs/design/ui-spec.md section 2: status is "●" plus
text rather than an emoji, hierarchy comes from weight.

**The team-channel drift notice does not name the absent stakeholder(s).**
``key_stakeholders_absent`` is a list of global ``user_id`` strings, and there
is no id-to-display-name resolution anywhere in this codebase yet -- naming
someone in a channel message would mean either printing a raw ``usr_...`` id
(no better than the read-API exposure flagged in issue #188, worse for being
pushed instead of pulled) or quietly inventing a resolution path outside a
design conversation. The channel notice states only the count. The personal
DM needs no such resolution: the recipient already knows who they are.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from autune_contracts import ChangeType

_CHANGE_VERB: dict[ChangeType, str] = {
    ChangeType.REVERSED: "번복",
    ChangeType.MODIFIED: "변경",
}


def _change_verb(change_type: ChangeType) -> str:
    return _CHANGE_VERB.get(change_type, "변경")


def _korean_date(value: date) -> str:
    # Not strftime("%-m/%-d ..."): the leading-zero-suppressing variant is a
    # POSIX strftime extension and is not portable (not on Windows, where part
    # of this team develops).
    return f"{value.year}년 {value.month}월 {value.day}일"


def build_topic_link_notice(
    *, topic_label: str, linked_meeting_date: date
) -> tuple[str, list[dict]]:
    """The channel notice for one topic linked to a past meeting it discussed.

    Screen text from docs/modules/context.md: "이 안건은 2026년 9월 4일 회의에서
    논의된 적 있습니다." No deep link yet -- no ``apps/web`` route exists for
    any module's screens (see #204), so there is nothing to link to.
    """
    when = _korean_date(linked_meeting_date)
    fallback = f"'{topic_label}' — {when} 회의에서 논의된 적 있습니다."
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "● *이전에 논의된 안건*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{topic_label}*"}},
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"{when} 회의에서 논의된 적 있습니다."}],
        },
    ]
    return fallback, blocks


def build_decision_drift_channel_notice(
    *, thread_label: str, current_statement: str, change_type: ChangeType, absent_count: int
) -> tuple[str, list[dict]]:
    """The team-channel drift warning. Names nobody -- see module docstring."""
    verb = _change_verb(change_type)
    fallback = f"결정이 {verb}되었습니다: {thread_label}"
    absence_note = f"핵심 이해관계자 {absent_count}명이 자리에 없는 상태에서 {verb}되었습니다."
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"● *결정 {verb}*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{thread_label}*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": current_statement}},
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": absence_note}],
        },
    ]
    return fallback, blocks


def build_decision_drift_personal_dm(
    *, thread_label: str, current_statement: str, change_type: ChangeType
) -> tuple[str, list[dict]]:
    """The DM to one absent stakeholder. No id or name in the text -- the
    recipient is the subject, so nothing needs to identify them."""
    verb = _change_verb(change_type)
    fallback = "자리를 비운 사이 결정이 바뀌었습니다."
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"● *부재 중 결정 {verb}*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{thread_label}*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": current_statement}},
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "이 결정이 바뀔 때 회의에 참석하지 않으셨습니다."}
            ],
        },
    ]
    return fallback, blocks
