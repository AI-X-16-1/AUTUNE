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
from typing import Any, Protocol, TypedDict

from autune_agent.results import SubagentResult

from .registry import Toolbox, is_personal_only

SUBAGENT_NAMES = ("research", "briefing", "followup", "workload", "report")
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

    def __post_init__(self) -> None:
        # The registry never holds a declared personal-only tool; this refuses
        # the name as well, so the mistake surfaces where it was written.
        personal = [tool for tool in self.tools if is_personal_only(tool)]
        if personal:
            raise ValueError(f"subagent {self.name} may not read personal-only tools: {personal}")


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
