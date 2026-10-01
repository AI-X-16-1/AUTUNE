"""A person's own data: what Autune holds about them, a copy of it, and deleting it.

privacy.md section 4 and invariant 11 say a user can delete their own data at
any time. Until this module that was true of one thing, the voice profile
(``DELETE /me/voice-profile``): ``autune_core.deletion.run_user_hooks`` had no
caller (#358), nothing deleted a ``users`` row, and nothing deleted a person's
speech. S29 (#554) is the screen; this is what its buttons do.

**Module A does it because A writes ``users``, ``participants`` and
``utterances``** (invariant 4). Another module's per-person state is its own
to clean up, which is what the user hooks are for; A runs them and owns only
the order.

Two deletions, matching S29's two buttons:

- **"내 발화 데이터 모두 삭제"** -- the person stays, their speech goes:
  every utterance attributed to them and every vector of their voice. The
  meeting keeps its other speakers, and the participant row stays as a
  record of attendance with nothing said under it. What B, C and D derived
  from those utterances follows their own foreign keys (``ON DELETE CASCADE``
  or ``SET NULL`` on ``utterances.id``); ADR 0007 decision 5 is what decides
  whether a decision outlives its source, and it is still Proposed (#92).
- **Account deletion** -- all of the above, every module's user hook, then
  the ``users`` row, whose foreign keys take sessions, memberships and
  integrations with it and clear ``participants.user_id``.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting, Participant, Team, TeamMember, User, Utterance, get_logger
from autune_core.deletion import run_user_hooks
from autune_core.errors import NotFoundError

from .models import AudConsentAttestation, AudSpeakerEmbedding
from .schemas import AccountDeleted, MyData, SpeechDeleted, TeamPrivacy
from .service import delete_voice_profile, require_team_member

log = get_logger(__name__)


def _my_participant_ids(user_id: str) -> sa.Select[tuple[str]]:
    return sa.select(Participant.id).where(Participant.user_id == user_id)


def my_data(session: Session, *, user: User) -> MyData:
    """Counts of what Autune holds about ``user``, for ``user``."""
    meetings = session.scalar(
        sa.select(sa.func.count(sa.distinct(Utterance.meeting_id))).where(
            Utterance.participant_id.in_(_my_participant_ids(user.id))
        )
    )
    profile_rows, profile_since = session.execute(
        sa.select(sa.func.count(), sa.func.min(AudSpeakerEmbedding.created_at)).where(
            AudSpeakerEmbedding.user_id == user.id
        )
    ).one()
    consents = session.scalar(
        sa.select(sa.func.count())
        .select_from(AudConsentAttestation)
        .where(AudConsentAttestation.attested_by == user.id)
    )
    return MyData(
        meetings_with_my_speech=int(meetings or 0),
        voice_profile_rows=int(profile_rows),
        voice_profile_since=profile_since,
        consents_attested=int(consents or 0),
    )


def export_my_data(session: Session, *, user: User) -> dict[str, Any]:
    """Everything ``my_data`` counts, as the rows themselves (PIPA Article 35).

    **Only the caller's own speech.** A meeting appears because they spoke in
    it, and only their utterances are listed -- the export is a copy of their
    data, not a transcript download that happens to start with them. The text
    is what is stored, which is masked text (privacy.md section 2); there is
    no unmasked copy to export.

    **The voice profile is described, not exported.** A vector is meaningless
    outside the model that made it and is the strongest re-identifier Autune
    holds; a file of it on someone's laptop is a copy nobody can delete. When
    and from which meeting each one was confirmed is what the person can act
    on.
    """
    rows = session.execute(
        sa.select(
            Meeting.id,
            Meeting.title,
            Meeting.started_at,
            Utterance.id,
            Utterance.start_sec,
            Utterance.end_sec,
            Utterance.text,
        )
        .join(Meeting, Meeting.id == Utterance.meeting_id)
        .where(Utterance.participant_id.in_(_my_participant_ids(user.id)))
        .order_by(Meeting.started_at.nulls_last(), Meeting.id, Utterance.start_sec)
    ).all()
    meetings: dict[str, dict[str, Any]] = {}
    for meeting_id, title, started_at, utterance_id, start, end, text in rows:
        meeting = meetings.setdefault(
            meeting_id,
            {
                "meeting_id": meeting_id,
                "title": title,
                "started_at": started_at.isoformat() if started_at else None,
                "utterances": [],
            },
        )
        meeting["utterances"].append(
            {"utterance_id": utterance_id, "start_sec": start, "end_sec": end, "text": text}
        )

    profiles = session.execute(
        sa.select(
            AudSpeakerEmbedding.created_at,
            AudSpeakerEmbedding.source_meeting_id,
            AudSpeakerEmbedding.model_version,
        )
        .where(AudSpeakerEmbedding.user_id == user.id)
        .order_by(AudSpeakerEmbedding.created_at)
    ).all()
    consents = session.execute(
        sa.select(AudConsentAttestation.meeting_id, AudConsentAttestation.attested_at)
        .where(AudConsentAttestation.attested_by == user.id)
        .order_by(AudConsentAttestation.attested_at)
    ).all()
    teams = session.execute(
        sa.select(Team.id, Team.name, TeamMember.role)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user.id)
        .order_by(Team.name)
    ).all()

    log.info("audio_my_data_exported", user_id=user.id, meetings=len(meetings))
    return {
        "user": {
            "user_id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "created_at": user.created_at.isoformat(),
        },
        "teams": [{"team_id": t, "name": n, "role": r} for t, n, r in teams],
        "meetings": list(meetings.values()),
        "voice_profile": [
            {
                "confirmed_at": created.isoformat(),
                "source_meeting_id": source,
                "model_version": model,
            }
            for created, source, model in profiles
        ],
        "consents_attested": [
            {"meeting_id": m, "attested_at": at.isoformat()} for m, at in consents
        ],
    }


def _delete_utterances(session: Session, participant_ids: list[str]) -> int:
    if not participant_ids:
        return 0
    result = session.execute(
        sa.delete(Utterance)
        .where(Utterance.participant_id.in_(participant_ids))
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0))


def delete_my_speech(session: Session, *, user: User) -> SpeechDeleted:
    """Delete every utterance attributed to ``user`` and every vector of their voice.

    An utterance is theirs when its participant row names them, which is what
    ``assign_speaker`` writes when somebody confirms who a speaker is. A
    speaker nobody ever identified is not attributable to anyone, and there is
    nothing this can find.

    The voice goes through ``delete_voice_profile``, so profile rows and the
    meeting observations still attributable to them both go -- the same
    "deleted means the vector that matters most, too" that route already
    promises. It runs first because it, too, finds observations through the
    participant rows.
    """
    voice_rows = delete_voice_profile(session, user=user)
    utterances = _delete_utterances(session, list(session.scalars(_my_participant_ids(user.id))))
    session.flush()
    log.info("audio_my_speech_deleted", user_id=user.id, utterances=utterances)
    return SpeechDeleted(utterances=utterances, voice_rows=voice_rows)


def delete_account(session: Session, *, user: User) -> AccountDeleted:
    """Delete ``user`` and everything that is theirs (#358).

    **The hooks run first, and a hook that raises stops the deletion.** A
    module's hook is the only thing that can find that module's per-person
    state, and it finds it by ``user_id``; deleting the ``users`` row after a
    hook failed would leave those rows with no account to be found through,
    which is a deletion nobody can finish. Stopping leaves the account in
    place and the request failed, so the person can try again -- every hook
    is a set of ``DELETE``/``UPDATE ... WHERE user_id = ...`` statements and
    running one twice is harmless. That is the answer to #358's "does
    deletion stop, or continue and report?": it stops, because a reported
    partial deletion is still a partial deletion.

    **Before anything in ``session``, not after.** Each hook opens and commits
    its own session (it takes only an id), so a row this transaction had
    already locked -- a participant row, say -- would leave the hook waiting
    on a lock held by the request that is waiting on the hook.

    The ``users`` row goes through a Core ``DELETE`` rather than
    ``session.delete``: the ORM would load ``User.memberships`` and null
    ``team_members.user_id`` first, which is ``NOT NULL`` (#355), where the
    database's own ``ON DELETE CASCADE`` does the right thing. The voice is
    the audio hook's (``forget_user_voice``), so it is not deleted twice here.

    **Which utterances are theirs is read before the hooks run.** A's own hook
    (``service.forget_user_voice``) clears ``participants.user_id``, and that
    column is the only thing that attributes an utterance to a person -- read
    afterwards, it would find nothing and leave every word they said behind.
    A plain ``SELECT`` takes no lock, so reading first does not reopen the
    wait described above.
    """
    user_id = user.id
    participant_ids = list(session.scalars(_my_participant_ids(user_id)))
    run_user_hooks(user_id)
    utterances = _delete_utterances(session, participant_ids)
    session.execute(sa.delete(User).where(User.id == user_id))
    session.flush()
    log.info("audio_account_deleted", user_id=user_id, utterances=utterances)
    return AccountDeleted(utterances=utterances)


def team_privacy(session: Session, *, team_id: str, reader: User) -> TeamPrivacy:
    require_team_member(session, user_id=reader.id, team_id=team_id)
    team = session.get(Team, team_id)
    if team is None:
        raise NotFoundError("team", team_id)
    return TeamPrivacy(team_id=team.id, retention_days=team.retention_days)


def set_retention(session: Session, *, team_id: str, days: int, by: User) -> TeamPrivacy:
    """Change the window for meetings the team holds from now on.

    **Not retroactive.** ``service.open_retention_window`` fixes each
    meeting's ``expires_at`` when it is held, and that is a promise made to the
    people in that room: lengthening the window later must not keep what they
    were told would go, and shortening it must not delete what the team was
    still relying on without anyone deciding to.

    Any member may change it. S29 labels the section "관리자", but there is no
    admin role in ``team_members`` yet -- a member is the narrowest check the
    schema can express. Logged with who changed it, which is the audit line
    S29 asks for.
    """
    require_team_member(session, user_id=by.id, team_id=team_id)
    team = session.get(Team, team_id, with_for_update=True)
    if team is None:
        raise NotFoundError("team", team_id)
    before = team.retention_days
    team.retention_days = days
    session.flush()
    log.info("audio_team_retention_changed", team_id=team_id, by=by.id, before=before, after=days)
    return TeamPrivacy(team_id=team.id, retention_days=team.retention_days)
