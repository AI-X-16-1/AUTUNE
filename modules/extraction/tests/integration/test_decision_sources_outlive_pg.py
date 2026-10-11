"""A decision says its source was deleted, on a real PostgreSQL (#400).

Module A's rerun of a meeting deletes every utterance and writes new ones, and
a person can delete their own speech. Either way ``ext_decision_sources`` used
to lose the row with the utterance, and a decision a person had added pointing
at two lines came back as one that had pointed at none.

Only the database does this -- ``ON DELETE SET NULL`` -- so the utterances are
deleted with plain SQL, the way module A and an account deletion do, and never
through the ORM. The decision is written by ``create_decision``, the path a
person's "add a decision" takes, and read by the functions S15, module D and
the Notion page read it through.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting, Team, Utterance
from autune_extraction import service
from autune_extraction.models import ExtDecision
from autune_extraction.schemas import DecisionCreate

PROPOSED = "색인을 다음 분기에 다시 만들면 어떨까요"
SETTLED = "네 그렇게 하기로 하죠"


@pytest.fixture
def meeting(db_session: Session) -> dict[str, str]:
    """A meeting of two speakers and a decision a person added from both lines."""
    team = Team(name="팀")
    db_session.add(team)
    db_session.flush()
    meeting = Meeting(team_id=team.id, title="주간 회의")
    db_session.add(meeting)
    db_session.flush()
    proposed, settled = (
        Utterance(meeting_id=meeting.id, speaker_label=who, start_sec=at, end_sec=at + 2, text=text)
        for who, at, text in (("S1", 0.0, PROPOSED), ("S2", 3.0, SETTLED))
    )
    db_session.add_all([proposed, settled])
    db_session.flush()
    added = service.create_decision(
        db_session,
        DecisionCreate(
            meeting_id=meeting.id,
            statement="색인을 다음 분기에 재구축한다",
            source_utterance_ids=[proposed.id, settled.id],
        ),
    )
    return {
        "meeting": meeting.id,
        "decision": added.id,
        "proposed": proposed.id,
        "settled": settled.id,
    }


def the_decision(session: Session, ids: dict[str, str]) -> ExtDecision:
    session.expire_all()
    row = session.get(ExtDecision, ids["decision"])
    assert row is not None
    return row


def the_row(session: Session, ids: dict[str, str]):  # type: ignore[no-untyped-def]
    session.expire_all()
    (row,) = service.review_for_meeting(session, ids["meeting"]).decisions
    return row


def test_a_rerun_of_the_meeting_leaves_the_decision_saying_its_sources_were_deleted(
    db_session: Session, meeting: dict[str, str]
) -> None:
    before = the_row(db_session, meeting)
    assert before.source_utterance_ids == [meeting["proposed"], meeting["settled"]]
    assert before.deleted_source_count == 0

    # Module A's rerun: every utterance of the meeting goes, new ones come.
    db_session.execute(
        sa.text("DELETE FROM utterances WHERE meeting_id = :id"), {"id": meeting["meeting"]}
    )
    db_session.add(
        Utterance(
            meeting_id=meeting["meeting"],
            speaker_label="S1",
            start_sec=0.0,
            end_sec=2.0,
            text=PROPOSED,
        )
    )
    db_session.flush()

    after = the_row(db_session, meeting)
    assert after.id == meeting["decision"] and after.origin == "user"
    assert after.source_utterance_ids == []
    assert after.deleted_source_count == 2
    assert after.summary is None
    # Nothing asked for it to be looked at again: its wording did not change.
    assert after.needs_recheck is False


def test_a_person_deleting_their_speech_leaves_the_other_source_and_a_count(
    db_session: Session, meeting: dict[str, str]
) -> None:
    db_session.execute(sa.text("DELETE FROM utterances WHERE id = :id"), {"id": meeting["settled"]})

    row = the_row(db_session, meeting)
    assert row.source_utterance_ids == [meeting["proposed"]]
    assert row.deleted_source_count == 1

    detail = service.read_decision_detail(db_session, the_decision(db_session, meeting))
    assert [said.id for said in detail.sources] == [meeting["proposed"]]
    assert detail.deleted_source_count == 1


def test_what_is_kept_of_a_deleted_source_names_nothing_of_it(
    db_session: Session, meeting: dict[str, str]
) -> None:
    """The row that stays says a source was here: no id of the line, none of
    its words, and no speaker or time -- it never held those."""
    db_session.execute(sa.text("DELETE FROM utterances WHERE id = :id"), {"id": meeting["settled"]})

    kept = db_session.execute(
        sa.text(
            "SELECT utterance_id, position, excerpt_start, excerpt_end"
            " FROM ext_decision_sources WHERE decision_id = :id ORDER BY position"
        ),
        {"id": meeting["decision"]},
    ).all()
    assert [tuple(row) for row in kept] == [
        (meeting["proposed"], 0, None, None),
        (None, 1, None, None),
    ]
    columns = db_session.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'ext_decision_sources' ORDER BY column_name"
        )
    ).scalars()
    assert list(columns) == [
        "decision_id",
        "excerpt_end",
        "excerpt_start",
        "id",
        "position",
        "utterance_id",
    ], "a new column on this table is a new thing kept of a deleted line: look at it"

    said = the_row(db_session, meeting).model_dump_json()
    detail = service.read_decision_detail(
        db_session, the_decision(db_session, meeting)
    ).model_dump_json()
    for read in (said, detail):
        assert meeting["settled"] not in read
        assert SETTLED not in read


def test_module_d_and_the_notion_page_see_the_sources_that_are_left(
    db_session: Session, meeting: dict[str, str]
) -> None:
    """What leaves B is what left it while the link went with the utterance:
    the ids that exist, and their number."""
    db_session.execute(sa.text("DELETE FROM utterances WHERE id = :id"), {"id": meeting["settled"]})
    db_session.expire_all()

    (for_d,) = service.decisions_for_meeting(db_session, meeting["meeting"])
    assert for_d.source_utterance_ids == [meeting["proposed"]]

    page = service.decision_notion_properties(
        "색인을 다음 분기에 재구축한다",
        the_decision(db_session, meeting),
        None,
        {"sources": "sources"},
    )
    assert page["sources"] == {"number": 1}


def test_a_correction_pass_reads_the_sources_that_are_left(
    db_session: Session, meeting: dict[str, str]
) -> None:
    """With every source gone there is nothing to compare, and nothing is flagged."""
    db_session.execute(
        sa.text("DELETE FROM utterances WHERE meeting_id = :id"), {"id": meeting["meeting"]}
    )
    db_session.expire_all()

    service.apply_source_corrections(db_session, meeting_id=meeting["meeting"], spoken={})

    assert the_row(db_session, meeting).needs_recheck is False


def test_a_correction_pass_treats_a_lost_source_as_it_did_when_the_link_went_too(
    db_session: Session, meeting: dict[str, str]
) -> None:
    """Unchanged by #400, and pinned so that it stays a choice: the pass compares
    the lines that are left with what the person typed the decision from, so a
    decision that lost one of two sources is asked about at the next pass. The
    same holds for an action item (``live_source_ids``)."""
    db_session.execute(sa.text("DELETE FROM utterances WHERE id = :id"), {"id": meeting["settled"]})
    db_session.expire_all()

    service.apply_source_corrections(
        db_session, meeting_id=meeting["meeting"], spoken={meeting["proposed"]: PROPOSED}
    )

    assert the_row(db_session, meeting).needs_recheck is True
