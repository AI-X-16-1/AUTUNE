"""The Briefing subagent's template: tool results in, one finding per section out.

No LLM: the same input gives the same text. A section is one ``Finding`` -- the
main agent shows its ``title`` and ``body`` and nothing else. Rules the tests pin:

- A tool that did not answer drops its section. A tool that answered "none"
  keeps it and says so in the tool's own words: "no Jira issue" is something the
  team may want to hear, "the tool failed" is not a meeting with nothing to
  settle.
- Another meeting is named by its title and date and never quoted beyond what
  D and C return, so deleting it leaves nothing of it here.
- A LOW gap is left out, as C's own screen (S20) and the Report keep it behind a
  toggle. C's summary still counts it.
- B's unreviewed question labels are never read: this is a brief a person acts
  on, and "raised" is not "open" (agent-layer.md section 8 rule 3).
"""

from __future__ import annotations

from autune_agent.results import Finding, ToolResult

MORE = "더 있어요 — 상세보기에서"

PREVIOUS = "previous_meeting"
DECISION = "decision"
"""``kind`` values D's ``brief_recap`` puts on its items."""


def _usable(result: ToolResult | None) -> ToolResult | None:
    return result if result is not None and result.ok else None


def _kind(item: Finding) -> str:
    return str((item.model_extra or {}).get("kind", ""))


def previous_meeting_id(recap: ToolResult | None) -> str | None:
    """The earlier meeting D chose, which B's and C's reads then take."""
    usable = _usable(recap)
    if usable is None:
        return None
    for item in usable.items:
        if _kind(item) == PREVIOUS:
            meeting_id = (item.model_extra or {}).get("meeting_id")
            return meeting_id if isinstance(meeting_id, str) else None
    return None


def previous_meeting_title(recap: ToolResult | None) -> str | None:
    usable = _usable(recap)
    if usable is None:
        return None
    return next((item.title for item in usable.items if _kind(item) == PREVIOUS), None)


def _section(title: str, lines: list[str], *, more: bool = False) -> Finding:
    if more:
        lines = [*lines, MORE]
    return Finding(title=title, body="\n".join(lines))


def recap_section(recap: ToolResult | None, *, not_composed: bool) -> Finding | None:
    """The meeting this one follows, and what it decided."""
    if not_composed:
        return _section(
            "지난 회의",
            ["이어받는 회의는 시작 10분 전에 정해집니다. 아직 알 수 없어요."],
        )
    usable = _usable(recap)
    if usable is None:
        return None
    if not usable.items:
        return _section("지난 회의", [usable.summary])
    lines: list[str] = []
    for item in usable.items:
        if _kind(item) == PREVIOUS:
            lines.append(f"{item.title} · {item.body}" if item.body else item.title)
        else:
            lines.append(f"• {item.title}")
    return _section("지난 회의에서 이어받는 결정", lines, more=usable.truncated)


def agenda_section(agenda: ToolResult | None) -> Finding | None:
    """The team's open Jira issues, as B last reported them."""
    usable = _usable(agenda)
    if usable is None:
        return None
    if not usable.items:
        return _section("이번 회의에서 다룰 Jira 이슈", [usable.summary])
    lines = [f"• {i.title} — {i.body}" if i.body else f"• {i.title}" for i in usable.items]
    return _section("이번 회의에서 다룰 Jira 이슈", lines, more=usable.truncated)


def actions_section(actions: ToolResult | None) -> Finding | None:
    """Confirmed work that is late, due soon or needs a new owner. Work state,
    which a team already sees on its board -- never speech."""
    usable = _usable(actions)
    if usable is None:
        return None
    lines = [usable.summary] if usable.summary else []
    lines += [f"• {i.title} — {i.body}" if i.body else f"• {i.title}" for i in usable.items]
    return _section("기한이 지났거나 다가온 액션 아이템", lines, more=usable.truncated)


def gaps_section(gaps: ToolResult | None) -> Finding | None:
    """C's undismissed gaps in the earlier meeting, each with its question."""
    usable = _usable(gaps)
    if usable is None:
        return None
    lines = [usable.summary] if usable.summary else []
    for item in usable.items:
        if (item.model_extra or {}).get("severity") == "low":
            continue
        lines.append(f"• {item.title}")
        if item.body:
            lines.append(f"  ↳ {item.body}")
    return _section("지난 회의에서 닫히지 않은 갭", lines, more=usable.truncated)
