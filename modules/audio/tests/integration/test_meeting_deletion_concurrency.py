"""A meeting's deletion against another request for the same meeting, on
PostgreSQL (#1161).

``meeting_deletion.delete_meeting`` rests on one lock, the meeting's row, and
it does not show in a single session:

- an upload cannot claim the meeting while it is being deleted and leave a
  queued job whose row is about to go, and an upload already in flight is
  seen before the meeting is called idle;
- a rename in flight finishes first, and it is its title the typed one is
  compared with;
- the lock is ``FOR NO KEY UPDATE``: a hook, in a session of its own, can
  still write a row that names the meeting.

Same shape as ``test_team_deletion_concurrency``: two real sessions, the
second held on the first's lock, and ``pg_stat_activity`` as the witness.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_audio import meeting_deletion, service
from autune_audio.meeting_deletion import MeetingInProgressError, MeetingTitleMismatchError
from autune_audio.models import TranscriptionJob
from autune_core import Meeting, Participant, Team, TeamMember, User, deletion
from autune_core.errors import NotFoundError

TITLE = "지울 회의"
NEW = "고친 이름"


@dataclass(frozen=True)
class Scene:
    team: str
    member: str
    meeting: str


@pytest.fixture(autouse=True)
def no_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deletion, "_meeting_hooks", {})


@pytest.fixture
def scene(db_engine: sa.Engine) -> Iterator[Scene]:
    """A team of one with a meeting. Committed, so that two sessions can both
    see it."""
    with Session(db_engine) as session:
        team = Team(name="회의를 지우는 팀")
        member = User(email="meeting-delete@example.com", display_name="구성원")
        session.add_all([team, member])
        session.flush()
        session.add(TeamMember(team_id=team.id, user_id=member.id))
        meeting = Meeting(team_id=team.id, title=TITLE, status="scheduled")
        session.add(meeting)
        session.commit()
        made = Scene(team.id, member.id, meeting.id)
    try:
        yield made
    finally:
        with Session(db_engine) as session:
            session.execute(sa.delete(Team).where(Team.id == made.team))
            session.execute(sa.delete(User).where(User.id == made.member))
            session.commit()


def waiting_on_a_lock(engine: sa.Engine, pid: int) -> bool:
    with engine.connect() as probe:
        return bool(
            probe.execute(
                sa.text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            ).scalar()
        )


def second_after_first(
    engine: sa.Engine, first: Callable[[Session], object], second: Callable[[Session], object]
) -> BaseException | str:
    """``first`` runs and does not commit; ``second`` is seen waiting on its
    lock; then the first commits. What the second came to: the exception it
    raised, or ``"done"``."""
    outcome: list[BaseException | str] = []
    one = Session(engine)
    first(one)  # not committed

    two = Session(engine)
    pid = two.connection().exec_driver_sql("SELECT pg_backend_pid()").scalar_one()

    def other_request() -> None:
        try:
            second(two)
            two.commit()
            outcome.append("done")
        except BaseException as caught:
            two.rollback()
            outcome.append(caught)

    thread = threading.Thread(target=other_request)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not waiting_on_a_lock(engine, pid):
            assert thread.is_alive(), f"the second request did not wait on the first: {outcome}"
            assert time.monotonic() < deadline, "the second request never reached the first's lock"
            time.sleep(0.05)
        one.commit()
        thread.join(timeout=10)
    finally:
        one.close()
        two.close()

    assert not thread.is_alive()
    (result,) = outcome
    return result


def as_member(scene: Scene, act: Callable[[Session, User], object]) -> Callable[[Session], object]:
    def run(session: Session) -> object:
        member = session.get(User, scene.member)
        assert member is not None
        return act(session, member)

    return run


def delete(scene: Scene, title: str = TITLE) -> Callable[[Session], object]:
    return as_member(
        scene,
        lambda session, member: meeting_deletion.delete_meeting(
            session, meeting_id=scene.meeting, member=member, title=title
        ),
    )


def upload(scene: Scene) -> Callable[[Session], object]:
    return as_member(
        scene,
        lambda session, member: service.start_transcription(
            session, meeting_id=scene.meeting, uploader=member
        ),
    )


def rename(scene: Scene, title: str = NEW) -> Callable[[Session], object]:
    return as_member(
        scene,
        lambda session, member: service.rename_meeting(
            session, meeting_id=scene.meeting, member=member, title=title
        ),
    )


def exists(engine: sa.Engine, meeting_id: str) -> bool:
    with Session(engine) as session:
        return session.get(Meeting, meeting_id) is not None


def jobs_of(engine: sa.Engine, meeting_id: str) -> int:
    with Session(engine) as session:
        return (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(TranscriptionJob)
                .where(TranscriptionJob.meeting_id == meeting_id)
            )
            or 0
        )


def test_an_upload_that_arrives_while_the_meeting_is_deleted_claims_nothing(
    db_engine: sa.Engine, scene: Scene
) -> None:
    result = second_after_first(db_engine, delete(scene), upload(scene))

    assert isinstance(result, NotFoundError), result
    assert not exists(db_engine, scene.meeting)
    assert jobs_of(db_engine, scene.meeting) == 0


def test_an_upload_in_flight_is_seen_before_the_meeting_is_called_idle(
    db_engine: sa.Engine, scene: Scene
) -> None:
    result = second_after_first(db_engine, upload(scene), delete(scene))

    # Held on the upload's claim of the meeting, then refused by the job it
    # left. Without the lock the job was not yet there to see, and the meeting
    # would have gone with a queued job and its recording.
    assert isinstance(result, MeetingInProgressError), result
    assert exists(db_engine, scene.meeting)
    assert jobs_of(db_engine, scene.meeting) == 1


def test_a_rename_in_flight_finishes_first_and_its_title_is_the_one_that_counts(
    db_engine: sa.Engine, scene: Scene
) -> None:
    result = second_after_first(db_engine, rename(scene), delete(scene, TITLE))

    # The title typed was the meeting's when the request was sent and is not
    # by the time the deletion holds the row.
    assert isinstance(result, MeetingTitleMismatchError), result
    assert exists(db_engine, scene.meeting)

    with Session(db_engine) as session:
        delete(scene, NEW)(session)
        session.commit()
    assert not exists(db_engine, scene.meeting)


def test_a_second_deletion_finds_the_meeting_gone(db_engine: sa.Engine, scene: Scene) -> None:
    result = second_after_first(db_engine, delete(scene), delete(scene))

    assert isinstance(result, NotFoundError), result
    assert not exists(db_engine, scene.meeting)


def test_a_hook_can_write_a_row_that_names_the_meeting_while_the_deletion_holds_it(
    db_engine: sa.Engine, scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook owns its session, so it meets the deletion's lock from outside.
    Under ``FOR UPDATE`` its insert would wait for the transaction that is
    waiting for it."""
    wrote: list[str] = []

    def hook(meeting_id: str) -> None:
        with Session(db_engine) as own:
            own.execute(sa.text("SET LOCAL lock_timeout = '3s'"))
            own.add(Participant(meeting_id=meeting_id, user_id=None, speaker_label="화자 9"))
            own.commit()
        wrote.append(meeting_id)

    monkeypatch.setattr(deletion, "_meeting_hooks", {"elsewhere": hook})

    with Session(db_engine) as session:
        delete(scene)(session)
        session.commit()

    assert wrote == [scene.meeting]
    assert not exists(db_engine, scene.meeting)
    with Session(db_engine) as session:
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(Participant)
                .where(Participant.meeting_id == scene.meeting)
            )
            == 0
        )
