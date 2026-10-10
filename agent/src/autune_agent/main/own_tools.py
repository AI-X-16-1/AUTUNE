"""The agent layer's own tools and actions, registered as ``agent.<function>``.

A module's tools come from its ``tools.py``; the layer's come from here, under
the same rules -- a docstring to route on, a ``ToolResult``-shaped dict, no
personal-only read -- and the same ``bind_scope``. Section 8: a write to an
``agent_*`` table is L0, so a tool may write one; anything a person sees is an
action and its level is declared here.
"""

from __future__ import annotations

import inspect
from typing import Any

from autune_agent.live.notes import live_research_notes
from autune_agent.research_store import save_research_document, share_research_document

from .actions import Action
from .registry import Tool, ToolContractError, is_personal_only

PREFIX = "agent"

TOOLS: list[Any] = [save_research_document, live_research_notes]
ACTIONS: list[Any] = [share_research_document]
L1_ACTIONS: list[Any] = []


def collect_own_tools() -> dict[str, Tool]:
    tools: dict[str, Tool] = {}
    for fn in TOOLS:
        if is_personal_only(fn.__name__):
            raise ToolContractError(f"{PREFIX}.{fn.__name__} looks personal-only")
        doc = inspect.getdoc(fn)
        if not doc:
            raise ToolContractError(f"{PREFIX}.{fn.__name__} has no docstring to route on")
        name = f"{PREFIX}.{fn.__name__}"
        tools[name] = Tool(name=name, description=doc, fn=fn)
    return tools


def collect_own_actions() -> dict[str, Action]:
    stray = [fn.__name__ for fn in L1_ACTIONS if fn not in ACTIONS]
    if stray:
        raise ToolContractError(f"own L1_ACTIONS not in ACTIONS: {', '.join(stray)}")
    actions: dict[str, Action] = {}
    for fn in ACTIONS:
        if is_personal_only(fn.__name__):
            raise ToolContractError(f"{PREFIX}.{fn.__name__} looks personal-only")
        name = f"{PREFIX}.{fn.__name__}"
        actions[name] = Action(name=name, fn=fn, level="L1" if fn in L1_ACTIONS else "L2")
    return actions
