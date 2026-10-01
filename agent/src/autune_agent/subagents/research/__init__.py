"""Research subagent -- 김민경 (@mkkim68). agent-layer.md section 3.1.

When a meeting floats an idea or argues over a fact nobody could confirm,
gathers what is known from uploaded material and past meetings into a short
document and proposes sending it to the people involved (L2).
"""

from autune_agent.main.subagents import Subagent
from autune_contracts import INTELLIGENCE_COMPLETED

from .graph import TOOLS, build_with
from .writer import GeminiWriter, Writer


def make_subagent(writer: Writer | None = None) -> Subagent:
    return Subagent(
        name="research",
        description=(
            "Use this when asked to look into what a meeting raised -- questions "
            "or disputes nobody could confirm -- or what the team said about them "
            "before. It writes a short document from the team's own meetings and "
            "proposes sharing it after a person approves. Do not use it for action "
            "items, gaps or a meeting's score."
        ),
        tools=TOOLS,
        build=build_with(writer or GeminiWriter()),
        triggers=(INTELLIGENCE_COMPLETED,),
    )


SUBAGENT = make_subagent()
