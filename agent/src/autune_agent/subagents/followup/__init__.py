"""Follow-up subagent -- 박재경 (@PARKJAEKYUNG0525). agent-layer.md section 3.1.

When a meeting leaves open what the previous one also left open, or leaves
several high gaps and a question behind, proposes a follow-up meeting to an
approver with scope ``followup`` only -- the team lead. Design:
``agent/docs/specs/2026-09-30-followup-subagent-design.md``.

Reads topics, never people or roles: C's open gaps and B's open questions, no
participation and no ``silent_share`` (spec section 6; in a small team a role
is a person, privacy.md section 3). For the suggested date it reads B's due
dates and whether each is confirmed, never who owns an item (#963). The rule
is in ``rules.py``, the subgraph in ``graph.py``.
"""

from __future__ import annotations

from autune_agent.main.subagents import Subagent
from autune_contracts import INTELLIGENCE_COMPLETED

from .graph import TOOLS, build

SUBAGENT = Subagent(
    name="followup",
    description=(
        "Use this when asked whether a meeting needs a follow-up meeting, or "
        "whether something keeps being left open from one meeting to the next. "
        "It checks the gaps a meeting left open against the previous meeting and "
        "proposes a follow-up meeting for the team lead to approve. Do not use it "
        "for one meeting's gaps on their own, action items or who is overloaded."
    ),
    tools=TOOLS,
    build=build,
    triggers=(INTELLIGENCE_COMPLETED,),
)
