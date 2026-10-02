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

It also wakes on its own, two ways (mentoring, 2026-10-01):

- every six hours, per team (``Periodic``, #634). A load changes between
  meetings -- an item is confirmed, finished or goes past its date in the app,
  and the API process publishes no event about it (#170) -- so "state" in
  section 3.1 is read on a timer;
- after each meeting is processed (``INTELLIGENCE_COMPLETED``), when new items
  land on people.

Either run is the team's. Its proposals wait for the manager like a chat run's,
and because they judge the whole team (``proposals_per="team"``, #631) a later
run's replace the earlier ones still waiting, whatever woke it: two runs never
leave the same item proposed to two people.
"""

from __future__ import annotations

from autune_agent.main.subagents import Periodic, Subagent
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
    triggers=(INTELLIGENCE_COMPLETED, Periodic(hours=6)),
    proposals_per="team",
)
