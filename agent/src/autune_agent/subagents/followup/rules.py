"""When a follow-up meeting looks needed -- the rule, as plain functions.

``agent/docs/specs/2026-09-30-followup-subagent-design.md`` section 4. No model
call: the lead should be able to read why a proposal was made, and a test
should be able to say when one is not.

Follow-up proposes when either holds for the meeting:

1. **Carried over.** A template item is open here and was open in the team's
   previous analysed meeting (C's ``recurring_open_gaps``).
2. **Left heavy.** ``MIN_HIGH_GAPS`` or more open high-severity gaps here
   (C's ``open_gaps``), and B raised at least one question
   (``unresolved_questions``).

Both thresholds are first guesses, to be checked on the real meetings of W5
(#22).
"""

from __future__ import annotations

from dataclasses import dataclass

from autune_agent.results import Finding

MIN_HIGH_GAPS = 2
"""Open high-severity gaps that, with a raised question, make a meeting heavy."""

MAX_TITLES = 3
"""Gap titles named in the proposed item. The rest are in its evidence."""

PREFIX = "후속 회의: "


@dataclass(frozen=True)
class Gap:
    id: str
    title: str
    score: float
    severity: str


@dataclass(frozen=True)
class Decision:
    """Why a follow-up is proposed, and what it is about. ``gaps`` is most
    risky first and never empty."""

    reason: str
    """``carried_over`` or ``left_heavy``."""
    gaps: tuple[Gap, ...]
    evidence: tuple[str, ...]


def gaps_from(items: list[Finding]) -> list[Gap]:
    """C's findings as gaps. Anything without an id is not a gap C stored."""
    found = []
    for item in items:
        extra = item.model_extra or {}
        gap_id = extra.get("id")
        if not gap_id:
            continue
        found.append(
            Gap(
                id=str(gap_id),
                title=item.title,
                score=item.score,
                severity=str(extra.get("severity", "")),
            )
        )
    return sorted(found, key=lambda g: (-g.score, g.id))


def decide(
    open_gaps: list[Gap],
    carried: list[Gap],
    carried_evidence: list[str],
    questions_raised: int,
) -> Decision | None:
    """The proposal to make, or ``None`` when no follow-up is called for.

    Carried-over wins when both hold. It is the stronger reason, since the
    team has already let the item go once.
    """
    if carried:
        return Decision(
            reason="carried_over",
            gaps=tuple(carried),
            evidence=tuple(dict.fromkeys(carried_evidence or [g.id for g in carried])),
        )
    high = [g for g in open_gaps if g.severity == "high"]
    if len(high) >= MIN_HIGH_GAPS and questions_raised > 0:
        return Decision(reason="left_heavy", gaps=tuple(high), evidence=tuple(g.id for g in high))
    return None


def item_description(decision: Decision) -> str:
    """The board item's text: a fixed prefix and C's gap titles.

    A gap title is a template's item name plus a masked topic label, so nothing
    here is utterance text (``add_action_item`` stores it as given).
    """
    titles = list(dict.fromkeys(g.title for g in decision.gaps))[:MAX_TITLES]
    return PREFIX + ", ".join(titles)


def rationale(decision: Decision) -> str:
    count = len(decision.gaps)
    if decision.reason == "carried_over":
        return f"직전 회의에 이어 이번 회의에서도 열린 채 남은 항목 {count}개."
    return f"심각도 높은 갭 {count}건이 열려 있고, 회의에서 나온 질문도 남아 있습니다."
