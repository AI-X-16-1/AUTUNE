"""Workload subagent -- 강민구 (@kjfcvx12). agent-layer.md section 3.1.

When one person is overloaded and another is free, proposes a redistribution
to an approver with scope ``workload`` only. Reads counts of work, never
speech: no speaking-ratio tool may be in its allow-list (privacy.md section 3),
and ``Subagent`` refuses one.

The rules are in ``plan.py``, the subgraph in ``graph.py``. Google Calendar
comes through B's ``team_busy_hours`` (busy windows only, the team's own
connection); without one the subagent decides on work alone. Jira reads wait
on #82.
"""

from __future__ import annotations

from autune_agent.main.subagents import Subagent

from .graph import TOOLS, build

SUBAGENT = Subagent(
    name="workload",
    description=(
        "Use this when asked whether work is piling up on someone, whether it "
        "should be spread differently, or on the periodic workload check. It "
        "proposes moving a few open action items from the most loaded people to "
        "people with little or none open, for the manager to approve one by one. "
        "Do not use it for which items are late or due soon, or for one "
        "meeting's outcome -- those are direct reads of module B."
    ),
    tools=TOOLS,
    build=build,
)
