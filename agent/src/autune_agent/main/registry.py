"""Modules as tools (agent-layer.md section 4).

Tools are collected the way ``apps/api`` collects routers: by iterating the
module list, never by appending to a registry (invariant 6). A module with no
``tools.py`` yet contributes nothing and is not an error.
"""

from __future__ import annotations

import inspect
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from autune_agent.results import ToolResult
from autune_contracts import MODULES
from autune_core import Meeting

log = logging.getLogger(__name__)

MAX_TOOL_CALLS = 15
"""Per run, a delegation included (agent-layer.md section 9)."""

ToolFn = Callable[..., Mapping[str, Any]]


PERSONAL_ONLY_BACKSTOP = "speakingratio"
"""Caught in a tool name with case, ``_`` and ``-`` removed, when a module forgot
to declare the tool. The declaration below is the rule; this is the net."""

TRACING_VARIABLES = tuple(
    f"{prefix}_{switch}"
    for prefix in ("LANGSMITH", "LANGCHAIN")
    for switch in ("TRACING_V2", "TRACING")
)
"""langsmith arrives with langgraph. Any of these set sends the graph's state --
requests and tool results -- to LangSmith, outside ``packages/integrations`` and
its privacy guard. Refused, not warned about.

Built from the names LangSmith itself reads (``langsmith.utils.tracing_is_enabled``:
``TRACING_V2`` under either prefix, then ``TRACING``), so none is left out --
``LANGCHAIN_TRACING`` was (#803). ``test_registry`` checks the list against the
installed LangSmith."""


def is_personal_only(name: str) -> bool:
    return PERSONAL_ONLY_BACKSTOP in name.lower().replace("_", "").replace("-", "")


def refuse_tracing(environ: Mapping[str, str] = os.environ) -> None:
    on = [v for v in TRACING_VARIABLES if environ.get(v, "").strip().lower() in {"1", "true"}]
    if on:
        raise RuntimeError(f"agent layer refuses to run with tracing on: unset {', '.join(on)}")


class ToolContractError(RuntimeError):
    """A tool returned something that is not a ``ToolResult``. A bug, not a route."""


class BudgetExceededError(RuntimeError):
    """The run used its tool calls. It stops and says so; it does not summarise past it."""


@dataclass(frozen=True)
class RunScope:
    """The team a run answers for, and the meeting it is about if any.

    Set by whoever started the run -- ``/api/agent/chat`` after checking
    membership, a trigger from the row that fired it -- and never by the model.
    ``Toolbox.call`` holds every tool call to it (review on #449).
    """

    team_id: str
    meeting_id: str | None = None
    user_id: str | None = None
    """Who asked, for a chat turn; None for a run nobody asked for. A read that
    takes ``user_id`` and is called without one is about this person -- "내 기한
    지난 거" -- since the model cannot know the asker's id (#677 review)."""


@dataclass(frozen=True)
class Tool:
    name: str
    """``<module>.<function>``, e.g. ``extraction.open_action_items``."""
    description: str
    """The function's docstring. It is the prompt: when to use it comes first."""
    fn: ToolFn

    @property
    def parameters(self) -> frozenset[str]:
        """Named parameters after the session, for filling scope arguments in."""
        return frozenset(inspect.signature(self.fn).parameters)

    @property
    def required(self) -> frozenset[str]:
        """Parameters the call must supply. The first is the session, always passed."""
        return required_parameters(self.fn, skip_first=True)

    def __call__(self, session: Session, **arguments: Any) -> ToolResult:
        raw = self.fn(session, **arguments)
        try:
            return ToolResult.model_validate(raw)
        except ValidationError as exc:
            raise ToolContractError(f"{self.name} broke the return contract: {exc}") from exc


def collect_tools(modules: Iterable[str] = MODULES) -> dict[str, Tool]:
    """Every module's agent tools, minus its personal-only reads.

    A module lists a read that returns one person's own data -- a speaking
    ratio -- in ``PERSONAL_ONLY_TOOLS`` in its ``tools.py``. Invariant 11 sends
    that data to its subject and nobody else, and the agent always answers on
    someone else's behalf, so such a tool is never registered at all. An
    undeclared tool whose name gives it away is a mistake and raises.
    """
    tools: dict[str, Tool] = {}
    for module in modules:
        path = f"autune_{module}.tools"
        try:
            loaded = import_module(path)
        except ModuleNotFoundError as exc:
            if exc.name != path:
                raise  # tools.py exists and something it imports does not
            continue
        personal = set(getattr(loaded, "PERSONAL_ONLY_TOOLS", []))
        for fn in getattr(loaded, "TOOLS", []):
            if fn in personal:
                continue
            if is_personal_only(fn.__name__):
                raise ToolContractError(
                    f"{path}.{fn.__name__} looks personal-only; list it in PERSONAL_ONLY_TOOLS"
                )
            doc = inspect.getdoc(fn)
            if not doc:
                raise ToolContractError(f"{path}.{fn.__name__} has no docstring to route on")
            tool = Tool(name=f"{module}.{fn.__name__}", description=doc, fn=fn)
            tools[tool.name] = tool
    return tools


class CallBudget:
    """One per run. The main agent and every subagent it delegates to spend from it.

    It also keeps the run's trace, ``steps``: each call's tool name, whether it
    answered, and its evidence ids -- never a summary, an item or a reason, any
    of which a tool may fill with meeting text. It lives here rather than in the
    graph so a run stopped by the cap still has everything up to the call that
    stopped it.
    """

    def __init__(self, limit: int = MAX_TOOL_CALLS) -> None:
        self.limit = limit
        self.used = 0
        self.steps: list[dict[str, Any]] = []

    def spend(self, name: str) -> None:
        if self.used >= self.limit:
            raise BudgetExceededError(f"{self.limit} tool calls used; {name} was not called")
        self.used += 1


