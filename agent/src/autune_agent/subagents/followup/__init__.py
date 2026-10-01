"""Follow-up subagent -- 박재경 (@PARKJAEKYUNG0525). agent-layer.md section 3.1.

When a meeting leaves open what the previous one also left open, or leaves
several high-severity gaps and a raised question, proposes a follow-up meeting
to an approver with scope ``followup`` only -- the team lead. The proposal is
one L2 ``extraction.add_action_item``. Once the lead gives it a due date and an
assignee on the board, B's calendar sync (#441) puts it on that person's
calendar.

Reads C's topic-level results only -- open gaps and the template items left
open twice -- and B's raised questions. Never participation per role or per
person: in a small team a role is a person (privacy.md section 3), and it does
not read ``silent_share`` either.

The rule is in ``rules.py``, the subgraph in ``graph.py``, the design in
``agent/docs/specs/2026-09-30-followup-subagent-design.md``.
"""

from __future__ import annotations

from autune_agent.main.subagents import Subagent
from autune_contracts import INTELLIGENCE_COMPLETED

from .graph import TOOLS, build

SUBAGENT = Subagent(
    name="followup",
    description=(
        "Use this when asked whether a meeting needs a follow-up, and after every "
        "analysed meeting. It proposes one follow-up meeting to the team lead "
        "when the meeting left open what the previous meeting also left open, "
        "or left several high-severity gaps and a raised question. The lead "
        "approves it before anything happens. Do not use it for what a meeting "
        "decided or for who is overloaded -- those are D's and Workload's."
    ),
    tools=TOOLS,
    build=build,
    triggers=(INTELLIGENCE_COMPLETED,),
)
