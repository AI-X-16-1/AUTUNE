"""Workload subagent -- 강민구 (@kjfcvx12). agent-layer.md section 3.1.

When one person is overloaded and another is free, proposes a redistribution
to an approver with scope ``workload`` only. Reads counts of work, never
speech: no speaking-ratio tool may be in its allow-list (privacy.md section 3),
and ``Subagent`` refuses one.

The rules are in ``plan.py``, the subgraph in ``graph.py``. It decides from
Autune's own data -- open, overdue and finished confirmed items -- and reads no
calendar (#435, 2026-09-30): a team Google account sees only members of one
Workspace, a calendar Autune creates holds only what Autune wrote, and a
person's own grant serves only their own work. Busy hours from a person's own
calendar, with their consent, are a later decision there. Jira reads wait on
#82.

It also wakes after each meeting is processed (``INTELLIGENCE_COMPLETED``):
that is when new items land on people, so it is when a load changes -- the
"state" trigger of section 3.1, until the main agent has a periodic one
(mentoring, 2026-10-01). The run is the meeting's team's; its proposals wait
for the manager like a chat run's, and a later run's replace the earlier ones
still waiting (the main agent's team-wide supersede for Workload).
"""

from __future__ import annotations

from autune_agent.main.subagents import Subagent
from autune_contracts import INTELLIGENCE_COMPLETED

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
    triggers=(INTELLIGENCE_COMPLETED,),
)
