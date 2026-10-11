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


def test_cs_one_write_waits_for_a_person_and_no_model_is_offered_it() -> None:
    """The follow-up meeting invites people and posts to the team channel, so
    it is L2; the registry offers ``TOOLS`` to models, the executor alone runs
    ``ACTIONS``."""
    assert [tools.schedule_followup_meeting] == tools.ACTIONS
    assert tools.L1_ACTIONS == []
    assert not set(tools.ACTIONS) & set(tools.TOOLS)


def test_the_write_takes_the_run_scope_and_the_approver() -> None:
    """``user_id`` is the parameter the agent layer fills with the approver,
    never a model (``ASKER_PARAMETER``)."""
    for fn in tools.ACTIONS:
        params = list(inspect.signature(fn).parameters)
        assert params[0] == "session"
        assert set(tools.RUN_SCOPE) | {"meeting_id", "user_id"} <= set(params)
