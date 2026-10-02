"""Function calling on the wire: the types the ask loop and its model share.

Kept apart from ``gemini.py`` so the loop (``ask.py``) and the model
(``GeminiTools``) both import it and neither imports the other. These are also
the seams a later LangChain ``create_agent`` takes (spec section 5): a model's
``_generate`` around ``ToolModel.step``, and a tool around ``call_tool``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from autune_integrations.privacy import strings_in


@dataclass(frozen=True)
class FunctionCall:
    name: str
    """The wire name (``module__function``); ``from_wire`` gives the tool's."""
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Declaration:
    name: str
    description: str
    parameters: dict[str, Any]


Step = str | list[FunctionCall]
"""Text when the model is done, or the calls it wants run."""


class ToolModel(Protocol):
    def step(
        self, instructions: str, turns: list[dict[str, Any]], declarations: list[Declaration]
    ) -> Step: ...


def to_wire(name: str) -> str:
    return name.replace(".", "__")


def from_wire(name: str) -> str:
    return name.replace("__", ".")


def tools_body(
    instructions: str, turns: list[dict[str, Any]], declarations: list[Declaration]
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": instructions}]},
        "contents": turns,
        "generationConfig": {"temperature": 0},
    }
    if declarations:
        body["tools"] = [
            {
                "functionDeclarations": [
                    {"name": d.name, "description": d.description, "parameters": d.parameters}
                    for d in declarations
                ]
            }
        ]
    return body


def body_chars(body: dict[str, Any], addressing: frozenset[str]) -> int:
    """What ``check_outbound`` will count for ``body``: the same walk, the same exemptions."""
    return len("".join(strings_in(body, addressing=addressing)))
