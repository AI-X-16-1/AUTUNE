"""Every ``ext_`` row leaves with its meeting, on a real PostgreSQL.

privacy.md section 4: analysis results have a retention window and a person can
delete their data at any time, and data-model.md asks every module table for a
path to deletion by ``meeting_id``. Module B's path is ``ON DELETE CASCADE`` on
every table, which only the database enforces -- so these delete with plain SQL,
the way a retention sweep or an account deletion would, and never through the
ORM, whose cascade would pass the test on its own.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting, Team, User, Utterance
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtClassification,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionSource,
    ExtEditEvent,
)

B_TABLES = (
    "ext_action_items",
    "ext_action_item_sources",
    "ext_edit_events",
    "ext_decisions",
    "ext_decision_sources",
    "ext_confirmations",
    "ext_classifications",
)


def count(session: Session, table: str) -> int:
    return session.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608


@pytest.fixture
def meeting(db_session: Session) -> dict[str, str]:
    """A meeting with one row in every ``ext_`` table, all of it B's own shape."""
    team = Team(name="팀")
    user = User(email="assignee@example.com", display_name="담당자")
    db_session.add_all([team, user])
    db_session.flush()

    meeting = Meeting(team_id=team.id, title="주간 회의")
    db_session.add(meeting)
    db_session.flush()

    said, agreed = (
        Utterance(meeting_id=meeting.id, speaker_label="S1", start_sec=s, end_sec=s + 2, text=t)
        for s, t in ((0.0, "제가 정리하겠습니다"), (3.0, "한번 볼게요"))
    )
    db_session.add_all([said, agreed])
    db_session.flush()

    item = ExtActionItem(
        meeting_id=meeting.id,
        description="정리",
        assignee_id=user.id,
        status="needs_confirmation",
        confidence=0.8,
        origin="model",
    )
    item.sources = [ExtActionItemSource(utterance_id=said.id)]
    decision = ExtDecision(meeting_id=meeting.id, statement="정리하기로", confidence=0.7)
    decision.sources = [ExtDecisionSource(utterance_id=said.id, position=0)]
    db_session.add_all(
        [
            item,
            decision,
            ExtClassification(
                utterance_id=said.id,
                meeting_id=meeting.id,
                kind="commitment",
                confidence=0.8,
                model_version="test",
                nli_verified=False,
            ),
            ExtConfirmation(
                utterance_id=agreed.id,
                meeting_id=meeting.id,
                reason="weak_assent",
                sent_at=datetime(2026, 9, 11, tzinfo=UTC),
            ),
        ]
    )
    db_session.flush()
    db_session.add(ExtEditEvent(meeting_id=meeting.id, action_item_id=item.id, kind="edited"))
    db_session.flush()
    return {"meeting": meeting.id, "said": said.id, "user": user.id, "item": item.id}


def test_every_b_table_has_a_row_to_lose(db_session: Session, meeting: dict[str, str]) -> None:
    """The fixture is only a test if each table starts non-empty."""
    assert {table: count(db_session, table) > 0 for table in B_TABLES} == dict.fromkeys(
        B_TABLES, True
    )


def test_deleting_a_meeting_deletes_everything_b_derived_from_it(
    db_session: Session, meeting: dict[str, str]
) -> None:
    """A meeting that expires or is deleted leaves nothing of itself in B."""
    db_session.execute(sa.text("DELETE FROM meetings WHERE id = :id"), {"id": meeting["meeting"]})

    assert {table: count(db_session, table) for table in B_TABLES} == dict.fromkeys(B_TABLES, 0)


def test_deleting_an_utterance_takes_its_derived_rows(
    db_session: Session, meeting: dict[str, str]
) -> None:
    """Its classification, and the links that quoted it. The item stays, now
    without that source -- the quotation is what went, not the commitment."""
    db_session.execute(sa.text("DELETE FROM utterances WHERE id = :id"), {"id": meeting["said"]})

    assert count(db_session, "ext_classifications") == 0
    assert count(db_session, "ext_action_item_sources") == 0
    assert count(db_session, "ext_decision_sources") == 0
    assert count(db_session, "ext_action_items") == 1


def test_a_departed_assignee_leaves_the_item_standing(
    db_session: Session, meeting: dict[str, str]
) -> None:
    """ADR 0007: the commitment does not stop having been made."""
    db_session.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": meeting["user"]})

    assignee = db_session.execute(
        sa.text("SELECT assignee_id FROM ext_action_items WHERE id = :id"), {"id": meeting["item"]}
    ).scalar_one()
    assert assignee is None


def test_a_deleted_item_keeps_its_edit_event_and_loses_the_link(
    db_session: Session, meeting: dict[str, str]
) -> None:
    """Edit cost counts that a deletion happened; cascading would erase the
    evidence that the model was wrong along with the wrong item."""
    db_session.execute(
        sa.text("DELETE FROM ext_action_items WHERE id = :id"), {"id": meeting["item"]}
    )

    events = db_session.execute(sa.text("SELECT action_item_id FROM ext_edit_events")).all()
    assert [row[0] for row in events] == [None]
