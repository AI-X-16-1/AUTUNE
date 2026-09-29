"""Labeled history read back from E's own tables, against real PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from autune_contracts import ContextLinks
from autune_core import Meeting
from autune_intelligence import service
from autune_intelligence.eval import evaluate
from autune_intelligence.history import labeled_examples

NOW = datetime.now(UTC)


def _meeting(session: Session, team: str, days_ago: float) -> str:
    m = Meeting(team_id=team, title="m", started_at=NOW - timedelta(days=days_ago))
    session.add(m)
    session.flush()
    return m.id


def _aggregate(session: Session, meeting_id: str, reverses: str | None = None) -> None:
    lineage = (
        [
            {
                "thread_id": "thr_1",
                "source_decision_id": "dec_1",
                "current_statement": "s",
                "previous_statement": "p",
                "previous_meeting_id": reverses,
                "change_type": "reversed",
                "confidence": 0.9,
            }
        ]
        if reverses
        else []
    )
    payload = ContextLinks(meeting_id=meeting_id, decision_lineage=lineage).model_dump(mode="json")
    service.record_completion(session, meeting_id, "context", payload)
    for source in ("extraction", "gap"):
        service.record_completion(session, meeting_id, source, {"meeting_id": meeting_id})
    session.flush()
    service.aggregate_meeting(session, meeting_id)
    session.flush()


def test_a_later_reversal_labels_the_earlier_meeting_and_features_are_rebuilt(
    db_session: Session, team: str
) -> None:
    earlier = _meeting(db_session, team, days_ago=30)
    steady = _meeting(db_session, team, days_ago=28)
    later = _meeting(db_session, team, days_ago=25)
    _aggregate(db_session, earlier)
    _aggregate(db_session, steady)
    _aggregate(db_session, later, reverses=earlier)

    examples = {e.meeting_id: e for e in labeled_examples(db_session, now=NOW)}

    assert examples[earlier].reversed_within_horizon is True
    assert examples[steady].reversed_within_horizon is False
    assert examples[later].features.reversed_count == 1
    assert "heuristic-v1" in examples[earlier].stored_predictions


def test_an_unaggregated_later_meeting_withholds_the_earlier_label(
    db_session: Session, team: str
) -> None:
    """A meeting E has not aggregated cannot testify that nothing was reversed.

    It carries no lineage at all, so labelling the meeting before it "negative"
    asserts more than anything looked at. #445.
    """
    earlier = _meeting(db_session, team, days_ago=30)
    _aggregate(db_session, earlier)
    _meeting(db_session, team, days_ago=25)  # exists, never aggregated

    labeled = {e.meeting_id for e in labeled_examples(db_session, now=NOW)}

    assert earlier not in labeled


def test_an_unaggregated_meeting_past_the_horizon_does_not_withhold(
    db_session: Session, team: str
) -> None:
    """Only a blind spot inside the horizon can hide a reversal of this meeting.

    Otherwise one meeting stuck in `analyzing` would un-label the whole team's
    history behind it.
    """
    earlier = _meeting(db_session, team, days_ago=30)
    _aggregate(db_session, earlier)
    _meeting(db_session, team, days_ago=3)  # unaggregated, but past earlier's horizon

    examples = {e.meeting_id: e for e in labeled_examples(db_session, now=NOW)}

    assert examples[earlier].reversed_within_horizon is False


def test_a_seen_reversal_survives_an_unaggregated_later_meeting(
    db_session: Session, team: str
) -> None:
    """A blind spot cannot unmake a reversal that was actually measured."""
    earlier = _meeting(db_session, team, days_ago=30)
    reverser = _meeting(db_session, team, days_ago=25)
    _aggregate(db_session, earlier)
    _aggregate(db_session, reverser, reverses=earlier)
    _meeting(db_session, team, days_ago=24)  # unaggregated, inside earlier's horizon

    examples = {e.meeting_id: e for e in labeled_examples(db_session, now=NOW)}

    assert examples[earlier].reversed_within_horizon is True


def test_the_blind_spot_log_separates_its_two_causes(db_session: Session, team: str) -> None:
    """ "0 labeled meetings" has two causes now, and they need different fixes.

    A measured meeting whose lineage came back without B's decisions is B not
    reaching D; a meeting E never aggregated is a stalled or failed pipeline.
    One count cannot tell an operator which one they have.
    """
    earlier = _meeting(db_session, team, days_ago=30)
    _aggregate(db_session, earlier)
    _meeting(db_session, team, days_ago=25)  # unaggregated, inside earlier's horizon

    with capture_logs() as logs:
        labeled_examples(db_session, now=NOW)

    blocked = [e for e in logs if e["event"] == "intelligence_history_labels_blocked_by_blind_spot"]
    assert len(blocked) == 1
    assert blocked[0]["meetings"] == 1
    assert blocked[0]["unaggregated"] == 1
    assert blocked[0]["unmeasured_lineage"] == 0


def test_recent_meetings_are_not_labeled_yet(db_session: Session, team: str) -> None:
    recent = _meeting(db_session, team, days_ago=3)
    _aggregate(db_session, recent)

    assert recent not in {e.meeting_id for e in labeled_examples(db_session, now=NOW)}


def test_evaluate_declines_to_score_a_handful_of_examples(db_session: Session, team: str) -> None:
    _aggregate(db_session, _meeting(db_session, team, days_ago=30))

    reports = evaluate(labeled_examples(db_session, now=NOW))

    assert reports and all(r is None for r in reports.values())
