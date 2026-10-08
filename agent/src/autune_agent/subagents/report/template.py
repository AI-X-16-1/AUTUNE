"""The Report subagent's template path: find the meeting, read four tools, render, propose.

Fixed tool order, no LLM. Reads through the Toolbox only and never writes: the
outcome carries two proposals naming E's actions -- store the draft (E lists it
in ``L1_ACTIONS``, so it runs at the end of the run) and post it (L2, after a
person approves). The main agent runs them (agent-layer.md section 8 rule 2).

Where the meeting comes from:

- **The request names no ``mtg_...`` id** -- a run woken by
  ``autune.intelligence.completed`` (#509), or "write the report" asked on a
  meeting's screen: the run's scope carries the meeting, so neither the tool
  calls nor the proposals name it; the Toolbox and the executor fill it in. A
  run about no meeting gets the Toolbox's own refusal from B's read.
- **The request names exactly one id:** it is passed on, and the scope still
  holds it to the run's team.
- **Several different ids:** refused rather than guessed.

**Woken by ``autune.intelligence.meeting_report_changed``** (#674): a person
edited the draft on E's dashboard. Nothing is read from B, C or D and nothing
is rendered; the run proposes the post of the draft E holds now (L2), with that
draft's id. Plan mode supersedes the earlier post proposal for the meeting, so
an approver sees one proposal, for the text that is there.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from autune_agent.main import BudgetExceededError, SubagentState, Toolbox
from autune_agent.results import ProposedAction, SubagentResult, ToolResult
from autune_contracts import INTELLIGENCE_COMPLETED, INTELLIGENCE_MEETING_REPORT_CHANGED
from autune_core import new_id

from .render import has_pending, render

log = logging.getLogger(__name__)

ACTIONS_TOOL = "extraction.meeting_action_items"
REVIEW_TOOL = "extraction.review_state"
GAPS_TOOL = "gap.open_gaps"
"""C's read since #546. A tool that is not registered is skipped, so a wrong name
here loses the gap section silently -- a test pins it to C's registry."""
LINKS_TOOL = "context.links_for_meeting"
READS = (ACTIONS_TOOL, REVIEW_TOOL, GAPS_TOOL, LINKS_TOOL)
"""What a report is composed from, in order."""
AWAITING_TOOL = "intelligence.meeting_report_awaiting_approval"
"""E's read of the draft a person's edit left, for the post proposal (#674)."""
CHANNEL_TOOL = "intelligence.report_channel"
"""E's read of whether the team has a Slack channel to post to. Without one a
post is refused at approval, so it is not proposed (#1000 review)."""
TOOLS = (*READS, AWAITING_TOOL, CHANNEL_TOOL)
OPTIONAL = (GAPS_TOOL, LINKS_TOOL)
"""Context around B's confirmed items. A failure here drops a section, never the report."""

DRAFT_ACTION = "intelligence.draft_meeting_report"
"""E stores the report. L1: E lists it in ``L1_ACTIONS``."""
PUBLISH_ACTION = "intelligence.publish_meeting_report"
"""E posts the stored report to the team channel. L2: a channel post moves people."""
CORRECTION_ACTION = "intelligence.publish_meeting_report_correction"
"""E posts a member's correction under the posted report. L2, like the post (#674)."""

DRAFT_ID_PREFIX = "rdr"
"""Both proposals carry one id per run. E stores it with the draft and posts only
the draft the approved proposal names: a later run's draft replaces this one,
and approving this run's post then posts nothing (review of #508)."""

TRIGGER = INTELLIGENCE_COMPLETED
"""B, C and D have all reported (or timed out) only by this event."""
CHANGED_TRIGGER = INTELLIGENCE_MEETING_REPORT_CHANGED
"""A person edited the draft; propose its post again (#674)."""
TRIGGERS = (TRIGGER, CHANGED_TRIGGER)

# ASCII boundaries, not \b: in a str pattern \b is Unicode-aware, so a Korean
# particle right after the id ("mtg_ab12cd의") would count as part of the word.
MEETING_ID = re.compile(r"(?<![A-Za-z0-9_])mtg_[A-Za-z0-9]+(?![A-Za-z0-9_])")


def _read(toolbox: Toolbox, name: str, meeting: dict[str, Any]) -> ToolResult | None:
    if name not in toolbox.describe():
        return None  # not shipped yet: no call, no budget spent
    if name not in OPTIONAL:
        return toolbox.call(name, **meeting)
    try:
        return toolbox.call(name, **meeting)
    except BudgetExceededError:
        raise  # the run's stop, not this tool's failure
    except Exception as exc:  # a bug in C's or D's tool costs its section only
        log.warning("report_optional_tool_failed tool=%s error=%s", name, type(exc).__name__)
        return None


NO_SLACK = (
    "Slack이 연결되어 있지 않아 게시는 요청하지 않았습니다. 설정에서 Slack과 채널을 연결해 주세요."
)
"""Said instead of a post proposal that would only fail at approval."""


def no_channel(result: ToolResult | None) -> bool:
    """True only when E's ``report_channel`` says there is no channel. A missing
    tool or a failed read proposes the post as before: E refuses it at approval."""
    if result is None or not result.ok or not result.items:
        return False
    return getattr(result.items[0], "connected", True) is False


