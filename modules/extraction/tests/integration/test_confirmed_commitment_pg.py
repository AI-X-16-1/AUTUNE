"""A speaker's "yes, that was a commitment", and the lines before a source, on PostgreSQL.

The unit suite runs these on SQLite. What only PostgreSQL shows: the correlated
``NOT EXISTS`` that spares an edited draft, the ``length(trim(...))`` filter and
the ``or_``/``and_`` ordering in ``context_before``, and that the foreign keys
accept the draft's source row.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_contracts.enums import UtteranceKind
from autune_core import Meeting, Participant, Team, User, Utterance
from autune_extraction import service
from autune_extraction.confirmations import ConfirmationResponse
from autune_extraction.models import ExtActionItem, ExtConfirmation, ExtEditEvent

STARTED = datetime(2026, 9, 9, 1, 0, tzinfo=UTC)  # a Wednesday, 10:00 in Seoul


@pytest.fixture
def scene(db_session: Session) -> dict[str, str]:
    team = Team(name="팀")
    user = User(email="speaker@example.com", display_name="김민경")
    db_session.add_all([team, user])
    db_session.flush()
    meeting = Meeting(team_id=team.id, title="주간 회의", started_at=STARTED)
    db_session.add(meeting)
    db_session.flush()
    yes = Participant(
        meeting_id=meeting.id, user_id=user.id, speaker_label="김민경", consented=True
    )
    no = Participant(meeting_id=meeting.id, speaker_label="박지영", consented=False)
    db_session.add_all([yes, no])
    db_session.flush()

    def say(start: float, text: str, participant: Participant) -> Utterance:
        return Utterance(
            meeting_id=meeting.id,
            participant_id=participant.id,
            speaker_label=participant.speaker_label,
            start_sec=start,
            end_sec=start + 2,
            text=text,
        )

    lines = [
        say(0.0, "지난주 고객 인터뷰 결과가 아직 정리가 안 됐어요", yes),
        say(3.0, "동의하지 않은 사람의 말", no),
        say(6.0, "   ", yes),
        say(9.0, "그럼 제가 다음 주 화요일까지 볼게요", yes),
    ]
    db_session.add_all(lines)
    db_session.flush()
    db_session.add(
        ExtConfirmation(
            utterance_id=lines[3].id,
            meeting_id=meeting.id,
            reason="weak_assent",
            sent_at=datetime.now(UTC),
        )
    )
    db_session.flush()
    return {"meeting": meeting.id, "user": user.id, "first": lines[0].id, "ask": lines[3].id}


def answer(scene: dict[str, str], kind: UtteranceKind) -> ConfirmationResponse:
    return ConfirmationResponse(
        utterance_id=scene["ask"], resolved_kind=kind, responder_id="U_SPEAKER"
    )


def items(session: Session) -> list[ExtActionItem]:
    return list(session.scalars(sa.select(ExtActionItem)))


def test_a_commitment_answer_makes_one_draft_and_a_retry_makes_no_second(
    db_session: Session, scene: dict[str, str]
) -> None:
    service.resolve_confirmation(db_session, answer(scene, UtteranceKind.COMMITMENT))
    service.resolve_confirmation(db_session, answer(scene, UtteranceKind.COMMITMENT))

    (item,) = items(db_session)
    assert item.description == "다음 주 화요일까지 볼 예정"
    assert item.assignee_id == scene["user"]
    assert item.status == "needs_confirmation"
    assert [s.utterance_id for s in item.sources] == [scene["ask"]]


def test_changing_the_answer_takes_back_an_untouched_draft_only(
    db_session: Session, scene: dict[str, str]
) -> None:
    service.resolve_confirmation(db_session, answer(scene, UtteranceKind.COMMITMENT))
    service.resolve_confirmation(db_session, answer(scene, UtteranceKind.CONCERN))
    assert items(db_session) == []

    service.resolve_confirmation(db_session, answer(scene, UtteranceKind.COMMITMENT))
    (item,) = items(db_session)
    db_session.add(
        ExtEditEvent(
            meeting_id=scene["meeting"], action_item_id=item.id, kind="edited", fields="due_date"
        )
    )
    db_session.flush()

    service.resolve_confirmation(db_session, answer(scene, UtteranceKind.CONCERN))

    assert len(items(db_session)) == 1, "an edited draft is a person's now"


def test_the_context_skips_a_speaker_who_did_not_consent_and_a_blank_line(
    db_session: Session, scene: dict[str, str]
) -> None:
    context = service.context_before(db_session, [scene["ask"]])

    assert [c.text for c in context] == ["지난주 고객 인터뷰 결과가 아직 정리가 안 됐어요"]


def test_an_items_detail_carries_its_context_on_postgres(
    db_session: Session, scene: dict[str, str]
) -> None:
    service.resolve_confirmation(db_session, answer(scene, UtteranceKind.COMMITMENT))
    (item,) = items(db_session)

    detail = service.read_detail(db_session, item)

    assert [s.text for s in detail.sources] == ["그럼 제가 다음 주 화요일까지 볼게요"]
    assert [c.text for c in detail.context] == ["지난주 고객 인터뷰 결과가 아직 정리가 안 됐어요"]
