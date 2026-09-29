"""Modules as tools (agent-layer.md section 4).

Tools are collected the way ``apps/api`` collects routers: by iterating the
module list, never by appending to a registry (invariant 6). A module with no
``tools.py`` yet contributes nothing and is not an error.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from autune_agent.results import ToolResult
from autune_contracts import MODULES

log = logging.getLogger(__name__)

MAX_TOOL_CALLS = 15
"""Per run, a delegation included (agent-layer.md section 9)."""

ToolFn = Callable[..., Mapping[str, Any]]


class ToolContractError(RuntimeError):
    """A tool returned something that is not a ``ToolResult``. A bug, not a route."""


class BudgetExceededError(RuntimeError):
    """The run used its tool calls. It stops and says so; it does not summarise past it."""


@dataclass(frozen=True)
class Tool:
    name: str
    """``<module>.<function>``, e.g. ``extraction.open_action_items``."""
    description: str
    """The function's docstring. It is the prompt: when to use it comes first."""
    fn: ToolFn

    def __call__(self, session: Session, **arguments: Any) -> ToolResult:
        raw = self.fn(session, **arguments)
        try:
            return ToolResult.model_validate(raw)
        except ValidationError as exc:
            raise ToolContractError(f"{self.name} broke the return contract: {exc}") from exc


def collect_tools(modules: Iterable[str] = MODULES) -> dict[str, Tool]:
    tools: dict[str, Tool] = {}
    for module in modules:
        path = f"autune_{module}.tools"
        try:
            loaded = import_module(path)
        except ModuleNotFoundError as exc:
            if exc.name != path:
                raise  # tools.py exists and something it imports does not
            continue
        for fn in getattr(loaded, "TOOLS", []):
            doc = inspect.getdoc(fn)
            if not doc:
                raise ToolContractError(f"{path}.{fn.__name__} has no docstring to route on")
            tool = Tool(name=f"{module}.{fn.__name__}", description=doc, fn=fn)
            tools[tool.name] = tool
    return tools


class CallBudget:
    """One per run. The main agent and every subagent it delegates to spend from it."""

    def __init__(self, limit: int = MAX_TOOL_CALLS) -> None:
        self.limit = limit
        self.used = 0

    def spend(self, name: str) -> None:
        if self.used >= self.limit:
            raise BudgetExceededError(f"{self.limit} tool calls used; {name} was not called")
        self.used += 1


class Toolbox:
    """The tools one caller may use, bound to a session and the run's budget.

    ``allowed`` is an allow-list: a subagent sees only the tools it named. This
    is how agent-layer.md section 3.1 keeps E's speaking-ratio read away from
    Workload and Follow-up -- enforced here, not asked for in a prompt.
    """

    def __init__(
        self,
        tools: Mapping[str, Tool],
        session: Session,
        budget: CallBudget,
        allowed: Iterable[str] | None = None,
    ) -> None:
        wanted = set(tools) if allowed is None else set(allowed)
        missing = sorted(wanted - set(tools))
        if missing:
            # A subagent may name a tool its module owner has not shipped yet.
            log.warning("tools not registered yet: %s", ", ".join(missing))
        self._tools = {name: tools[name] for name in sorted(wanted & set(tools))}
        self._session = session
        self._budget = budget

    def describe(self) -> dict[str, str]:
        return {name: tool.description for name, tool in self._tools.items()}

    def call(self, name: str, **arguments: Any) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            # A model asking for a tool it was not given is a route to correct,
            # not a crash.
            return ToolResult.failure(f"{name} is not available here")
        self._budget.spend(name)
        return tool(self._session, **arguments)
