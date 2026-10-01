"""The shape the agent registry collects C's tools by (agent-layer.md section 4).

The registry is in ``autune_agent``, which a module may not import, so this
checks what it relies on from here: a docstring that says when to use the tool
first, ``team_id`` for the run's scope to fill, and nothing personal-only.
"""

from __future__ import annotations

import inspect

from autune_gap import tools


def test_every_tool_says_when_to_use_it() -> None:
    for fn in tools.TOOLS:
        doc = inspect.getdoc(fn)
        assert doc is not None and doc.startswith("Use this")


def test_every_tool_takes_the_session_then_the_run_scope() -> None:
    for fn in tools.TOOLS:
        params = list(inspect.signature(fn).parameters)
        assert params[0] == "session"
        assert set(tools.RUN_SCOPE) <= set(params)


def test_nothing_is_personal_only_and_no_name_looks_it() -> None:
    assert tools.PERSONAL_ONLY_TOOLS == []
    for fn in tools.TOOLS:
        assert "speakingratio" not in fn.__name__.replace("_", "").lower()


def test_c_offers_no_writes() -> None:
    assert not hasattr(tools, "ACTIONS")
