"""Module E read API: router + service + database against real PostgreSQL.

The read surface is deliberately thin — every route parses a path parameter,
calls one ``service`` function, and returns an explicit response model. These
tests exercise that path end to end, including the shared ``AutuneError`` -> JSON
mapping that apps/api installs in production.

``/heatmap``, ``/predictions`` and ``/reports`` are covered here for their
empty and populated shapes by inserting rows directly; the aggregation that
writes those rows is covered in test_aggregate.py and test_report.py.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_core import AutuneError, TeamMember, User, get_session
from autune_core.auth import current_user
from autune_intelligence.models import (
    IntelAlignment,
    IntelCompletion,
    IntelGapPattern,
    IntelPrediction,
    IntelReport,
    IntelScore,
)
from autune_intelligence.router import router


def _app(db_session: Session, user: User | None) -> TestClient:
    """The intelligence router on a bare app, wired the way apps/api wires it.

    apps/api owns router registration and the error handler; this rebuilds just
    enough of it to test module E's surface without importing the app package.
    ``user`` is who the token says is asking; ``None`` sends no token.
    """
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix="/api/intelligence")
    app.dependency_overrides[get_session] = lambda: db_session
    if user is not None:
        app.dependency_overrides[current_user] = lambda: user
    return TestClient(app)


def _user(db_session: Session, team: str | None) -> User:
    user = User(email=f"reader-{uuid.uuid4().hex}@example.com", display_name="읽는 사람")
    db_session.add(user)
    db_session.flush()
    if team is not None:
        db_session.add(TeamMember(team_id=team, user_id=user.id))
        db_session.flush()
    return user


@pytest.fixture
def client(db_session: Session, team: str) -> Iterator[TestClient]:
    """Asked by a member of ``team``."""
    yield _app(db_session, _user(db_session, team))


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


def _seed_alignment(db_session: Session, team: str, role_b: str, scores: tuple[float, ...]) -> None:
    from autune_core import Meeting

    for i, score in enumerate(scores):
        m = Meeting(team_id=team, title=f"{role_b}{i}")
        db_session.add(m)
        db_session.flush()
        db_session.add(
            IntelAlignment(meeting_id=m.id, role_a="Dev", role_b=role_b, team_id=team, score=score)
        )
    db_session.flush()


def test_heatmap_averages_the_role_pair_score_across_the_team(
    client: TestClient, db_session: Session, team: str
) -> None:
    _seed_alignment(db_session, team, "PM", (0.4, 0.8, 0.6))

    body = client.get(f"/api/intelligence/heatmap/{team}").json()

    assert body == [
        {"role_a": "Dev", "role_b": "PM", "score": pytest.approx(0.6), "meeting_count": 3}
    ]


def test_heatmap_leaves_out_a_pair_scored_in_too_few_meetings(
    client: TestClient, db_session: Session, team: str
) -> None:
    """privacy.md section 3: a small sample leaves the cell empty."""
    _seed_alignment(db_session, team, "PM", (0.4, 0.8, 0.6))
    _seed_alignment(db_session, team, "Design", (0.9, 0.1))

    body = client.get(f"/api/intelligence/heatmap/{team}").json()

    assert [(c["role_a"], c["role_b"]) for c in body] == [("Dev", "PM")]


# --- /predictions/{team_id} -----------------------------------------------


def _predicted_meeting(
    db_session: Session,
    team: str,
    probability: float,
    scored_at: datetime,
    started_at: datetime | None = None,
) -> str:
    from autune_core import Meeting

    m = Meeting(team_id=team, title="p", started_at=started_at)
    db_session.add(m)
    db_session.flush()
    _score(db_session, m.id, team, created_at=scored_at)
    db_session.add(
        IntelPrediction(
            meeting_id=m.id,
            kind="misalignment_risk",
            horizon_days=14,
            team_id=team,
            probability=probability,
            model_version="heuristic-v1",
        )
    )
    db_session.flush()
    return m.id


def test_predictions_are_withheld_below_the_meeting_floor(
    client: TestClient, db_session: Session, team: str
) -> None:
    old = datetime.now(UTC) - timedelta(weeks=10)
    for _ in range(2):  # history enough, meetings not
        _predicted_meeting(db_session, team, 0.4, old)

    body = client.get(f"/api/intelligence/predictions/{team}").json()

    assert body == {"team_id": team, "prediction": None, "reason": "insufficient_history"}


def test_predictions_return_the_latest_once_the_gate_clears(
    client: TestClient, db_session: Session, team: str
) -> None:
    old = datetime.now(UTC) - timedelta(weeks=5)
    for _ in range(3):
        _predicted_meeting(db_session, team, 0.2, old)

    body = client.get(f"/api/intelligence/predictions/{team}").json()

    assert body["reason"] is None
    assert body["prediction"]["kind"] == "misalignment_risk"
    assert body["prediction"]["horizon_days"] == 14
    assert body["prediction"]["probability"] == pytest.approx(0.2)


def test_three_meetings_this_week_show_a_prediction_until_the_presentation(
    client: TestClient, db_session: Session, team: str
) -> None:
    """#27's four weeks are lifted until the final presentation; restore with them."""
    now = datetime.now(UTC)
    for _ in range(3):
        _predicted_meeting(db_session, team, 0.3, now)

    body = client.get(f"/api/intelligence/predictions/{team}").json()

    assert body["reason"] is None
    assert body["prediction"]["probability"] == pytest.approx(0.3)


