"""The layer's own tools and actions sit in the registry beside the modules'."""

from __future__ import annotations

from typing import Any

import pytest

from autune_agent.main import own_tools


def _read(session: Any, team_id: str) -> dict[str, Any]:
    """Use this in tests."""
    return {"ok": True, "summary": "x"}


def _write(session: Any, team_id: str) -> dict[str, Any]:
    """Share it."""
    return {"ok": True, "summary": "x"}


def test_own_tools_are_named_under_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(own_tools, "TOOLS", [_read])
    monkeypatch.setattr(own_tools, "ACTIONS", [_write])
    monkeypatch.setattr(own_tools, "L1_ACTIONS", [])

    assert list(own_tools.collect_own_tools()) == ["agent._read"]
    actions = own_tools.collect_own_actions()
    assert {n: a.level for n, a in actions.items()} == {"agent._write": "L2"}


def test_an_own_tool_without_a_docstring_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def bare(session: Any) -> dict[str, Any]:
        return {"ok": True, "summary": "x"}

    monkeypatch.setattr(own_tools, "TOOLS", [bare])

    with pytest.raises(own_tools.ToolContractError):
        own_tools.collect_own_tools()
