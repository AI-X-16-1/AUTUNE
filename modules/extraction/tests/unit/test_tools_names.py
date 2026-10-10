"""B's name hiding as a public read in ``tools`` (#1226).

``hide_names`` and ``names_not_sent`` are what the agent layer's live research
is meant to call instead of its copy (``autune_agent.live.names``). The cases
mirror that copy's tests (``agent/tests/test_live_names.py``) so the two are
shown to answer alike, and a few check that this is B's own matching, not a
third one. SQLite in memory for the roster, as ``test_roster_names`` does.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from autune_core import Base, Meeting, Participant, TeamMember, User
from autune_extraction import service, tools
from autune_extraction.pipeline.llm import substitute_names_mapped

ROSTER = ["김민경", "박 재경"]

_MARK = re.compile(r"\[사람\d+\]")


def _put_back(text: str, surface: dict[str, str]) -> str:
    """What a caller does with the map: each placeholder back as its form."""
    return _MARK.sub(lambda m: surface.get(m.group(0), m.group(0)), text)


# --- the cases the agent copy's tests cover ---------------------------------------


def test_every_form_of_a_name_becomes_the_same_placeholder() -> None:
    texts = ["민경님 말로는 김민경이 정했대요", "재경님도 박재경도 몰라요"]

    sent, surface = tools.hide_names(texts, ROSTER)

    assert sent == ["[사람1]님 말로는 [사람1]이 정했대요", "[사람2]님도 [사람2]도 몰라요"]
    assert surface == {"[사람1]": "민경", "[사람2]": "재경"}


def test_no_roster_changes_nothing() -> None:
    assert tools.hide_names(["민경님"], []) == (["민경님"], {})


def test_a_question_goes_out_with_placeholders_and_comes_back_with_names() -> None:
    (sent,), surface = tools.hide_names(["민경님이 배포일 정했죠?"], ROSTER)

    assert "민경" not in sent
    assert "[사람1]" in sent
    assert _put_back("[사람1]님이 정한 배포일은?", surface) == "민경님이 정한 배포일은?"


def test_a_search_query_goes_out_with_placeholders() -> None:
    (sent,), surface = tools.hide_names(["김민경 발표 자료"], ROSTER)

    assert sent == "[사람1] 발표 자료"
    assert _put_back("[사람1]에 대한 결과는 없습니다", surface) == "김민경에 대한 결과는 없습니다"


def test_people_are_numbered_by_first_appearance_across_every_text() -> None:
    # The live model sends "already researched" first, so 민경 is [사람1].
    sent, surface = tools.hide_names(["민경님 일정", "재경님이 말한 가격 얼마였죠?"], ROSTER)

    assert all("재경" not in s and "민경" not in s for s in sent)
    assert sent[1] == "[사람2]님이 말한 가격 얼마였죠?"
    assert _put_back("[사람2]님이 말한 가격은?", surface) == "재경님이 말한 가격은?"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[Meeting.__table__, User.__table__, TeamMember.__table__, Participant.__table__],
    )
    with Session(engine) as s:
        s.add(Meeting(id="mtg_1", team_id="team_1", title="주간 회의"))
        for uid, name, team in (
            ("user_a", "팀원", "team_1"),
            ("user_x", "외부", "team_2"),
        ):
            s.add(User(id=uid, email=f"{uid}@example.com", display_name=name))
            s.add(TeamMember(team_id=team, user_id=uid))
        s.flush()
        yield s


def test_the_roster_is_the_team_and_whoever_was_in_the_meeting(session: Session) -> None:
    session.add(User(id="user_left", email="left@example.com", display_name="떠난사람"))
    session.flush()
    session.add(Participant(meeting_id="mtg_1", speaker_label="화자 1", user_id="user_left"))
    session.flush()

    names = tools.names_not_sent(session, "mtg_1")

    assert set(names) == {"팀원", "떠난사람"}
    assert session.query(TeamMember).filter_by(user_id="user_left").count() == 0


# --- that it is B's own matching ----------------------------------------------------


def test_it_is_exactly_what_b_sends_its_own_model() -> None:
    roster = ["김민경", "박 재경", "재경 이", "Kim Min", "a", "  이   승환 "]
    texts = ["김민경, 민경, 재경박, 이재경, Kim Min, 승환이승환", "a b 재경님"]

    assert tools.hide_names(texts, roster) == substitute_names_mapped(texts, roster)


def test_the_roster_is_b_s_own(session: Session) -> None:
    assert tools.names_not_sent(session, "mtg_1") == service.team_roster(session, "mtg_1")
    assert tools.names_not_sent(session, "mtg_missing") == []


def test_texts_may_be_any_sequence_and_are_not_changed() -> None:
    texts = ("김민경 님",)

    sent, _ = tools.hide_names(texts, ROSTER)

    assert sent == ["[사람1] 님"]
    assert texts == ("김민경 님",)


def test_neither_is_offered_to_a_model() -> None:
    offered = {fn.__name__ for fn in [*tools.TOOLS, *tools.ACTIONS]}

    assert "hide_names" not in offered
    assert "names_not_sent" not in offered
    assert list(inspect.signature(tools.hide_names).parameters) == ["texts", "roster"]