def test_the_latest_prediction_is_the_newest_meeting_not_the_last_written(
    client: TestClient, db_session: Session, team: str
) -> None:
    """A re-aggregated old meeting must not become the team's prediction.

    ``updated_at`` is bumped every time ``aggregate_meeting`` upserts, which a
    late source does to meetings long past (``tasks.py``'s reopen path). Ordering
    by it surfaced a prediction whose 14-day horizon had closed weeks earlier.
    """
    now = datetime.now(UTC)
    old_at, recent_at = now - timedelta(days=40), now - timedelta(days=1)
    old = _predicted_meeting(db_session, team, 0.57, old_at, started_at=old_at)
    _predicted_meeting(
        db_session, team, 0.33, now - timedelta(days=20), started_at=now - timedelta(days=20)
    )
    _predicted_meeting(db_session, team, 0.10, recent_at, started_at=recent_at)
    # The old meeting is re-aggregated last. In one transaction ``func.now()``
    # is fixed, so the write order is set on the column directly.
    db_session.execute(
        sa.update(IntelPrediction).where(IntelPrediction.meeting_id == old).values(updated_at=now)
    )
    db_session.flush()

    body = client.get(f"/api/intelligence/predictions/{team}").json()

    assert body["reason"] is None
    assert body["prediction"]["probability"] == pytest.approx(0.10)


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
        "average_grade": None,
        "action_item_completion_rate": None,
        "action_completion_meeting_count": None,
        "overdue_action_items": None,
        "action_progress_as_of": None,
        "action_item_confirmation_rate": None,
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
            IntelGapPattern(
                meeting_id=m.id,
                pattern_type="ownership",
                team_id=team,
                count=i + 1,
                classifier_version="fake",
            )
        )
    db_session.flush()

    body = client.get(f"/api/intelligence/dashboard/{team}").json()

    assert body["meeting_count"] == 2
    assert body["average_score"] == pytest.approx(0.75)
    assert body["average_grade"] == "C"
    assert body["action_item_confirmation_rate"] == pytest.approx(0.4)
    assert body["action_item_completion_rate"] is None  # no counts from B yet
    assert [s["grade"] for s in body["recent_scores"]] == ["A", "C"]
    assert body["gap_distribution"] == {"ownership": 3}


def test_dashboard_excludes_gap_patterns_without_a_classifier_version(
    client: TestClient, db_session: Session, team: str
) -> None:
    """Pre-#203 rows carry the raw category text in ``pattern_type`` and an
    empty ``classifier_version`` — mixing them into the same distribution as
    real classifier output means one graph shows two incompatible vocabularies
    at once. Deferred in PR #210's review pending #203; #203 is on ``main`` now.
    """
    from autune_core import Meeting

    m = Meeting(team_id=team, title="legacy")
    db_session.add(m)
    db_session.flush()
    db_session.add(
        IntelGapPattern(
            meeting_id=m.id,
            pattern_type="technical_spec",
            team_id=team,
            count=5,
            classifier_version="",
        )
    )
    db_session.add(
        IntelGapPattern(
            meeting_id=m.id,
            pattern_type="ownership",
            team_id=team,
            count=2,
            classifier_version="fake",
        )
    )
    db_session.flush()

    body = client.get(f"/api/intelligence/dashboard/{team}").json()

    assert body["gap_distribution"] == {"ownership": 2}


