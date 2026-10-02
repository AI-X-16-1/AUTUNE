"""Mock tools, a fake router and a reference subagent, for building before modules are ready.

agent-layer.md section 14: a subagent owner builds against these first, so
nobody waits on another module's ``tools.py``. ``example_subagent`` is the
smallest subagent that follows every rule -- copy its shape, not its logic.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langgraph.graph import END, START, StateGraph

from .main.registry import Tool, Toolbox
from .main.subagents import CompiledSubagent, Subagent, SubagentState
from .main.toolcall import Declaration, Step, tools_body
from .results import SubagentResult, ToolResult


def mock_tool(name: str, result: Mapping[str, Any], description: str = "") -> Tool:
    """A registry entry named ``name`` that always returns ``result``.

    ``result`` is the dict a module's tool would return, so it is validated
    exactly as a real one is.
    """
    payload = dict(result)
    return Tool(
        name=name,
        description=description or f"Use this in tests. Mock of {name}.",
        fn=lambda _session, **_arguments: payload,
    )


class FakeRouter:
    """Routes by keyword and composes by echoing the summary.

    ``routes`` maps a substring of the request to a subagent name; the first
    match wins and no match means no subagent.
    """

    def __init__(self, routes: Mapping[str, str] | None = None) -> None:
        self.routes = dict(routes or {})
        self.seen: list[Mapping[str, str]] = []

    def route(self, request: str, subagents: Mapping[str, str]) -> str | None:
        self.seen.append(dict(subagents))
        for needle, name in self.routes.items():
            if needle in request:
                return name
        return None

    def compose(self, request: str, outcome: SubagentResult) -> str:
        return outcome.result.summary


def example_subagent(name: str, tools: tuple[str, ...]) -> Subagent:
    """Call each allowed tool once and report what came back.

    The pattern to copy: read through the toolbox only, hand back a
    ``SubagentResult`` in ``outcome``, and propose rather than act.
    """

    def build(toolbox: Toolbox) -> CompiledSubagent:
        def gather(state: SubagentState) -> SubagentState:
            results = [toolbox.call(tool) for tool in toolbox.describe()]
            usable = [r for r in results if r.ok]
            summary = " ".join(r.summary for r in usable) or "읽을 수 있는 도구가 없습니다."
            result = ToolResult(
                ok=bool(usable),
                reason=None if usable else "no tool answered",
                summary=summary,
                items=[item for r in usable for item in r.items],
                evidence=[e for r in usable for e in r.evidence],
                confidence=min((r.confidence for r in usable), default=0.0),
            )
            return {"outcome": SubagentResult(result=result)}

        graph = StateGraph(SubagentState)
        graph.add_node("gather", gather)
        graph.add_edge(START, "gather")
        graph.add_edge("gather", END)
        return graph.compile()

    return Subagent(
        name=name,
        description=f"Use this in tests. Example subagent {name}.",
        tools=tools,
        build=build,
    )


class ScriptedToolModel:
    """A ``ToolModel`` that replies from a script: a list of calls, or text.

    ``sent`` keeps every body it was handed, so a test can read what would
    have left. It runs the same request builder ``GeminiTools`` does.
    """

    def __init__(self, steps: list[Step]) -> None:
        self._steps = list(steps)
        self.sent: list[dict[str, Any]] = []
        self.last_parts: list[dict[str, Any]] = []

    def step(
        self, instructions: str, turns: list[dict[str, Any]], declarations: list[Declaration]
    ) -> Step:
        self.sent.append(tools_body(instructions, [dict(t) for t in turns], declarations))
        reply = self._steps.pop(0) if self._steps else "DONE"
        self.last_parts = (
            [{"functionCall": {"name": c.name, "args": c.args}} for c in reply]
            if isinstance(reply, list)
            else [{"text": reply}]
        )
        return reply
