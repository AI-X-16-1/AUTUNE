"""Module A as agent tools (docs/architecture/agent-layer.md section 4).

Each tool returns a plain dict in the ``ToolResult`` shape; ``autune_agent``
validates it when it collects the tools, and this module may not import it
(ADR 0010), so the shape is checked here by key. The tests that matter are the
privacy ones: no text from a speaker who did not consent, and nothing that
orders speakers by how much they said.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from autune_audio import tools
from autune_core import Meeting, Participant, User, Utterance

RESULT_KEYS = {"ok", "reason", "summary", "items", "evidence", "confidence", "truncated"}


def _assert_shape(result: dict[str, Any]) -> None:
    assert set(result) == RESULT_KEYS
    assert len(result["items"]) <= tools.MAX_ITEMS
    for item in result["items"]:
        assert {"title", "body", "score"} <= set(item)


def _participant(
    db_session: Session, meeting: str, label: str, *, consented: bool, user: User | None = None
) -> Participant:
    row = Participant(
        meeting_id=meeting,
        speaker_label=label,
        consented=consented,
        user_id=user.id if user else None,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _say(
    db_session: Session, meeting: str, who: Participant | None, start: float, text: str
) -> str:
    row = Utterance(
        meeting_id=meeting,
        participant_id=who.id if who else None,
        speaker_label=who.speaker_label if who else "SPEAKER_99",
        start_sec=start,
        end_sec=start + 1.0,
        text=text,
        confidence=0.9,
    )
    db_session.add(row)
    db_session.flush()
    return row.id


@pytest.fixture
def spoken(db_session: Session, meeting: str) -> dict[str, Any]:
    """Three speakers. The quiet one who consented speaks first and briefly; the
    talkative one did not consent; a third utterance has no participant at all."""
    person = User(email="kim@example.com", display_name="김팀장")
    db_session.add(person)
    db_session.flush()
    quiet = _participant(db_session, meeting, "SPEAKER_01", consented=True)
    talker = _participant(db_session, meeting, "SPEAKER_00", consented=False)
    named = _participant(db_session, meeting, "SPEAKER_02", consented=True, user=person)
    ids = {
        "quiet": _say(db_session, meeting, quiet, 1.0, "배포는 금요일입니다"),
        "talker": _say(db_session, meeting, talker, 5.0, "배포 일정은 제가 다시 보겠습니다"),
        "talker2": _say(db_session, meeting, talker, 9.0, "그리고 예산도 다시 봐야 합니다"),
        "named": _say(db_session, meeting, named, 70.0, "배포 전에 QA를 한 번 더 합니다"),
        "orphan": _say(db_session, meeting, None, 80.0, "배포 누가 하죠"),
    }
    row = db_session.get(Meeting, meeting)
    assert row is not None
    row.status = "complete"
    row.duration_seconds = 1800.0
    db_session.flush()
    return ids


def test_the_tool_list_is_exactly_the_four_reads() -> None:
    assert [fn.__name__ for fn in tools.TOOLS] == [
        "meeting_overview",
        "recent_meetings",
        "find_utterances",
        "quote_utterances",
    ]
    assert tools.PERSONAL_ONLY_TOOLS == []
    for fn in tools.TOOLS:
        assert (fn.__doc__ or "").startswith("Use this")


def test_the_overview_lists_speakers_by_first_word_not_by_how_much(
    db_session: Session, meeting: str, spoken: dict[str, str]
) -> None:
    result = tools.meeting_overview(db_session, meeting)

    _assert_shape(result)
    assert result["ok"] is True
    # SPEAKER_00 said twice as much; SPEAKER_01 spoke first.
    assert [i["title"] for i in result["items"]] == ["SPEAKER_01", "SPEAKER_00", "김팀장"]
    assert "분석 완료" in result["summary"]
    assert "30분" in result["summary"]
    assert "발언 5건" in result["summary"]
    assert "화자 3명(이름 확인 1명)" in result["summary"]
    assert "동의한 화자 2명" in result["summary"]
    assert result["evidence"] == [meeting]


def test_the_overview_carries_no_per_speaker_duration(
    db_session: Session, meeting: str, spoken: dict[str, str]
) -> None:
    result = tools.meeting_overview(db_session, meeting)

    for item in result["items"]:
        assert set(item) == {"title", "body", "score"}
        assert item["score"] == 0.0


def test_search_quotes_only_speakers_who_consented(
    db_session: Session, meeting: str, spoken: dict[str, str]
) -> None:
    result = tools.find_utterances(db_session, meeting, "배포")

    _assert_shape(result)
    assert result["evidence"] == [spoken["quiet"], spoken["named"]]
    assert [i["title"] for i in result["items"]] == ["00:01 SPEAKER_01", "01:10 김팀장"]
    bodies = " ".join(i["body"] for i in result["items"])
    assert "제가 다시 보겠습니다" not in bodies
    assert "누가 하죠" not in bodies
    assert result["summary"] == "일치하는 발언 2건."


def test_search_treats_the_query_as_text_not_a_pattern(
    db_session: Session, meeting: str, spoken: dict[str, str]
) -> None:
    assert tools.find_utterances(db_session, meeting, "%")["items"] == []
    assert tools.find_utterances(db_session, meeting, "qa")["evidence"] == [spoken["named"]]


@pytest.mark.parametrize("query", ["", "   ", "가" * (tools.MAX_QUERY_CHARS + 1)])
def test_a_query_that_is_empty_or_a_paragraph_is_refused(
    db_session: Session, meeting: str, query: str
) -> None:
    result = tools.find_utterances(db_session, meeting, query)

    _assert_shape(result)
    assert result["ok"] is False


def test_more_than_five_matches_says_so(db_session: Session, meeting: str) -> None:
    who = _participant(db_session, meeting, "SPEAKER_00", consented=True)
    for n in range(7):
        _say(db_session, meeting, who, float(n), f"안건 {n}")

    result = tools.find_utterances(db_session, meeting, "안건")

    assert len(result["items"]) == 5
    assert result["truncated"] is True
    assert "7건" in result["summary"]


def test_quote_skips_ids_it_may_not_quote_and_counts_them(
    db_session: Session, meeting: str, spoken: dict[str, str]
) -> None:
    result = tools.quote_utterances(
        db_session, meeting, [spoken["named"], spoken["talker"], spoken["quiet"], "utt_nobody"]
    )

    _assert_shape(result)
    assert result["ok"] is True
    assert result["evidence"] == [spoken["quiet"], spoken["named"]]
    assert "2건은 이 회의에 없거나" in result["summary"]


def test_quote_stays_inside_the_meeting_it_was_asked_about(
    db_session: Session, team: str, meeting: str, spoken: dict[str, str]
) -> None:
    other = Meeting(team_id=team, title="다른 회의")
    db_session.add(other)
    db_session.flush()

    result = tools.quote_utterances(db_session, other.id, [spoken["quiet"]])

    assert result["ok"] is False
    assert result["items"] == []


def test_quote_takes_five_at_a_time(db_session: Session, meeting: str) -> None:
    who = _participant(db_session, meeting, "SPEAKER_00", consented=True)
    ids = [_say(db_session, meeting, who, float(n), f"말 {n}") for n in range(7)]

    result = tools.quote_utterances(db_session, meeting, ids)

    assert result["evidence"] == ids[:5]
    assert result["truncated"] is True


def test_a_long_utterance_is_cut(db_session: Session, meeting: str) -> None:
    who = _participant(db_session, meeting, "SPEAKER_00", consented=True)
    uid = _say(db_session, meeting, who, 0.0, "가" * 500)

    body = tools.quote_utterances(db_session, meeting, [uid])["items"][0]["body"]

    assert len(body) == tools.MAX_BODY_CHARS
    assert body.endswith("…")


@pytest.mark.parametrize(
    "call",
    [
        lambda s: tools.meeting_overview(s, "mtg_nobody"),
        lambda s: tools.find_utterances(s, "mtg_nobody", "배포"),
        lambda s: tools.quote_utterances(s, "mtg_nobody", ["utt_x"]),
    ],
)
def test_an_unknown_meeting_is_a_refusal_not_an_exception(db_session: Session, call: Any) -> None:
    result = call(db_session)

    _assert_shape(result)
    assert result["ok"] is False
    assert result["reason"] == "meeting not found"
    assert "mtg_nobody" not in result["summary"]


def test_recent_meetings_are_the_teams_newest_first_with_what_is_ahead(
    db_session: Session, team: str
) -> None:
    now = datetime.now(UTC)
    old = Meeting(team_id=team, title="석 달 전", started_at=now - timedelta(days=90))
    last = Meeting(team_id=team, title="지난주 리뷰", started_at=now - timedelta(days=7))
    ahead = Meeting(
        team_id=team, title="다음 주 킥오프", started_at=now + timedelta(days=5), status="scheduled"
    )
    db_session.add_all([old, last, ahead])
    db_session.flush()

    result = tools.recent_meetings(db_session, team)

    _assert_shape(result)
    titles = [i["title"] for i in result["items"]]
    assert titles[:2] == ["다음 주 킥오프", "지난주 리뷰"]
    assert "석 달 전" not in titles
    assert result["items"][0]["meeting_id"] == ahead.id
    assert "예정" in result["items"][0]["body"]


def test_recent_meetings_of_a_team_with_none_is_an_answer(db_session: Session) -> None:
    result = tools.recent_meetings(db_session, "team_empty")

    _assert_shape(result)
    assert result["ok"] is True
    assert result["items"] == []