def _channel(toolbox: Toolbox) -> ToolResult | None:
    if CHANNEL_TOOL not in toolbox.describe():
        return None
    try:
        return toolbox.call(CHANNEL_TOOL)
    except BudgetExceededError:
        raise
    except Exception as exc:  # a broken check costs nothing: the post is proposed as before
        log.warning("report_channel_check_failed error=%s", type(exc).__name__)
        return None


def failed(reason: str) -> SubagentState:
    return {"outcome": SubagentResult(result=ToolResult.failure(reason))}


def repropose(toolbox: Toolbox) -> SubagentState:
    """The post of what a person's change left, for approval -- no model, no render.

    Before the report is posted that is an edited draft (``PUBLISH_ACTION`` with
    its ``draft_id``); after, a correction (``CORRECTION_ACTION`` with its
    ``correction_id``). Never both: E accepts a correction only once the report
    is posted, so one proposal per run, superseding the last for the meeting.
    """
    awaiting = toolbox.call(AWAITING_TOOL)
    if not awaiting.ok:
        return {"outcome": SubagentResult(result=awaiting)}
    if not awaiting.items:
        return failed("nothing awaits approval")
    item = awaiting.items[0]
    kind = getattr(item, "kind", None)
    if kind == "draft" and isinstance(draft_id := getattr(item, "draft_id", None), str):
        post = ProposedAction(
            kind="meeting_report_post",
            title="회의 리포트 게시 (팀원이 고친 초안)",
            tool=PUBLISH_ACTION,
            arguments={"draft_id": draft_id},
            level="L2",
            rationale="A team member edited the report; post the edit once a person approves.",
        )
        said = "고친 회의 리포트를 승인 대기로 올렸습니다."
    elif kind == "correction" and isinstance(
        correction_id := getattr(item, "correction_id", None), str
    ):
        post = ProposedAction(
            kind="meeting_report_correction_post",
            title="회의 리포트 수정본 게시",
            tool=CORRECTION_ACTION,
            arguments={"correction_id": correction_id},
            level="L2",
            rationale="A team member corrected the posted report; post it once approved.",
        )
        said = "회의 리포트 수정본을 승인 대기로 올렸습니다."
    else:
        return failed("what awaits approval has no id")
    if no_channel(_channel(toolbox)):
        return {"outcome": SubagentResult(result=ToolResult(ok=True, summary=NO_SLACK, items=[]))}
    summary = ToolResult(ok=True, summary=said, items=[])
    return {"outcome": SubagentResult(result=summary, proposed=[post])}


POSTED = (
    "이 회의의 리포트는 이미 게시됐습니다. 고칠 내용은 대시보드의 회의 리포트 카드에서 "
    "수정본으로 올려 주세요. 승인되면 원래 게시물 아래에 올라갑니다."
)
"""What a run says once the report is out: never a second report, but the way to fix one."""


def already_posted(toolbox: Toolbox, meeting: dict[str, Any]) -> bool:
    """The report went out, so a run proposes nothing (#658 review).

    A late ``intelligence.completed`` after the post would otherwise queue a
    draft and its post: E refuses both, but plan mode would already have
    superseded a correction waiting for approval with that dead post proposal.
    A correction waiting means posted too. Skipped when E's read is not there.
    """
    if AWAITING_TOOL not in toolbox.describe():
        return False
    awaiting = toolbox.call(AWAITING_TOOL, **meeting)
    if not awaiting.ok:
        return awaiting.reason == "already posted"
    return any(getattr(item, "kind", None) == "correction" for item in awaiting.items)


def compose_report(toolbox: Toolbox, meeting: dict[str, Any]) -> SubagentResult:
    """Read B, C, D, render, and propose the draft (L1) and its post (L2) --
    the draft alone when the team has no Slack channel to post to."""
    results = {name: _read(toolbox, name, meeting) for name in READS}
    actions = results[ACTIONS_TOOL]
    if actions is not None and not actions.ok:
        # B (or the scope check) cannot find the meeting: nothing to report.
        return SubagentResult(result=actions)
    body = render(actions, results[REVIEW_TOOL], results[GAPS_TOOL], results[LINKS_TOOL])
    if not body:
        return SubagentResult(result=ToolResult.failure("nothing to report for this meeting"))

    # No team_id anywhere: the run's scope fills it (E's RUN_SCOPE).
    draft_id = new_id(DRAFT_ID_PREFIX)
    draft = ProposedAction(
        kind="meeting_report_draft",
        title="회의 리포트 초안 저장",
        tool=DRAFT_ACTION,
        arguments={
            **meeting,
            "body_markdown": body,
            "pending_review": has_pending(results[REVIEW_TOOL]),
            "draft_id": draft_id,
        },
        level="L1",
        rationale="The meeting's analysis finished; store its structured minutes.",
    )
    if no_channel(_channel(toolbox)):
        said = f"회의 리포트 초안을 만들었습니다. {NO_SLACK}"
        return SubagentResult(result=ToolResult(ok=True, summary=said, items=[]), proposed=[draft])
    post = ProposedAction(
        kind="meeting_report_post",
        title="회의 리포트 게시",
        tool=PUBLISH_ACTION,
        arguments={**meeting, "draft_id": draft_id},
        level="L2",
        rationale="Post the stored minutes to the team channel once a person approves.",
    )
    summary = ToolResult(ok=True, summary="회의 리포트 초안을 만들었습니다.", items=[])
    return SubagentResult(result=summary, proposed=[draft, post])
