"""Module E as agent tools (docs/architecture/agent-layer.md section 4).

Each tool returns a plain dict in the ``ToolResult`` shape; ``autune_agent``
validates it when it collects the tools, and this module may not import it
(ADR 0010), so the shape is checked here by key.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from autune_intelligence import tools
from autune_intelligence.models import IntelCompletion, IntelGapPattern, IntelPrediction, IntelScore

RESULT_KEYS = {"ok", "reason", "summary", "items", "evidence", "confidence", "truncated"}


def _assert_shape(result: dict[str, Any]) -> None:
    assert set(result) == RESULT_KEYS
    assert len(result["items"]) <= tools.MAX_ITEMS
    assert result["evidence"] == []  # E holds no utterance ids
    for item in result["items"]:
        assert {"title", "body", "score"} <= set(item)


def _new_meeting(db_session: Session, team: str, **kw: Any) -> str:
    from autune_core import Meeting

    row = Meeting(team_id=team, title="m", **kw)
    db_session.add(row)
    db_session.flush()
    return row.id


def _score(db_session: Session, meeting_id: str, team: str, **kw: Any) -> None:
    db_session.add(
        IntelScore(
            meeting_id=meeting_id,
            team_id=team,
            grade=kw.pop("grade", "B"),
            value=kw.pop("value", 0.82),
            **kw,
        )
    )
    db_session.flush()


def test_the_tool_list_is_exactly_the_seven_reads() -> None:
    """No speaking-ratio read, ever: a report goes to many people (invariant 11)."""
    assert [fn.__name__ for fn in tools.TOOLS] == [
        "meeting_quality",
        "team_trend",
        "recurring_gaps",
        "misalignment_risk",
        "meeting_report_draft",  # the approval card's preview (#571)
        "meeting_report_awaiting_approval",  # a person's edit, proposed again (#674)
        "meeting_report_correction",  # the approval card's preview of a correction (#674)
    ]
    for fn in tools.TOOLS:
        assert fn.__doc__ and fn.__doc__.strip().startswith("Use this")


# --- meeting_quality ----------------------------------------------------------


def test_meeting_quality_is_not_ok_for_a_meeting_without_a_score(
    db_session: Session, meeting: str
) -> None:
    result = tools.meeting_quality(db_session, meeting)

    _assert_shape(result)
    assert result["ok"] is False
    assert result["reason"]
    assert result["confidence"] == 0.0


def test_meeting_quality_ranks_the_weakest_component_first(
    db_session: Session, team: str, meeting: str
) -> None:
    _score(
        db_session,
        meeting,
        team,
        grade="C",
        value=0.71,
        decision_density=0.9,
        gap_count=1,  # one of HIGH_GAP_CEILING -> burden 0.8
        action_item_completion_rate=0.4,
        participation_balance=None,
    )

    result = tools.meeting_quality(db_session, meeting)

    _assert_shape(result)
    assert result["ok"] is True
    assert "C" in result["summary"]
    titles = [i["title"] for i in result["items"]]
    # Measured components weakest first; the unmeasured one last, never scored as zero.
    assert titles == ["액션아이템 확정률", "고위험 갭", "결정 밀도", "참여 균형"]
    assert result["items"][-1]["body"] == "측정 안 됨"
    assert result["items"][1]["body"] == "1건"
    assert result["confidence"] == 1.0


def test_meeting_quality_lowers_confidence_when_a_source_was_missing(
    db_session: Session, team: str, meeting: str
) -> None:
    _score(db_session, meeting, team, missing_sources=["extraction"])

    result = tools.meeting_quality(db_session, meeting)

    assert result["ok"] is True
    assert "extraction" in result["summary"]
    assert result["confidence"] < 1.0


# --- team_trend ---------------------------------------------------------------


def test_team_trend_is_empty_but_ok_for_a_team_with_no_scores(
    db_session: Session, team: str
) -> None:
    result = tools.team_trend(db_session, team)

    _assert_shape(result)
    assert result["ok"] is True
    assert result["items"] == []


def test_team_trend_keeps_the_five_newest_meetings_and_says_it_cut(
    db_session: Session, team: str
) -> None:
    now = datetime.now(UTC)
    ids = []
    for days_ago in range(7):
        m = _new_meeting(db_session, team)
        _score(db_session, m, team, created_at=now - timedelta(days=days_ago))
        ids.append(m)

    result = tools.team_trend(db_session, team)

    _assert_shape(result)
    assert [i["meeting_id"] for i in result["items"]] == ids[:5]
    assert result["truncated"] is True
    assert "7" in result["summary"]


def test_team_trend_names_both_rates_and_keeps_them_apart(db_session: Session, team: str) -> None:
    """확정률 is the quality score's; 완료율 is B's latest counts (#605)."""
    from autune_contracts import TeamActionProgress
    from autune_intelligence import service

    m = _new_meeting(db_session, team)
    _score(db_session, m, team, action_item_completion_rate=0.9)
    others = [_new_meeting(db_session, team) for _ in range(2)]
    service.store_action_progress(
        db_session,
        TeamActionProgress(
            team_id=team,
            as_of=datetime.now(UTC),
            meetings=[
                {"meeting_id": m, "confirmed": 4, "done": 1, "overdue": 2},
                *({"meeting_id": o, "confirmed": 4, "done": 1, "overdue": 0} for o in others),
            ],
        ),
    )

    summary = tools.team_trend(db_session, team)["summary"]

    assert "확정률 90%" in summary
    assert "완료율 25%" in summary
    assert "기한 지난 항목 2건" in summary


def test_team_trend_names_completion_before_any_meeting_is_scored(
    db_session: Session, team: str
) -> None:
    """B's counts can be current while E has scored nothing (#800 review)."""
    from autune_contracts import TeamActionProgress
    from autune_intelligence import service

    meetings = [_new_meeting(db_session, team) for _ in range(3)]
    service.store_action_progress(
        db_session,
        TeamActionProgress(
            team_id=team,
            as_of=datetime.now(UTC),
            meetings=[{"meeting_id": m, "confirmed": 2, "done": 1, "overdue": 0} for m in meetings],
        ),
    )

    result = tools.team_trend(db_session, team)

    assert result["items"] == []
    assert "완료율 50%" in result["summary"]


def test_team_trend_says_nothing_of_completion_it_does_not_know(
    db_session: Session, team: str
) -> None:
    m = _new_meeting(db_session, team)
    _score(db_session, m, team, action_item_completion_rate=0.9)

    assert "완료율" not in tools.team_trend(db_session, team)["summary"]


# --- recurring_gaps -----------------------------------------------------------


def test_recurring_gaps_orders_patterns_by_count_with_example_titles(
    db_session: Session, team: str
) -> None:
    m = _new_meeting(db_session, team)
    db_session.add(
        IntelCompletion(
            meeting_id=m,
            first_seen_at=datetime.now(UTC),
            gap_payload={
                "gaps": [{"id": "gap_1", "title": "예산 담당자 미정", "severity": "high"}]
            },
        )
    )
    for pattern, count, gap_ids in (("schedule", 1, []), ("budget", 4, ["gap_1"])):
        db_session.add(
            IntelGapPattern(
                meeting_id=m,
                pattern_type=pattern,
                team_id=team,
                count=count,
                source_gap_ids=gap_ids,
                classifier_version="fake",
            )
        )
    db_session.flush()

    result = tools.recurring_gaps(db_session, team)

    _assert_shape(result)
    assert [i["title"] for i in result["items"]] == ["budget", "schedule"]
    assert "예산 담당자 미정" in result["items"][0]["body"]
    assert result["items"][0]["score"] == 4


def test_recurring_gaps_is_empty_but_ok_without_patterns(db_session: Session, team: str) -> None:
    result = tools.recurring_gaps(db_session, team)

    _assert_shape(result)
    assert result["ok"] is True
    assert result["items"] == []


# --- misalignment_risk --------------------------------------------------------


def _predicted(db_session: Session, team: str, probability: float, scored_at: datetime) -> None:
    m = _new_meeting(db_session, team)
    _score(db_session, m, team, created_at=scored_at)
    db_session.add(
        IntelPrediction(
            meeting_id=m,
            kind="misalignment_risk",
            horizon_days=14,
            team_id=team,
            probability=probability,
            model_version="heuristic-v1",
        )
    )
    db_session.flush()


def test_misalignment_risk_withholds_the_probability_before_the_history_gate(
    db_session: Session, team: str
) -> None:
    for _ in range(5):
        _predicted(db_session, team, 0.37, datetime.now(UTC))

    result = tools.misalignment_risk(db_session, team)

    _assert_shape(result)
    assert result["ok"] is False
    assert result["reason"] == "insufficient_history"
    assert "37" not in str(result)


def test_misalignment_risk_returns_the_latest_prediction_once_the_gate_clears(
    db_session: Session, team: str
) -> None:
    old = datetime.now(UTC) - timedelta(weeks=5)
    for _ in range(5):
        _predicted(db_session, team, 0.62, old)

    result = tools.misalignment_risk(db_session, team)

    _assert_shape(result)
    assert result["ok"] is True
    assert result["items"][0]["score"] == pytest.approx(0.62)
    assert "62%" in result["summary"]
