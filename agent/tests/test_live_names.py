"""Roster names leave as ``[사람N]`` and come back as the name (#1162 review, B's #411)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from autune_agent.live.model import GeminiLive, Row
from autune_agent.live.names import NamedText, roster, substitute
from autune_agent.main.gemini import WebAnswer
from autune_core import Participant, TeamMember, User


class Scripted:
    """Stands in for GeminiText: answers in order, keeps what was sent."""

    def __init__(self, *answers: str, web: WebAnswer | None = None) -> None:
        self.answers = list(answers)
        self.sent: list[dict[str, Any]] = []
        self._web = web or WebAnswer(text="", sources=[])

    def generate(self, instructions: str, text: str, *, json_answer: bool) -> str:
        self.sent.append({"text": text})
        return self.answers.pop(0)

    def search(self, instructions: str, question: str) -> WebAnswer:
        self.sent.append({"text": question})
        return self._web


ROSTER = ["김민경", "박 재경"]


def test_every_form_of_a_name_becomes_the_same_placeholder() -> None:
    texts = ["민경님 말로는 김민경이 정했대요", "재경님도 박재경도 몰라요"]

    sent, surface = substitute(texts, ROSTER)

    assert sent == ["[사람1]님 말로는 [사람1]이 정했대요", "[사람2]님도 [사람2]도 몰라요"]
    assert surface == {"[사람1]": "민경", "[사람2]": "재경"}


def test_no_roster_changes_nothing() -> None:
    assert substitute(["민경님"], []) == (["민경님"], {})


def test_generate_sends_placeholders_and_answers_with_names() -> None:
    inner = Scripted('{"questions": [{"q": "[사람1]님이 정한 배포일은?", "web": false}]}')

    answer = NamedText(inner, ROSTER).generate("지시", "민경님이 배포일 정했죠?", json_answer=True)

    assert "민경" not in inner.sent[0]["text"]
    assert "[사람1]" in inner.sent[0]["text"]
    assert answer == '{"questions": [{"q": "민경님이 정한 배포일은?", "web": false}]}'


def test_search_sends_placeholders_and_answers_with_names() -> None:
    inner = Scripted(web=WebAnswer(text="[사람1]에 대한 결과는 없습니다", sources=[("t", "u")]))

    answer = NamedText(inner, ROSTER).search("지시", "김민경 발표 자료")

    assert inner.sent[0]["text"] == "[사람1] 발표 자료"
    assert answer == WebAnswer(text="김민경에 대한 결과는 없습니다", sources=[("t", "u")])


def test_the_live_model_never_sends_a_roster_name() -> None:
    # "Already researched" is sent first, so 민경 is [사람1] and 재경 [사람2].
    inner = Scripted('{"questions": [{"q": "[사람2]님이 말한 가격은?", "web": true}]}')
    model = GeminiLive(inner, roster=ROSTER)

    rows = [Row(start=1.0, text="재경님이 말한 가격 얼마였죠?")]

    found = model.detect(rows, known=["민경님 일정"])

    assert all("재경" not in s["text"] and "민경" not in s["text"] for s in inner.sent)
    assert found[0].question == "재경님이 말한 가격은?"


def test_the_roster_is_the_team_and_whoever_was_in_the_meeting(
    session: Session, team: dict[str, str]
) -> None:
    left = User(email="left@example.com", display_name="떠난사람")
    session.add(left)
    session.flush()
    session.add(Participant(meeting_id=team["meeting"], speaker_label="화자 1", user_id=left.id))
    session.commit()

    names = roster(session, team["meeting"])

    assert set(names) == {"팀원", "떠난사람"}
    assert session.query(TeamMember).filter_by(user_id=left.id).count() == 0