def test_dashboard_average_grade_is_none_without_any_scores(client: TestClient, team: str) -> None:
    assert client.get(f"/api/intelligence/dashboard/{team}").json()["average_grade"] is None


def test_dashboard_recent_scores_excludes_meetings_older_than_the_trend_window(
    client: TestClient, db_session: Session, team: str
) -> None:
    from autune_core import Meeting

    old = Meeting(team_id=team, title="old")
    recent = Meeting(team_id=team, title="recent")
    db_session.add_all([old, recent])
    db_session.flush()
    now = datetime.now(UTC)
    _score(db_session, old.id, team, value=0.5, created_at=now - timedelta(weeks=9))
    _score(db_session, recent.id, team, value=0.9, created_at=now - timedelta(weeks=1))
    db_session.flush()

    body = client.get(f"/api/intelligence/dashboard/{team}").json()

    assert body["meeting_count"] == 2
    assert body["average_score"] == pytest.approx(0.7)
    assert [s["meeting_id"] for s in body["recent_scores"]] == [recent.id]


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


# --- /gap-titles/{team_id} ---------------------------------------------------


def test_gap_titles_is_empty_for_a_team_with_no_gap_patterns(client: TestClient, team: str) -> None:
    assert client.get(f"/api/intelligence/gap-titles/{team}").json() == {}


def test_gap_titles_matches_source_gap_ids_against_the_meeting_gap_payload(
    client: TestClient, db_session: Session, team: str
) -> None:
    from autune_core import Meeting

    m = Meeting(team_id=team, title="m")
    db_session.add(m)
    db_session.flush()
    db_session.add(
        IntelCompletion(
            meeting_id=m.id,
            first_seen_at=datetime.now(UTC),
            gap_payload={
                "gaps": [
                    {"id": "gap_1", "title": "예산 담당자 미정", "severity": "high"},
                    {"id": "gap_2", "title": "일정 재확인 필요", "severity": "high"},
                ]
            },
        )
    )
    db_session.add(
        IntelGapPattern(
            meeting_id=m.id,
            pattern_type="budget",
            team_id=team,
            count=1,
            source_gap_ids=["gap_1"],
        )
    )
    db_session.add(
        IntelGapPattern(
            meeting_id=m.id,
            pattern_type="schedule",
            team_id=team,
            count=1,
            source_gap_ids=["gap_2"],
        )
    )
    db_session.flush()

    body = client.get(f"/api/intelligence/gap-titles/{team}").json()

    assert body == {
        "budget": ["예산 담당자 미정"],
        "schedule": ["일정 재확인 필요"],
    }


def test_gap_titles_skips_a_meeting_whose_gap_payload_never_arrived(
    client: TestClient, db_session: Session, team: str
) -> None:
    """A pattern row can outlive its completion row's payload (retention,
    re-aggregation) — the id simply finds no title rather than erroring."""
    from autune_core import Meeting

    m = Meeting(team_id=team, title="m")
    db_session.add(m)
    db_session.flush()
    db_session.add(
        IntelGapPattern(
            meeting_id=m.id,
            pattern_type="risk",
            team_id=team,
            count=1,
            source_gap_ids=["gap_missing"],
        )
    )
    db_session.flush()

    body = client.get(f"/api/intelligence/gap-titles/{team}").json()

    assert body == {}


def test_gap_titles_excludes_gaps_below_high_severity(
    client: TestClient, db_session: Session, team: str
) -> None:
    """`docs/architecture/contracts.md`: only `high` is surfaced in the UI by
    default. `source_gap_ids` carries every severity (it feeds the count, not
    the UI), so the title lookup has to filter on its own."""
    from autune_core import Meeting

    m = Meeting(team_id=team, title="m")
    db_session.add(m)
    db_session.flush()
    db_session.add(
        IntelCompletion(
            meeting_id=m.id,
            first_seen_at=datetime.now(UTC),
            gap_payload={
                "gaps": [
                    {"id": "gap_high", "title": "예산 담당자 미정", "severity": "high"},
                    {"id": "gap_medium", "title": "회의실 예약 미정", "severity": "medium"},
                    {"id": "gap_low", "title": "다과 준비 여부", "severity": "low"},
                ]
            },
        )
    )
    db_session.add(
        IntelGapPattern(
            meeting_id=m.id,
            pattern_type="budget",
            team_id=team,
            count=3,
            source_gap_ids=["gap_high", "gap_medium", "gap_low"],
        )
    )
    db_session.flush()

    body = client.get(f"/api/intelligence/gap-titles/{team}").json()

    assert body == {"budget": ["예산 담당자 미정"]}


