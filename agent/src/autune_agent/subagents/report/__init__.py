"""Report subagent -- 이승환 (@lsh2217). agent-layer.md section 3.1.

After a meeting's analysis finishes, composes its structured minutes from B, C
and D's tools and proposes that E store them (L1) and post them (L2, after a
person approves) -- section 8 rule 2. That trigger path has no LLM: a fixed tool
order and a template (``render``, in ``template.py``). When a person edits the
draft on E's dashboard, proposes the post of the edited draft again, for
approval (#674). A person's question takes the chat path (``chat.py``): a Gemini
tool loop over E's reads, with ``redraft`` and ``request_post`` turned into
proposals.
"""

from autune_agent.main import Subagent

from . import chat
from .graph import TOOLS, TRIGGERS, build

SUBAGENT = Subagent(
    name="report",
    description=(
        "Use this for anything about module E: a meeting's quality score, the team's "
        "trend and completion, recurring gap patterns, role alignment, the misalignment "
        "prediction, meeting reports (show, redo before posting, ask to post) and weekly "
        "reports and their schedule, and what any of these numbers means. Also when a "
        "meeting's analysis has just finished. Do not use it for action items or "
        "decisions themselves, or to brief a meeting that has not happened."
    ),
    tools=(*TOOLS, *chat.CHAT_READS),
    build=build,
    triggers=TRIGGERS,
    # Its chat reads are E's whole surface, which the ask loop does not hold (#879).
    answers_lookups=True,
)
