"""The Report subagent's template: tool results in, report body out.

No LLM: the same input gives the same text. E adds the header (title, date) and
the footer when it stores the report (``publish_meeting_report``). Rules the
tests pin:

- A section whose tool is missing or failed is removed, not written as "none":
  a tool that did not answer is not a meeting that did not discuss.
- Unconfirmed B items are never quoted; the pending line is B's own count
  sentence (agent-layer.md section 8 rule 3).
- Another meeting is named by its title and date, never quoted, so deleting it
  leaves nothing of it in this meeting's report.
"""

from __future__ import annotations

from autune_agent.results import Finding, ToolResult

BODY_MAX_CHARS = 2500
"""E stores header + body + footer under 3,000; the header can take ~420."""

MORE = "더 있어요 — 상세보기에서"


def _usable(result: ToolResult | None) -> ToolResult | None:
    return result if result is not None and result.ok else None


def has_pending(review: ToolResult | None) -> bool:
    """B has items waiting for a person -- the post then gets a review button."""
    usable = _usable(review)
    return bool(usable and usable.items)


def _actions(actions: ToolResult | None) -> list[str]:
    usable = _usable(actions)
    if usable is None:
        return []
    if not usable.items:
        return ["✅ 확정된 액션 아이템 없음"]
    lines = ["✅ 확정된 액션 아이템"]
    lines += [f"• {i.title} — {i.body}" if i.body else f"• {i.title}" for i in usable.items]
    if usable.truncated:
        lines.append(MORE)
    return lines


def _pending(review: ToolResult | None) -> list[str]:
    return [f"⏳ {review.summary}"] if review is not None and has_pending(review) else []


def _gaps(gaps: ToolResult | None) -> list[str]:
    usable = _usable(gaps)
    if usable is None:
        return []
    lines = [f"💬 {usable.summary}"] if usable.summary else []
    lines += [f"⚠️ 놓친 논의: {item.title} — {item.body}" for item in usable.items]
    return lines


def _link_line(item: Finding) -> str | None:
    extra = item.model_extra or {}
    title = extra.get("meeting_title")
    if not title:
        return None  # never fall back to item.title: it may be another meeting's sentence
    date = extra.get("date")
    return f"🔗 이어지는 회의: {title} ({date})" if date else f"🔗 이어지는 회의: {title}"


def _links(links: ToolResult | None) -> list[str]:
    usable = _usable(links)
    if usable is None:
        return []
    return [line for item in usable.items if (line := _link_line(item)) is not None]


def _join(sections: list[list[str]]) -> str:
    return "\n\n".join("\n".join(lines) for lines in sections if lines)


def render(
    actions: ToolResult | None,
    review: ToolResult | None,
    gaps: ToolResult | None,
    links: ToolResult | None,
) -> str:
    """The report body from B's items and review state, C's gaps, D's links."""
    pending = _pending(review)
    head = _actions(actions) + pending
    tail = [_gaps(gaps), _links(links)]
    body = _join([head, *tail])
    # Over budget: drop action bullets from the end, then say there is more.
    bullets = [line for line in head if line.startswith("• ")]
    while len(body) > BODY_MAX_CHARS and bullets:
        head.remove(bullets.pop())
        if MORE not in head:
            head.insert(len(head) - len(pending), MORE)
        body = _join([head, *tail])
    return body[:BODY_MAX_CHARS]
