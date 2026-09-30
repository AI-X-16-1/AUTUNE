"""A speaker identified after extraction becomes the assignee, on PostgreSQL.

The unit suite runs the rules on SQLite. What only PostgreSQL shows: the
correlated ``NOT EXISTS`` over ``ext_edit_events``, the ``UPDATE ... RETURNING``
and the foreign key the new ``assignee_id`` must satisfy.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting, Participant, Team, User, Utterance
from autune_extraction import service
from autune_extraction.models import ExtActionItem, ExtActionItemSource, ExtEditEvent


def test_identified_speakers_fill_only_the_untouched_items(db_session: Session) -> None:
    team = Team(name="팀")
    user = User(email="speaker@example.com", display_name="김민경")
    db_session.add_all([team, user])
    db_session.flush()
    meeting = Meeting(team_id=team.id, title="주간 회의")
    db_session.add(meeting)
    db_session.flush()
    speaker = Participant(meeting_id=meeting.id, speaker_label="Speaker 2", consented=True)
    db_session.add(speaker)
    db_session.flush()

    def drafted(start: float) -> ExtActionItem:
        line = Utterance(
            meeting_id=meeting.id,
            participant_id=speaker.id,
            speaker_label="Speaker 2",
            start_sec=start,
            end_sec=start + 2,
            text="제가 정리하겠습니다",
        )
        db_session.add(line)
        db_session.flush()
        row = ExtActionItem(
            meeting_id=meeting.id,
            description="정리할 예정",
            assignee_label="Speaker 2",
            status="needs_confirmation",
            confidence=0.9,
            origin="model",
            sources=[ExtActionItemSource(utterance_id=line.id)],
        )
        db_session.add(row)
        db_session.flush()
        return row

    untouched, cleared = drafted(0.0), drafted(3.0)
    db_session.add(
        ExtEditEvent(
            meeting_id=meeting.id, action_item_id=cleared.id, kind="edited", fields="assignee_id"
        )
    )
    speaker.user_id = user.id
    db_session.flush()

    filled = service.fill_identified_assignees(db_session)

    assert [i.id for i in filled] == [untouched.id]
    rows = dict(
        db_session.execute(
            sa.select(ExtActionItem.id, ExtActionItem.assignee_id).where(
                ExtActionItem.meeting_id == meeting.id
            )
        )
        .tuples()
        .all()
    )
    assert rows == {untouched.id: user.id, cleared.id: None}
