"""A team's deletion against another request for the same team, on PostgreSQL.

``team_deletion.delete_team`` rests on three locks, and none of them shows in
a single session:

- the team's memberships, in ``leave_team``'s order, so a mate who is leaving
  is counted before the team is called a team of one;
- the team's pending invitations, which it deletes, so nobody joins
  underneath: an ``accept`` in flight finishes first and is counted, and one
  that comes later finds no invitation;
- the team's meetings, so an upload cannot claim one while it is being
  deleted and leave a queued job whose row is about to go -- and an upload
  already in flight is seen before the team is called idle. That lock is
  ``FOR NO KEY UPDATE``: a hook, in a session of its own, can still write a
  row that names the meeting.

Same shape as ``test_team_leave_concurrency``: two real sessions, the second
held on the first's lock, and ``pg_stat_activity`` as the witness.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_audio import invitations, service, team_deletion
from autune_audio.invitations import InvitationUnusableError
from autune_audio.models import AudTeamInvitation, TranscriptionJob
from autune_audio.team_deletion import TeamHasOtherMembersError, TeamMeetingInProgressError
from autune_core import Meeting, Participant, Team, TeamMember, User, deletion
from autune_core.errors import NotFoundError

NAME = "지울 팀"
TOKEN = "an-invitation-token-for-the-concurrency-test"


@dataclass(frozen=True)
class Scene:
    team: str
    host: str
    mate: str
    newcomer: str
    meeting: str


@pytest.fixture(autouse=True)
def no_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deletion, "_meeting_hooks", {})


@pytest.fixture
def scene(db_engine: sa.Engine) -> Iterator[Scene]:
    """A team of one with a meeting and an open invitation, and two people
    who are not on it. Committed, so that two sessions can both see it."""
    with Session(db_engine) as session:
        team = Team(name=NAME)
        host = User(email="delete-host@example.com", display_name="남은 사람")
        mate = User(email="delete-mate@example.com", display_name="팀원")
        newcomer = User(email="delete-newcomer@example.com", display_name="초대받은 사람")
        session.add_all([team, host, mate, newcomer])
        session.flush()
        session.add(TeamMember(team_id=team.id, user_id=host.id))
        meeting = Meeting(team_id=team.id, title="회의", status="scheduled")
        session.add(meeting)
        session.add(
            AudTeamInvitation(
                team_id=team.id,
                email=None,
                token_hash=invitations._digest(TOKEN),
                invited_by=host.id,
                expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            )
        )
        session.commit()
        made = Scene(team.id, host.id, mate.id, newcomer.id, meeting.id)
    try:
        yield made
    finally:
        with Session(db_engine) as session:
            session.execute(sa.delete(Team).where(Team.id == made.team))
            session.execute(
                sa.delete(User).where(User.id.in_((made.host, made.mate, made.newcomer)))
            )
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


def delete_as(user_id: str, team_id: str) -> Callable[[Session], object]:
    def run(session: Session) -> object:
        user = session.get(User, user_id)
        assert user is not None
        return team_deletion.delete_team(session, team_id=team_id, member=user, name=NAME)

    return run


def accept_as(user_id: str) -> Callable[[Session], object]:
    def run(session: Session) -> object:
        user = session.get(User, user_id)
        assert user is not None
        return invitations.accept(session, token=TOKEN, user=user)

    return run


def on_team(engine: sa.Engine, team_id: str) -> set[str]:
    with Session(engine) as session:
        return set(
            session.scalars(sa.select(TeamMember.user_id).where(TeamMember.team_id == team_id))
        )


def exists(engine: sa.Engine, model: type, key: str) -> bool:
    with Session(engine) as session:
        return session.get(model, key) is not None


def test_somebody_joining_as_the_team_is_deleted_is_counted_and_the_team_stays(
    db_engine: sa.Engine, scene: Scene
) -> None:
    result = second_after_first(
        db_engine, accept_as(scene.newcomer), delete_as(scene.host, scene.team)
    )

    assert isinstance(result, TeamHasOtherMembersError), result
    assert on_team(db_engine, scene.team) == {scene.host, scene.newcomer}
    assert exists(db_engine, Meeting, scene.meeting)


def test_an_invitation_opened_after_the_deletion_began_brings_nobody_onto_a_deleted_team(
    db_engine: sa.Engine, scene: Scene
) -> None:
    result = second_after_first(
        db_engine, delete_as(scene.host, scene.team), accept_as(scene.newcomer)
    )

    assert isinstance(result, InvitationUnusableError), result
    assert not exists(db_engine, Team, scene.team)
    assert on_team(db_engine, scene.team) == set()


def test_a_mate_who_is_leaving_is_counted_before_the_team_is_called_a_team_of_one(
    db_engine: sa.Engine, scene: Scene
) -> None:
    with Session(db_engine) as session:
        session.add(TeamMember(team_id=scene.team, user_id=scene.mate))
        session.commit()

    def mate_leaves(session: Session) -> None:
        mate = session.get(User, scene.mate)
        assert mate is not None
        service.leave_team(session, team_id=scene.team, member=mate)

    result = second_after_first(db_engine, mate_leaves, delete_as(scene.host, scene.team))

    # Held on the mate's lock, then alone: the deletion goes through. Had it
    # counted before the lock it would have refused a team of one.
    assert result == "done", result
    assert not exists(db_engine, Team, scene.team)


def test_an_upload_that_arrives_while_the_team_is_deleted_claims_nothing(
    db_engine: sa.Engine, scene: Scene
) -> None:
    def upload(session: Session) -> object:
        host = session.get(User, scene.host)
        assert host is not None
        return service.start_transcription(session, meeting_id=scene.meeting, uploader=host)

    result = second_after_first(db_engine, delete_as(scene.host, scene.team), upload)

    assert isinstance(result, NotFoundError), result
    assert not exists(db_engine, Meeting, scene.meeting)
    with Session(db_engine) as session:
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(TranscriptionJob)
                .where(TranscriptionJob.meeting_id == scene.meeting)
            )
            == 0
        )


def test_an_upload_in_flight_is_seen_before_the_team_is_called_idle(
    db_engine: sa.Engine, scene: Scene
) -> None:
    def upload(session: Session) -> object:
        host = session.get(User, scene.host)
        assert host is not None
        return service.start_transcription(session, meeting_id=scene.meeting, uploader=host)

    result = second_after_first(db_engine, upload, delete_as(scene.host, scene.team))

    # Held on the upload's claim of the meeting, then refused by the job it
    # left. Without the lock the job was not yet there to see, and the team
    # would have gone with a queued job and its recording.
    assert isinstance(result, TeamMeetingInProgressError), result
    assert exists(db_engine, Team, scene.team)
    assert exists(db_engine, Meeting, scene.meeting)


def test_a_hook_can_write_a_row_that_names_the_meeting_while_the_deletion_holds_it(
    db_engine: sa.Engine, scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook owns its session, so it meets the deletion's locks from outside.
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
        delete_as(scene.host, scene.team)(session)
        session.commit()

    assert wrote == [scene.meeting]
    assert not exists(db_engine, Team, scene.team)
    with Session(db_engine) as session:
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(Participant)
                .where(Participant.meeting_id == scene.meeting)
            )
            == 0
        )
