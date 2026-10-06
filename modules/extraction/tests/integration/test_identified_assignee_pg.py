"""A speaker identified after extraction becomes the assignee, on PostgreSQL.

The unit suite runs the rules on SQLite. What only PostgreSQL shows: the
correlated ``NOT EXISTS`` over ``ext_edit_events`` with its NULL ``fields``,
the label compared against the utterance's, the ``UPDATE ... RETURNING`` and
the foreign key the new ``assignee_id`` must satisfy -- and, for a speaker
corrected or undone afterwards (#929), the outer joins and an update that
matches a NULL label and a set account as they were read.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_core import Meeting, Participant, Team, TeamMember, User, Utterance
from autune_extraction import service
from autune_extraction.models import ExtActionItem, ExtActionItemSource, ExtEditEvent
from autune_extraction.schemas import ActionItemUpdate


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

    untouched, cleared, legacy, renamed = (drafted(s) for s in (0.0, 3.0, 6.0, 9.0))
    db_session.add_all(
        [
            ExtEditEvent(
                meeting_id=meeting.id,
                action_item_id=cleared.id,
                kind="edited",
                fields="assignee_id",
            ),
            # Written before ``fields`` existed: may have been the assignee.
            ExtEditEvent(meeting_id=meeting.id, action_item_id=legacy.id, kind="edited"),
        ]
    )
    renamed.assignee_label = "민경님"
    speaker.user_id = user.id
    db_session.flush()

    filled = service.fill_identified_assignees(db_session)

    assert [i.id for i in filled] == [untouched.id]
    rows = {
        item_id: (assignee, label)
        for item_id, assignee, label in db_session.execute(
            sa.select(
                ExtActionItem.id, ExtActionItem.assignee_id, ExtActionItem.assignee_label
            ).where(ExtActionItem.meeting_id == meeting.id)
        )
    }
    assert rows == {
        untouched.id: (user.id, None),
        cleared.id: (None, "Speaker 2"),
        legacy.id: (None, "Speaker 2"),
        renamed.id: (None, "민경님"),
    }


def test_a_corrected_and_an_undone_speaker_move_only_the_untouched_items(
    db_session: Session,
) -> None:
    team = Team(name="팀")
    first = User(email="first@example.com", display_name="김민경")
    second = User(email="second@example.com", display_name="이승환")
    db_session.add_all([team, first, second])
    db_session.flush()
    db_session.add_all(
        [
            TeamMember(team_id=team.id, user_id=first.id),
            TeamMember(team_id=team.id, user_id=second.id),
        ]
    )
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

    def held() -> dict[str, tuple[str | None, str | None]]:
        return {
            item_id: (assignee, label)
            for item_id, assignee, label in db_session.execute(
                sa.select(
                    ExtActionItem.id, ExtActionItem.assignee_id, ExtActionItem.assignee_label
                ).where(ExtActionItem.meeting_id == meeting.id)
            )
        }

    untouched, confirmed, chosen, finished = (drafted(s) for s in (0.0, 3.0, 6.0, 9.0))
    # The label is put to the first person, wrongly, and the fill gives them
    # all four.
    speaker.user_id = first.id
    db_session.flush()
    assert len(service.fill_identified_assignees(db_session)) == 4
    # Then people work on three of them, through the board's own write.
    service.update_action_item(db_session, confirmed, ActionItemUpdate(status=ActionStatus.TODO))
    service.update_action_item(db_session, chosen, ActionItemUpdate(assignee_id=first.id))
    service.update_action_item(db_session, chosen, ActionItemUpdate(assignee_id=second.id))
    service.update_action_item(db_session, chosen, ActionItemUpdate(assignee_id=first.id))
    service.update_action_item(db_session, finished, ActionItemUpdate(status=ActionStatus.DONE))
    db_session.flush()

    # Corrected to the second person.
    speaker.user_id = second.id
    db_session.flush()
    moved = service.fill_identified_assignees(db_session)

    assert sorted(i.id for i in moved) == sorted([untouched.id, confirmed.id])
    assert held() == {
        untouched.id: (second.id, None),
        confirmed.id: (second.id, None),
        chosen.id: (first.id, None),
        finished.id: (first.id, None),
    }

    # Undone: nobody.
    speaker.user_id = None
    db_session.flush()
    emptied = service.fill_identified_assignees(db_session)

    assert sorted(i.id for i in emptied) == sorted([untouched.id, confirmed.id])
    assert held() == {
        untouched.id: (None, "Speaker 2"),
        confirmed.id: (None, "Speaker 2"),
        chosen.id: (first.id, None),
        finished.id: (first.id, None),
    }
    assert service.fill_identified_assignees(db_session) == []
