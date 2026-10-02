"""Briefing subagent -- 문민재 (@mminjae97). agent-layer.md section 3.1.

Composes the picture a meeting that has not started should open with: the
earlier meeting it follows and what that one decided, the Jira issues it should
take up, the confirmed work that is late, and the gaps nobody closed. Read-only
and no LLM -- a fixed tool order and a template (``render``).

It does not send. The brief ten minutes before a meeting is the Meeting Context
Engine's own surface and it sends exactly one (section 8 rule 2, section 6); this
subagent answers a person who asks, from a meeting's screen or in chat. Waking on
a timer is not declared: it would produce an answer nobody reads, because a
woken run's text is not stored (section 8 rule 8) and nothing here posts.
"""

from autune_agent.main import Subagent

from .graph import TOOLS, build

SUBAGENT = Subagent(
    name="briefing",
    description=(
        "Use this when asked to prepare for or brief a meeting that has not "
        "happened yet: what the previous meeting decided, which Jira issues it "
        "should take up, which action items are late and which gaps were left "
        "open. Do not use it for a meeting that has already been analysed -- the "
        "report subagent summarises that -- or for anything across several "
        "meetings. It reads and answers; it never posts a brief."
    ),
    tools=TOOLS,
    build=build,
)
