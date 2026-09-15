"""Module E read API: router + service + database against real PostgreSQL.

The read surface is deliberately thin — every route parses a path parameter,
calls one ``service`` function, and returns an explicit response model. These
tests exercise that path end to end, including the shared ``AutuneError`` -> JSON
mapping that apps/api installs in production.

``alignment`` and ``report`` rows are not produced yet (that is P1/P2 work), so
``/heatmap`` and ``/reports`` are covered here for their empty and populated
shapes by inserting rows directly.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_core import AutuneError, get_session
from autune_intelligence.models import IntelAlignment, IntelGapPattern, IntelReport, IntelScore
from autune_intelligence.router import router


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    """The intelligence router on a bare app, wired the way apps/api wires it.

    apps/api owns router registration and the error handler; this rebuilds just
    enough of it to test module E's surface without importing the app package.
    """
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix="/api/intelligence")
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)


def _score(session: Session, meeting_id: str, team_id: str, **kw: object) -> IntelScore:
    row = IntelScore(
        meeting_id=meeting_id,
        team_id=team_id,
        grade=kw.pop("grade", "B"),
        value=kw.pop("value", 0.72),
        **kw,
    )
    session.add(row)
    session.flush()
    return row


# --- /scores/{meeting_id} ---------------------------------------------------


def test_get_score_returns_the_persisted_score(
    client: TestClient, db_session: Session, meeting: str, team: str
) -> None:
    _score(
        db_session,
        meeting,
        team,
        grade="A",
        value=0.91,
        decision_density=0.8,
        gap_count=2,
        action_item_completion_rate=0.5,
        participation_balance=0.7,
        missing_sources=["context"],
    )

    body = client.get(f"/api/intelligence/scores/{meeting}").json()

    assert body == {
        "meeting_id": meeting,
        "team_id": team,
        "grade": "A",
        "value": 0.91,
        "decision_density": 0.8,
        "gap_count": 2,
        "action_item_completion_rate": 0.5,
        "participation_balance": 0.7,
        "missing_sources": ["context"],
    }


def test_get_score_is_404_when_the_meeting_has_no_score(client: TestClient, meeting: str) -> None:
    response = client.get(f"/api/intelligence/scores/{meeting}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# --- /heatmap/{team_id} ---------------------------------------------------


def test_heatmap_is_empty_when_no_alignment_has_been_computed(
    client: TestClient, team: str
) -> None:
    assert client.get(f"/api/intelligence/heatmap/{team}").json() == []


def test_heatmap_averages_the_role_pair_score_across_the_team(
    client: TestClient, db_session: Session, team: str
) -> None:
    from autune_core import Meeting

    for i, score in enumerate((0.4, 0.8)):
        m = Meeting(team_id=team, title=f"m{i}")
        db_session.add(m)
        db_session.flush()
        db_session.add(
            IntelAlignment(meeting_id=m.id, role_a="Dev", role_b="PM", team_id=team, score=score)
        )
    db_session.flush()

    body = client.get(f"/api/intelligence/heatmap/{team}").json()

    assert body == [
        {"role_a": "Dev", "role_b": "PM", "score": pytest.approx(0.6), "meeting_count": 2}
    ]


# --- /reports/{team_id} ---------------------------------------------------


def test_reports_is_empty_when_none_generated(client: TestClient, team: str) -> None:
    assert client.get(f"/api/intelligence/reports/{team}").json() == []


def test_reports_lists_generated_reports_newest_period_first(
    client: TestClient, db_session: Session, team: str
) -> None:
    weeks = ((date(2026, 8, 31), date(2026, 9, 6)), (date(2026, 9, 7), date(2026, 9, 13)))
    for start, end in weeks:
        db_session.add(
            IntelReport(
                team_id=team,
                period_start=start,
                period_end=end,
                body_markdown=f"# week of {start}",
                metrics_json={"meetings": 3},
                source_meeting_ids=["mtg_1"],
            )
        )
    db_session.flush()

    body = client.get(f"/api/intelligence/reports/{team}").json()

    assert [r["period_start"] for r in body] == ["2026-09-07", "2026-08-31"]
    assert body[0]["body_markdown"] == "# week of 2026-09-07"
    assert body[0]["metrics_json"] == {"meetings": 3}


# --- /dashboard/{team_id} ---------------------------------------------------


def test_dashboard_is_empty_for_a_team_with_no_scored_meetings(
    client: TestClient, team: str
) -> None:
    assert client.get(f"/api/intelligence/dashboard/{team}").json() == {
        "team_id": team,
        "meeting_count": 0,
        "average_score": None,
        "action_item_completion_rate": None,
        "recent_scores": [],
        "gap_distribution": {},
    }


def test_dashboard_rolls_up_scores_and_gap_patterns_for_the_team(
    client: TestClient, db_session: Session, team: str
) -> None:
    from autune_core import Meeting

    for i, (grade, value, rate, created) in enumerate(
        (
            ("C", 0.6, 0.4, datetime(2026, 9, 1, tzinfo=UTC)),
            ("A", 0.9, None, datetime(2026, 9, 8, tzinfo=UTC)),
        )
    ):
        m = Meeting(team_id=team, title=f"m{i}")
        db_session.add(m)
        db_session.flush()
        _score(
            db_session,
            m.id,
            team,
            grade=grade,
            value=value,
            action_item_completion_rate=rate,
            created_at=created,
        )
        db_session.add(
            IntelGapPattern(meeting_id=m.id, pattern_type="ownership", team_id=team, count=i + 1)
        )
    db_session.flush()

    body = client.get(f"/api/intelligence/dashboard/{team}").json()

    assert body["meeting_count"] == 2
    assert body["average_score"] == pytest.approx(0.75)
    assert body["action_item_completion_rate"] == pytest.approx(0.4)
    assert [s["grade"] for s in body["recent_scores"]] == ["A", "C"]
    assert body["gap_distribution"] == {"ownership": 3}


def test_dashboard_recent_scores_carry_created_at_for_weekly_bucketing(
    client: TestClient, db_session: Session, team: str
) -> None:
    from autune_core import Meeting

    m = Meeting(team_id=team, title="m0")
    db_session.add(m)
    db_session.flush()
    _score(db_session, m.id, team, created_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
    db_session.flush()

    body = client.get(f"/api/intelligence/dashboard/{team}").json()

    assert body["recent_scores"][0]["created_at"] == "2026-09-01T12:00:00Z"
