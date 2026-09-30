"""Report subagent -- 이승환 (@lsh2217). agent-layer.md section 3.1.

After a meeting's analysis finishes, composes its structured minutes from B, C
and D's tools and proposes that E store them (L1) and post them (L2, after a
person approves) -- section 8 rule 2. No LLM: a fixed tool order and a
template (``render``).
"""

from autune_agent.main import Subagent

from .graph import TOOLS, TRIGGER, build

SUBAGENT = Subagent(
    name="report",
    description=(
        "Use this when a meeting's analysis has just finished, or when asked to "
        "write that one meeting's summary report. Do not use it for anything "
        "across several meetings or for a team overview, and not to send a "
        "report again: one already posted is not posted twice."
    ),
    tools=TOOLS,
    build=build,
    triggers=(TRIGGER,),
)
