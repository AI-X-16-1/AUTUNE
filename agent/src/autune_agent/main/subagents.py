"""How a subagent plugs into the main agent (agent-layer.md sections 3.1 and 3.2).

Each ``autune_agent.subagents.<name>`` package exports ``SUBAGENT``. The main
agent collects them by iterating ``SUBAGENT_NAMES``; a package that does not
define ``SUBAGENT`` yet is skipped, so an owner can merge their folder before
their subagent works.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Literal, Protocol, TypedDict

from autune_agent.results import SubagentResult
from autune_contracts import INTELLIGENCE_COMPLETED, TRANSCRIPT_READY

from .registry import Toolbox, is_personal_only

SUBAGENT_NAMES = ("research", "briefing", "followup", "workload", "report")

TRIGGER_EVENTS = (TRANSCRIPT_READY, INTELLIGENCE_COMPLETED)
"""The pipeline events the main agent listens to on everyone's behalf -- section
6's two event rows. ``autune_agent.tasks`` has one task per entry, and a test
holds the two lists together; a subagent may name only these.

A subagent that reads B's, C's or D's results wakes on
``INTELLIGENCE_COMPLETED``. ``TRANSCRIPT_READY`` reaches those modules at the
same moment it reaches this layer, so their results do not exist yet."""
"""Owners in agent-layer.md section 3.1 and CODEOWNERS."""


class SubagentState(TypedDict, total=False):
    """The subgraph's state. The main agent writes ``request``; the subagent
    must leave ``outcome``. Anything else it keeps is its own business."""

    request: str
    outcome: SubagentResult


class CompiledSubagent(Protocol):
    """What ``StateGraph(SubagentState).compile()`` returns, as far as we use it."""

    def invoke(self, state: SubagentState, /) -> Any: ...


@dataclass(frozen=True)
class Subagent:
    name: str
    description: str
    """What the main agent routes on. Say when to use it first, like a tool docstring."""
    tools: tuple[str, ...]
    """Registry names this subagent may call, and the only ones it will see."""
    build: Callable[[Toolbox], CompiledSubagent]
    """Given its toolbox, return the compiled subgraph."""
    triggers: tuple[str, ...] = ()
    """Pipeline events that wake this subagent (agent-layer.md section 6), from
    ``TRIGGER_EVENTS``. The main agent subscribes once for everyone
    (``autune_agent.tasks``); a subagent never registers a task of its own. A
    woken run's ``request`` is the event name, and its scope carries the
    meeting the event was about."""
    proposals_per: Literal["meeting", "team"] = "meeting"
    """What one of its L2 proposals is about, so what a newer run replaces
    (``pending.queue_l2``). ``"meeting"``: a run supersedes the pending
    proposals an earlier run made about the same meeting, and a run about no
    meeting supersedes nothing. ``"team"``: the proposals judge the whole team
    -- Workload weighs everyone's open items -- so a run supersedes the team's
    earlier pending proposals from this subagent whatever meeting, if any,
    woke it (#631). Otherwise each meeting's run leaves another proposal
    moving the same item to someone else, and approving both lets the later
    approval win."""

    def __post_init__(self) -> None:
        # The registry never holds a declared personal-only tool; this refuses
        # the name as well, so the mistake surfaces where it was written.
        personal = [tool for tool in self.tools if is_personal_only(tool)]
        if personal:
            raise ValueError(f"subagent {self.name} may not read personal-only tools: {personal}")
        unknown = [t for t in self.triggers if t not in TRIGGER_EVENTS]
        if unknown:
            raise ValueError(f"subagent {self.name} names events nothing listens to: {unknown}")


def collect_subagents(names: Iterable[str] = SUBAGENT_NAMES) -> dict[str, Subagent]:
    found: dict[str, Subagent] = {}
    for name in names:
        package = import_module(f"autune_agent.subagents.{name}")
        subagent = getattr(package, "SUBAGENT", None)
        if subagent is None:
            continue
        if subagent.name != name:
            raise ValueError(f"autune_agent.subagents.{name} exports {subagent.name!r}")
        found[name] = subagent
    return found
