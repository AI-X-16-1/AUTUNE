"""The ask loop: which tools a turn may use, how they are declared, and the rounds."""

from __future__ import annotations

from typing import Any

from autune_agent.main.ask import MEETING_TOOLS, TEAM_TOOLS, declare, tool_set
from autune_agent.main.registry import RunScope, Tool

TEAM = RunScope(team_id="team_a")
MEETING = RunScope(team_id="team_a", meeting_id="mtg_1")


def _tool(name: str, fn: Any, doc: str = "Use this to test. More text here.") -> Tool:
    return Tool(name=name, description=doc, fn=fn)


def test_the_tool_set_follows_the_scope() -> None:
    assert tool_set(MEETING) == MEETING_TOOLS
    assert tool_set(TEAM) == TEAM_TOOLS
    assert len(MEETING_TOOLS) <= 9 and len(TEAM_TOOLS) <= 9


def test_team_id_is_never_declared_and_meeting_id_only_without_a_meeting() -> None:
    def gaps(session: Any, team_id: str, meeting_id: str) -> dict[str, Any]:
        return {}

    tools = {"gap.open_gaps": _tool("gap.open_gaps", gaps)}

    (in_meeting,) = declare(tools, MEETING)

    assert in_meeting.parameters["properties"] == {}
    assert "required" not in in_meeting.parameters
    assert declare(tools, TEAM) == []  # gap.open_gaps is not in the team set


def test_types_map_and_defaults_are_optional() -> None:
    def search(
        session: Any,
        team_id: str,
        query: str,
        limit: int = 5,
        exact: bool = False,
        terms: list[str] | None = None,
        meeting_id: str | None = None,
    ) -> dict[str, Any]:
        return {}

    (decl,) = declare(
        {"audio.search_team_meetings": _tool("audio.search_team_meetings", search)}, TEAM
    )

    assert decl.name == "audio__search_team_meetings"
    assert decl.parameters["properties"] == {
        "query": {"type": "STRING"},
        "limit": {"type": "INTEGER"},
        "exact": {"type": "BOOLEAN"},
        "terms": {"type": "ARRAY", "items": {"type": "STRING"}},
        "meeting_id": {"type": "STRING"},
    }
    assert decl.parameters["required"] == ["query"]


def test_the_description_is_the_first_sentence_cut_to_120() -> None:
    long = "Use this " + "x" * 200 + ". Second sentence."

    def recent(session: Any, team_id: str) -> dict[str, Any]:
        return {}

    (decl,) = declare({"audio.recent_meetings": _tool("audio.recent_meetings", recent, long)}, TEAM)

    assert len(decl.description) <= 120
    assert "Second" not in decl.description


def test_an_unmappable_parameter_drops_the_tool() -> None:
    def odd(session: Any, team_id: str, when: dict[str, Any]) -> dict[str, Any]:
        return {}

    assert declare({"audio.recent_meetings": _tool("audio.recent_meetings", odd)}, TEAM) == []


def test_tools_outside_the_set_are_not_declared() -> None:
    def anything(session: Any, team_id: str) -> dict[str, Any]:
        return {}

    assert (
        declare({"extraction.add_action_item": _tool("extraction.add_action_item", anything)}, TEAM)
        == []
    )
