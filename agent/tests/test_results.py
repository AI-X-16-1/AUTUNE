"""A proposal's tool and kind are code names, so no stored row can carry a sentence."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from autune_agent.results import ProposedAction

SENTENCE = "김 팀장에게 금요일까지 배포하라고 전달"


def _proposal(**overrides: Any) -> ProposedAction:
    fields: dict[str, Any] = {
        "kind": "reassign_action_item",
        "title": "t",
        "tool": "extraction.reassign_action_item",
        "level": "L2",
        "rationale": "r",
    }
    return ProposedAction(**{**fields, **overrides})


@pytest.mark.parametrize("field", ["tool", "kind"])
def test_a_sentence_in_tool_or_kind_is_refused_and_not_echoed(field: str) -> None:
    with pytest.raises(ValidationError) as error:
        _proposal(**{field: SENTENCE})

    assert "김" not in str(error.value)
    assert SENTENCE not in repr(error.value)


@pytest.mark.parametrize(
    "tool",
    ["reassign", "Extraction.Reassign", "agent.share 김", "a.", "x.y\n", "a." + "b" * 128],
)
def test_a_tool_that_is_not_a_registry_name_is_refused(tool: str) -> None:
    with pytest.raises(ValidationError):
        _proposal(tool=tool)


@pytest.mark.parametrize("kind", ["Note", "", "k" * 65, "a b", "note\n"])
def test_a_kind_that_is_not_a_code_name_is_refused(kind: str) -> None:
    with pytest.raises(ValidationError):
        _proposal(kind=kind)


@pytest.mark.parametrize(
    "tool",
    ["extraction.reassign_action_item", "agent.share_research_document", "fake.post", "x.y"],
)
def test_a_registry_name_passes(tool: str) -> None:
    assert _proposal(tool=tool).tool == tool