def test_gap_titles_skips_a_gap_missing_id_or_title(
    client: TestClient, db_session: Session, team: str
) -> None:
    """A malformed payload entry is skipped, matching the docstring's
    "skipped rather than raising" promise — not a 500."""
    from autune_core import Meeting

    m = Meeting(team_id=team, title="m")
    db_session.add(m)
    db_session.flush()
    db_session.add(
        IntelCompletion(
            meeting_id=m.id,
            first_seen_at=datetime.now(UTC),
            gap_payload={
                "gaps": [
                    {"id": "gap_ok", "title": "예산 담당자 미정", "severity": "high"},
                    {"id": "gap_no_title", "severity": "high"},
                    {"title": "id 없는 갭", "severity": "high"},
                ]
            },
        )
    )
    db_session.add(
        IntelGapPattern(
            meeting_id=m.id,
            pattern_type="budget",
            team_id=team,
            count=3,
            source_gap_ids=["gap_ok", "gap_no_title"],
        )
    )
    db_session.flush()

    body = client.get(f"/api/intelligence/gap-titles/{team}").json()

    assert body == {"budget": ["예산 담당자 미정"]}


def test_gap_titles_deduplicates_repeated_titles(
    client: TestClient, db_session: Session, team: str
) -> None:
    from autune_core import Meeting

    m = Meeting(team_id=team, title="m")
    db_session.add(m)
    db_session.flush()
    gaps = [
        {"id": "gap_1", "title": "예산 담당자 미정", "severity": "high"},
        {"id": "gap_2", "title": "예산 담당자 미정", "severity": "high"},
        {"id": "gap_3", "title": "일정 재확인 필요", "severity": "high"},
    ]
    db_session.add(
        IntelCompletion(
            meeting_id=m.id, first_seen_at=datetime.now(UTC), gap_payload={"gaps": gaps}
        )
    )
    db_session.add(
        IntelGapPattern(
            meeting_id=m.id,
            pattern_type="budget",
            team_id=team,
            count=3,
            source_gap_ids=["gap_1", "gap_2", "gap_3"],
        )
    )
    db_session.flush()

    body = client.get(f"/api/intelligence/gap-titles/{team}").json()

    assert body == {"budget": ["예산 담당자 미정", "일정 재확인 필요"]}


def test_gap_titles_caps_the_list_per_pattern(
    client: TestClient, db_session: Session, team: str
) -> None:
    from autune_core import Meeting

    m = Meeting(team_id=team, title="m")
    db_session.add(m)
    db_session.flush()
    gaps = [{"id": f"gap_{i}", "title": f"제목 {i}", "severity": "high"} for i in range(25)]
    db_session.add(
        IntelCompletion(
            meeting_id=m.id, first_seen_at=datetime.now(UTC), gap_payload={"gaps": gaps}
        )
    )
    db_session.add(
        IntelGapPattern(
            meeting_id=m.id,
            pattern_type="budget",
            team_id=team,
            count=25,
            source_gap_ids=[g["id"] for g in gaps],
        )
    )
    db_session.flush()

    body = client.get(f"/api/intelligence/gap-titles/{team}").json()

    assert len(body["budget"]) == 20


@pytest.mark.parametrize("path", ["dashboard", "reports", "gap-titles", "heatmap", "predictions"])
def test_a_teams_numbers_are_for_its_members(db_session: Session, team: str, path: str) -> None:
    """Every team route is members only (#800, #812 reviews): completion counts,
    gap titles -- meeting content -- and the rest. No session and a person from
    another team are both refused -- 403, core's ``PermissionDeniedError``."""
    outsider = _user(db_session, None)

    assert _app(db_session, None).get(f"/api/intelligence/{path}/{team}").status_code == 403
    assert _app(db_session, outsider).get(f"/api/intelligence/{path}/{team}").status_code == 403


def test_a_score_is_for_its_meetings_team(db_session: Session, meeting: str, team: str) -> None:
    """Asked by meeting id, so another team's person gets the same 404 as for a
    meeting with no score: whether the meeting exists does not leak."""
    _score(db_session, meeting, team)
    outsider = _user(db_session, None)

    assert _app(db_session, None).get(f"/api/intelligence/scores/{meeting}").status_code == 403
    response = _app(db_session, outsider).get(f"/api/intelligence/scores/{meeting}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
