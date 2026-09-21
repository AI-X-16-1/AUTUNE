"""Business logic for module A: Audio Pipeline.

Owner: 김민경. See docs/modules/audio.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``aud_*`` tables.
Never imports another module.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_contracts.transcript import Utterance as ContractUtterance
from autune_core import Meeting, Participant, TeamMember, User, get_logger
from autune_core.errors import NotFoundError, PermissionDeniedError

from .models import AudConsentAttestation
from .persistence import transcript_payload

log = get_logger(__name__)


def require_team_member(session: Session, *, user_id: str, team_id: str) -> None:
    """Raise unless ``user_id`` belongs to ``team_id``.

    A token proves who is asking, not which team's meetings they may read. Named
    and shared rather than written inline, because every route this module grows
    asks the same question and an authorisation check that exists in two places
    is one that can come to mean two things.
    """
    member = session.scalar(
        sa.select(TeamMember.id).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
    )
    if member is None:
        raise PermissionDeniedError("you are not a member of this team")


def transcript_for_meeting(
    session: Session, *, meeting_id: str, reader: User
) -> list[ContractUtterance]:
    """One meeting's transcript, for the person asking to read it.

    **The same builder the published event uses.** ``transcript_payload`` shapes
    what goes out on ``TranscriptReady``; taking its ``utterances`` rather than
    querying the rows again means the screen and the four consuming modules
    cannot come to disagree about what was said, or about the order it was said
    in. Two readers of the same rows is how the masker and the outbound guard
    drifted apart in #126.

    **Masked, because that is what is stored.** There is no unmasked column to
    read and no flag that returns one — module A masks before the first write
    (privacy.md section 2) — so this returns what the database has and the screen
    marks the masked spans rather than offering to reveal them.

    **Empty while the task is still running**, which is honest rather than a
    special case: ``persist_transcript`` writes every utterance in one
    transaction at the end, so a meeting part-way through has none rather than
    some. A caller that needs to tell "not yet" from "nothing was said" reads
    the meeting's status, which is what it is for.

    Speaking ratios are not here and never will be. privacy.md section 3 gives
    those to module E, delivered to the speaker and nobody else.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    require_team_member(session, user_id=reader.id, team_id=meeting.team_id)

    # Ids only. A transcript is meeting content and a log line is a store.
    log.info("audio_transcript_read", meeting_id=meeting_id, reader_id=reader.id)
    return transcript_payload(session, meeting_id=meeting_id).utterances


def attest_consent(session: Session, *, meeting_id: str, attested_by: User) -> None:
    """Record that everyone in this meeting's recording consented, on the
    word of a member of its team.

    **The only path in the repository to ``participants.consented = True``**
    (#190). Consent is a statement about people, and before identification (#6)
    a participant row is a voice, not a person -- so there is nowhere to write
    a per-person answer, and the one honest statement available is a team
    member's about the whole meeting. Any member of the team, not necessarily
    the one who uploaded and not necessarily one who was there: ``meetings``
    has no ``created_by`` to narrow it, so the check is membership and the
    limit is documented. This records that statement and applies
    it: every participant row the meeting has *now* is set True, and
    ``persistence._participants_for`` reads the attestation for every row it
    creates *later*, so a rerun that invents a label the first run never saw
    gets the same value. Module B's condition on #190, and it is pinned by
    ``test_a_rerun_gives_a_new_label_the_same_consent``.

    Idempotent: a second call finds the row and changes nothing but the
    participant flags, which were already True. A reload is not a second
    statement.

    Not per person, not revocable, and it does not tell B and C that a meeting
    they already analysed has changed. Those are S10, S11 and #190's republish
    question. Nor does anything here undo what B, C and E derived once the
    meeting was analysed -- today the only way that data goes is with the
    meeting itself (CASCADE), and before identification (#6) there is no
    per-person unit to revoke for. The default is not loosened by any of
    this: a meeting with no attestation is exactly as it was.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    require_team_member(session, user_id=attested_by.id, team_id=meeting.team_id)

    if session.get(AudConsentAttestation, meeting_id) is None:
        session.add(AudConsentAttestation(meeting_id=meeting_id, attested_by=attested_by.id))

    result = session.execute(
        sa.update(Participant)
        .where(Participant.meeting_id == meeting_id, Participant.consented.is_(False))
        .values(consented=True)
    )
    updated = int(getattr(result, "rowcount", 0))
    session.flush()
    # Counts and ids only. Who attested is in the row; the log says it happened.
    log.info(
        "consent_attested_by_member",
        meeting_id=meeting_id,
        attested_by=attested_by.id,
        participants_updated=updated,
    )