class Toolbox:
    """The tools one caller may use, bound to a session and the run's budget.

    ``allowed`` is an allow-list: a subagent sees only the tools it named. This
    is how agent-layer.md section 3.1 keeps E's speaking-ratio read away from
    Workload and Follow-up -- enforced here, not asked for in a prompt.

    ``scope`` is the other half of the same idea. Module tools take their range
    as arguments -- ``open_action_items(session, team_id)`` -- and the model
    writes the arguments, so without this a member of one team could name
    another team's id in a sentence and get that team's work back. The toolbox
    therefore writes ``team_id`` itself, refuses a call that names a different
    one, and refuses a ``meeting_id`` from another team as if it did not exist
    (the same 404-not-403 rule as #437). Other ids a tool takes -- an action
    item, a decision -- are the tool's to check against the ``team_id`` it is
    handed; every such tool on main already takes one.
    """

    def __init__(
        self,
        tools: Mapping[str, Tool],
        session: Session,
        budget: CallBudget,
        *,
        allowed: Iterable[str],
        scope: RunScope,
    ) -> None:
        # Required, with no "everything" default: a caller that forgets it
        # must fail to construct, not get every tool (review on #432).
        wanted = set(allowed)
        missing = sorted(wanted - set(tools))
        if missing:
            # A subagent may name a tool its module owner has not shipped yet.
            log.warning("tools not registered yet: %s", ", ".join(missing))
        self._tools = {name: tools[name] for name in sorted(wanted & set(tools))}
        self._session = session
        self._budget = budget
        self._scope = scope

    def describe(self) -> dict[str, str]:
        return {name: tool.description for name, tool in self._tools.items()}

    def call(self, name: str, **arguments: Any) -> ToolResult:
        # Spent before the lookup: a model that keeps asking for a tool it was
        # not given still reaches the cap (review on #432).
        self._budget.spend(name)
        tool = self._tools.get(name)
        if tool is None:
            # A route to correct, not a crash.
            result = ToolResult.failure(f"{name} is not available here")
        else:
            if (
                ASKER_PARAMETER in tool.parameters
                and ASKER_PARAMETER not in arguments
                and self._scope.user_id is not None
            ):
                # A read's omitted person is the asker. An action's is pinned to
                # the asker whatever was passed, in run_action (#862).
                arguments = {**arguments, ASKER_PARAMETER: self._scope.user_id}
            scoped = bind_scope(
                tool.parameters,
                arguments,
                self._scope,
                self._session,
                required=tool.required,
                open_ended=takes_any_keyword(tool.fn),
            )
            result = scoped if isinstance(scoped, ToolResult) else tool(self._session, **scoped)
        self._budget.steps.append(
            {
                "tool": name,
                "ok": result.ok,
                "evidence": result.evidence,
                "truncated": result.truncated,
            }
        )
        return result


ASKER_PARAMETER = "user_id"
"""The parameter that names one person. A read left without it gets the person
asking; an action always gets the person asking (``run_action``, #862)."""

NO_MEETING = "this run is about no meeting; pass meeting_id"
UNEXPECTED_ARGUMENT = "unexpected argument"
"""Without the name: it is whatever the model wrote."""
MISSING_ARGUMENT = "missing argument: "
"""Followed by parameter names, which come from the code, never from the model."""


def takes_any_keyword(fn: Callable[..., Any]) -> bool:
    return any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in inspect.signature(fn).parameters.values()
    )


def required_parameters(fn: Callable[..., Any], *, skip_first: bool) -> frozenset[str]:
    params = list(inspect.signature(fn).parameters.values())
    if skip_first:
        params = params[1:]
    return frozenset(
        p.name
        for p in params
        if p.default is inspect.Parameter.empty
        and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    )


def bind_scope(
    parameters: frozenset[str],
    arguments: Mapping[str, Any],
    scope: RunScope,
    session: Session,
    *,
    required: frozenset[str] = frozenset(),
    open_ended: bool = True,
) -> dict[str, Any] | ToolResult:
    """``arguments`` with the run's scope written in, or the refusal.

    One function for a tool call and for an action's execution
    (``main/actions.py``), so the rule cannot drift between reading and
    writing: a model that could not read another team's work cannot change it
    either (#449, answered on its review).
    """
    bound = dict(arguments)
    team_id = bound.get("team_id")
    if team_id is not None and team_id != scope.team_id:
        # Not echoed back: the id is whatever the model wrote.
        return ToolResult.failure("team_id is outside this run's team")
    if "team_id" in parameters:
        bound["team_id"] = scope.team_id
    meeting_id = bound.get("meeting_id")
    if meeting_id is None and scope.meeting_id and "meeting_id" in parameters:
        bound["meeting_id"] = meeting_id = scope.meeting_id
    if meeting_id is not None:
        meeting = session.get(Meeting, meeting_id)
        if meeting is None or meeting.team_id != scope.team_id:
            return ToolResult.failure("meeting not found")
    # A route to correct, not a TypeError that fails the whole run: a chat run
    # has no meeting in its scope, so a per-meeting tool called without one
    # would otherwise crash (#509 review).
    if not open_ended and set(bound) - parameters:
        return ToolResult.failure(UNEXPECTED_ARGUMENT)
    missing = required - set(bound)
    if "meeting_id" in missing:
        return ToolResult.failure(NO_MEETING)
    if missing:
        return ToolResult.failure(MISSING_ARGUMENT + ", ".join(sorted(missing)))
    return bound
