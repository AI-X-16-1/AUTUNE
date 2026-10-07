"""Tracker subagent ("할 일 챙김") -- 강민구 (@kjfcvx12). The sixth subagent, #856.

Finds a team's confirmed action items that have stopped moving and proposes
the change that would settle each. It proposes and nothing more -- every
change waits for a person holding ``any`` (the manager), one by one. It asks
for no approval scope of its own.

The scope is the one mkkim68 accepted on #856 (2026-10-07):

- confirmed items only. An item still waiting for confirmation is never
  raised as an approval card (agent-layer rule 3); B's morning DM and the
  review screen keep those;
- B's own L2 writes, nothing new in this layer;
- no notify-only path: the layer has "propose, then approve" and "answer in
  chat", and nothing that tells a person something without an approval.

**What it proposes today is one thing: moving the due date of an item that is
past it** (``set_action_item_due_date``). #856 also accepts closing an item
carried through three or more meetings; that waits for module B to mark a
close apart from finished work, so that nobody is told they finished what the
manager closed (the user, 2026-10-07 -- ``plan.py`` says why).

The rules are in ``plan.py``, the subgraph in ``graph.py``. It is about items
and never about people: no count or ranking of what a person has left undone.

It wakes on its own, two ways:

- once a week, per team (``Periodic(hours=168)``). People already get their
  own morning DM and Monday digest; this is the team's look, not a daily one;
- after each meeting is processed (``INTELLIGENCE_COMPLETED``).

Either run is the team's (``proposals_per="team"``): a later run's proposals
replace the earlier ones still waiting, so the same item is never left
proposed twice and a waiting card's new date is never older than the last run.
"""

from __future__ import annotations

from autune_agent.main.subagents import Periodic, Subagent
from autune_contracts import INTELLIGENCE_COMPLETED

from .graph import TOOLS, build

SUBAGENT = Subagent(
    name="tracker",
    description=(
        "Use this when asked to tidy up action items that have stopped moving "
        "-- confirmed items past their due date -- or on the periodic check of "
        "them. It proposes moving a late item's due date a week on, for the "
        "manager to approve one by one. Do not use it for which items are late "
        "or due soon -- that is a direct read of module B -- for who holds too "
        "much (Workload), or for scheduling a follow-up meeting (Follow-up)."
    ),
    tools=TOOLS,
    build=build,
    triggers=(INTELLIGENCE_COMPLETED, Periodic(hours=168)),
    proposals_per="team",
)
