"""The function-calling wire shapes: names, the request body, and how it is measured."""

from __future__ import annotations

from autune_agent.main.toolcall import Declaration, body_chars, from_wire, to_wire, tools_body


def test_names_round_trip_through_double_underscores() -> None:
    assert to_wire("extraction.open_action_items") == "extraction__open_action_items"
    assert from_wire("extraction__open_action_items") == "extraction.open_action_items"


def test_the_body_carries_instructions_turns_and_declarations() -> None:
    decl = Declaration(
        name="gap__open_gaps",
        description="Use this to see what a meeting left open.",
        parameters={"type": "OBJECT", "properties": {}},
    )
    turns = [{"role": "user", "parts": [{"text": "열린 갭?"}]}]

    body = tools_body("지시", turns, [decl])

    assert body["systemInstruction"] == {"parts": [{"text": "지시"}]}
    assert body["contents"] == turns
    assert body["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": "gap__open_gaps",
                    "description": "Use this to see what a meeting left open.",
                    "parameters": {"type": "OBJECT", "properties": {}},
                }
            ]
        }
    ]
    assert body["generationConfig"] == {"temperature": 0}


def test_no_declarations_means_no_tools_key() -> None:
    assert "tools" not in tools_body("지시", [], [])


def test_size_counts_every_string_but_the_addressing_keys() -> None:
    body = {"a": "1234", "role": "model", "nested": [{"b": "56"}]}

    assert body_chars(body, frozenset({"role"})) == 6
