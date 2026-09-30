"""Running what a subagent proposed -- the L1 half (agent-layer.md section 8).

A subagent never writes. It returns ``ProposedAction``s, and this is where the
ones at L1 -- reversible writes, "automatic, notify after" -- are carried out
at the end of the run. L2 is not run here or anywhere yet: it waits for plan
mode and the approval screen, and until then it stays in ``agent_runs.proposed``
as a proposal.

**The module decides the level, not the subagent.** A module lists its writes
in ``ACTIONS`` and the reversible ones among them in ``L1_ACTIONS``; everything
else in ``ACTIONS`` is L2. A proposal marked L1 whose action the module did not
declare L1 is refused, never run -- a subagent cannot demote a write that moves
a person (section 8 rule 2: the module that owns the content owns what happens
to it). The reverse is harmless: a subagent may ask for approval of an L1
action, and it simply waits with the L2s.

**Scope is bound the way a tool call's is** (``registry.bind_scope``):
``team_id`` and ``meeting_id`` come from the run, a different one from the
model is refused, and another team's meeting reads as missing.

**What is kept is what a tool step keeps**: the action's name, its level,
whether it worked, evidence ids, and a reason only when it is one of this
file's own. Never the arguments -- a report body is meeting text -- and never a
module's reason, which may repeat a value the model wrote
(``not a date: '...'``).
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy.orm import Session

from autune_agent.results import ProposedAction, ToolResult
from autune_contracts import MODULES

from .registry import (
    MISSING_ARGUMENT,
    NO_MEETING,
    UNEXPECTED_ARGUMENT,
    RunScope,
    ToolContractError,
    ToolFn,
    bind_scope,
    is_personal_only,
    required_parameters,
    takes_any_keyword,
)

log = logging.getLogger(__name__)

NOT_DECLARED = "not a declared action"
KEPT_FOR_APPROVAL = "its module declares it L2; it waits for approval"
FAILED = "the action failed"
OUT_OF_SCOPE = ("team_id is outside this run's team", "meeting not found")
OWN_REASONS = frozenset(
    {NOT_DECLARED, KEPT_FOR_APPROVAL, FAILED, NO_MEETING, UNEXPECTED_ARGUMENT, *OUT_OF_SCOPE}
)
"""Reasons this layer wrote, and so knows hold no meeting or model text. A
``MISSING_ARGUMENT`` reason is also ours: it names parameters from the code."""


def _own(reason: str | None) -> bool:
    return reason is not None and (reason in OWN_REASONS or reason.startswith(MISSING_ARGUMENT))


Level = Literal["L1", "L2"]


@dataclass(frozen=True)
class Action:
    name: str
    """``<module>.<function>``, the name a ``ProposedAction.tool`` gives."""
    fn: ToolFn
    level: Level
    """As the module declared it. Not what a proposal claims."""

    @property
    def parameters(self) -> frozenset[str]:
        return frozenset(inspect.signature(self.fn).parameters)

    @property
    def required(self) -> frozenset[str]:
        takes_session = list(inspect.signature(self.fn).parameters)[:1] == ["session"]
        return required_parameters(self.fn, skip_first=takes_session)

    def __call__(self, session: Session, **arguments: Any) -> ToolResult:
        # B's writes open their own session (they commit through B's service,
        # the way the board does); a write that wants the run's takes it first.
        params = list(inspect.signature(self.fn).parameters)
        raw = self.fn(session, **arguments) if params[:1] == ["session"] else self.fn(**arguments)
        try:
            return ToolResult.model_validate(raw)
        except ValidationError as exc:
            raise ToolContractError(f"{self.name} broke the return contract: {exc}") from exc


def collect_actions(modules: Iterable[str] = MODULES) -> dict[str, Action]:
    """Every module's writes, with the level its module gave each."""
    actions: dict[str, Action] = {}
    for module in modules:
        path = f"autune_{module}.tools"
        try:
            loaded = import_module(path)
        except ModuleNotFoundError as exc:
            if exc.name != path:
                raise
            continue
        declared = list(getattr(loaded, "ACTIONS", []))
        l1 = list(getattr(loaded, "L1_ACTIONS", []))
        stray = [fn.__name__ for fn in l1 if fn not in declared]
        if stray:
            raise ToolContractError(f"{path}: L1_ACTIONS not in ACTIONS: {', '.join(stray)}")
        for fn in declared:
            if is_personal_only(fn.__name__):
                raise ToolContractError(f"{path}.{fn.__name__} looks personal-only")
            name = f"{module}.{fn.__name__}"
            actions[name] = Action(name=name, fn=fn, level="L1" if fn in l1 else "L2")
    return actions


def execute_l1(
    proposed: Sequence[ProposedAction],
    *,
    actions: Mapping[str, Action],
    session: Session,
    scope: RunScope,
) -> list[dict[str, Any]]:
    """Run each L1 proposal once, in order, and return what ``agent_runs.actions`` keeps.

    One failure does not stop the rest: they are independent writes, and the
    record says which worked. An action that raises is a bug in its module; it
    is logged by name only (the message may carry meeting text) and recorded as
    failed.
    """
    done: list[dict[str, Any]] = []
    for proposal in proposed:
        if proposal.level != "L1":
            continue
        action = actions.get(proposal.tool)
        if action is None:
            result = ToolResult.failure(NOT_DECLARED)
        elif action.level != "L1":
            result = ToolResult.failure(KEPT_FOR_APPROVAL)
        else:
            result = _run(action, proposal, session=session, scope=scope)
        done.append(
            {
                "tool": proposal.tool,
                "level": "L1",
                "ok": result.ok,
                "reason": result.reason if _own(result.reason) else None,
                "evidence": result.evidence,
            }
        )
    return done


def _run(
    action: Action, proposal: ProposedAction, *, session: Session, scope: RunScope
) -> ToolResult:
    bound = bind_scope(
        action.parameters,
        proposal.arguments,
        scope,
        session,
        required=action.required,
        open_ended=takes_any_keyword(action.fn),
    )
    if isinstance(bound, ToolResult):
        return bound
    try:
        return action(session, **bound)
    except Exception as exc:  # noqa: BLE001 - one broken write must not lose the rest
        log.error("agent_action_failed action=%s error=%s", action.name, type(exc).__name__)
        return ToolResult.failure(FAILED)
