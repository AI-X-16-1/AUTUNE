"""When another meeting looks needed (spec section 4).

Pure functions over tool results, no model call. Like Workload's ``plan.py``,
the decision is a rule the team can read and test, and the lead approves what
it proposes, so the rule only has to propose sensibly, never decide.

Both thresholds are first guesses, to be checked on W5's real meetings (#22).
"""

from __future__ import annotations

from dataclasses import dataclass

from autune_agent.results import Finding, ToolResult

MIN_HIGH_GAPS = 2
"""Open high-severity gaps that, with an unresolved question, leave a meeting heavy."""

MAX_EVIDENCE = 5
"""Gap ids a proposal carries: the ones the lead's preview shows (#562)."""


@dataclass(frozen=True)
class Verdict:
    carried: tuple[str, ...]
    """M's gap ids on a template item the previous analysed meeting left open too."""
    heavy: tuple[str, ...]
    """M's open high-severity gap ids, when there are enough of them and a question."""

    @property
    def fires(self) -> bool:
        return bool(self.carried or self.heavy)

    @property
    def evidence(self) -> list[str]:
        """Carried-over gaps first, then heavy ones, each once."""
        return list(dict.fromkeys(self.carried + self.heavy))[:MAX_EVIDENCE]

    def reason(self) -> str:
        parts = []
        if self.carried:
            parts.append(f"직전 회의에 이어 다시 열린 항목 {len(self.carried)}개")
        if self.heavy:
            parts.append(f"높음 갭 {len(self.heavy)}건과 미해결 질문")
        return ", ".join(parts)


def _id(item: Finding) -> str | None:
    value = getattr(item, "id", None)
    return value if isinstance(value, str) else None


def decide(open_gaps: ToolResult, recurring: ToolResult, questions: ToolResult) -> Verdict:
    """Either rule of section 4, over C's two reads and B's questions."""
    carried = tuple(i for i in map(_id, recurring.items) if i)
    high = tuple(
        i
        for item in open_gaps.items
        if getattr(item, "severity", None) == "high" and (i := _id(item))
    )
    heavy = high if len(high) >= MIN_HIGH_GAPS and questions.items else ()
    return Verdict(carried=carried, heavy=heavy)


def cited(open_gaps: ToolResult, verdict: Verdict) -> list[Finding]:
    """The gaps a proposal rests on, in its evidence's order, for the chat answer."""
    by_id = {i: item for item in open_gaps.items if (i := _id(item))}
    return [by_id[i] for i in verdict.evidence if i in by_id]
