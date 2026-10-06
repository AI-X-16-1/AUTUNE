"""Team invitations the invitee accepts (#552).

Team membership is the read boundary for every module's team-level data, so a
person joins a team only by their own act. The first S02 made invited
addresses members on the spot, and review of #539 showed what that gave away;
this is the replacement. Scope and rules are the module owner's, on #552.

**A pending invitation is not a membership.** It is a row in
``aud_team_invitations``: a team, an address, who invited, when it lapses.
Nothing reads it as a member, so until it is accepted the invited person's
teams, their default team and what they can open are exactly what they were.

**Creating one looks nobody up.** ``invite`` never reads ``users``: the answer
has the same shape for an address with an account, without one, or already on
the team, so the invitation route cannot be used to learn whether an address
has signed up or what its owner is called. No ``users`` row is made for it
either -- an address that never signs up is held only in the invitation, and
that lapses.

**The link is the only way in, and only for the invited address.** The token
is ``secrets.token_urlsafe(32)``, shown once in the creating response; only
its SHA-256 is stored, so the table cannot be turned back into links. Accepting
needs a signed-in account whose email is the invited address, compared without
case.

**Every refusal is one answer.** An unknown token, a used one, an expired one
and one opened by another account all get ``InvitationUnusableError`` with the
same status and the same words. The reason goes to the log, by id.

**The address is somebody's personal data, held before they agreed to
anything.** It goes when the invitation is accepted, when it lapses (the
retention sweep, and the next invitation made for that team), when it is
replaced by a new invitation to the same address, when a member of the team
cancels it (``cancel``), when the inviter leaves the team
(``service.leave_team``: nobody joins a team on the word of somebody who is
no longer on it), when the team or the inviter's account is deleted
(``ON DELETE CASCADE``), and when the invited person deletes their own
account (``forget_address``). Log lines carry ids only: never the address,
the token or its hash.

**A team's members can see what is pending and take it back** (``pending``,
``cancel``). The list is the address, when the link lapses and who invited:
what the inviter typed and the team already holds. It still looks nobody up
-- an address with an account and one without read the same -- and never
carries the token or its hash, so a link cannot be rebuilt from it. Who may
see the addresses and who may cancel is the module owner's to settle on
#552; as built, any member of the team may do both.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autune_core import Team, TeamMember, User, get_logger
from autune_core.errors import AutuneError, ConflictError

from .models import AudTeamInvitation
from .service import require_team_member

log = get_logger(__name__)

TOKEN_BYTES = 32
"""256 bits from the OS. ``token_urlsafe`` writes them as 43 characters."""

LIFETIME = timedelta(days=7)
"""How long a link works. Not set on #552; proposed in the PR and ours to change."""

MAX_PENDING = 50
"""Pending invitations one team may hold. A bound on how many third-party
addresses a team can park here, not a product limit anybody should meet."""


class InvitationUnusableError(AutuneError):
    """The one answer for every invitation that cannot be accepted.

    Unknown, already used, expired, or addressed to another account: the
    caller is told none of those, because each is something about an
    invitation -- or about an address -- that they may not be entitled to know.
    """

    code = "invitation_unusable"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("this invitation cannot be used")


def normalise(email: str) -> str:
    """An address as it is stored and compared: trimmed, lower-cased."""
    return email.strip().lower()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def invite(
    session: Session, *, team_id: str, email: str, by: User, now: datetime | None = None
) -> tuple[str, datetime]:
    """Make a pending invitation; the token for its link, and when it lapses.

    Only a member of the team may invite to it. A second invitation to the same
    address replaces the first, so an address has one live link per team and
    the earlier one stops working.
    """
    require_team_member(session, user_id=by.id, team_id=team_id)
    now = now or datetime.now(tz=UTC)
    address = normalise(email)

    session.execute(
        sa.delete(AudTeamInvitation).where(
            AudTeamInvitation.team_id == team_id,
            sa.or_(AudTeamInvitation.expires_at <= now, AudTeamInvitation.email == address),
        )
    )
    pending = session.scalar(
        sa.select(sa.func.count())
        .select_from(AudTeamInvitation)
        .where(AudTeamInvitation.team_id == team_id)
    )
    if (pending or 0) >= MAX_PENDING:
        raise ConflictError("this team has too many pending invitations")

    token = secrets.token_urlsafe(TOKEN_BYTES)
    row = AudTeamInvitation(
        team_id=team_id,
        email=address,
        token_hash=_digest(token),
        invited_by=by.id,
        expires_at=now + LIFETIME,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError as exc:
        # Two requests inviting the same address at once: the other one won.
        raise ConflictError("this invitation could not be made; try again") from exc
    log.info("team_invitation_created", team_id=team_id, invitation_id=row.id, invited_by=by.id)
    return token, row.expires_at


def accept(session: Session, *, token: str, user: User, now: datetime | None = None) -> Team:
    """Put ``user`` on the team the token invites them to, and use the invitation up.

    The membership is written here and nowhere else in this file. Someone who
    is already on the team succeeds without a second row.
    """
    now = now or datetime.now(tz=UTC)
    row = session.scalar(
        sa.select(AudTeamInvitation)
        .where(AudTeamInvitation.token_hash == _digest(token))
        .with_for_update()
    )
    reason = (
        "unknown"
        if row is None
        else "expired"
        if row.expires_at <= now
        else "other_account"
        if row.email != normalise(user.email)
        else None
    )
    team = session.get(Team, row.team_id) if row is not None and reason is None else None
    if row is None or team is None:
        log.info(
            "team_invitation_refused",
            user_id=user.id,
            invitation_id=row.id if row is not None else None,
            reason=reason or "team_gone",
        )
        raise InvitationUnusableError

    already = session.scalar(
        sa.select(TeamMember.id).where(TeamMember.team_id == team.id, TeamMember.user_id == user.id)
    )
    if already is None:
        session.add(TeamMember(team_id=team.id, user_id=user.id))
    invitation_id = row.id
    session.delete(row)
    session.flush()
    log.info(
        "team_invitation_accepted",
        team_id=team.id,
        user_id=user.id,
        invitation_id=invitation_id,
        already_member=already is not None,
    )
    return team


def pending(
    session: Session, *, team_id: str, reader: User, now: datetime | None = None
) -> list[tuple[AudTeamInvitation, str | None]]:
    """The team's invitations nobody has accepted yet, with the inviter's name:
    the one lapsing soonest first. Members of the team only.

    A lapsed one is not listed even before the sweep has removed it: its link
    no longer works, and a list that showed it would say otherwise.
    """
    require_team_member(session, user_id=reader.id, team_id=team_id)
    now = now or datetime.now(tz=UTC)
    return [
        (row, name)
        for row, name in session.execute(
            sa.select(AudTeamInvitation, User.display_name)
            .outerjoin(User, User.id == AudTeamInvitation.invited_by)
            .where(AudTeamInvitation.team_id == team_id, AudTeamInvitation.expires_at > now)
            .order_by(AudTeamInvitation.expires_at, AudTeamInvitation.id)
        )
    ]


def cancel(session: Session, *, team_id: str, invitation_id: int, by: User) -> bool:
    """Take a pending invitation back: the row goes, and its link with it.

    Members of the team only. One that is no longer there -- accepted, lapsed,
    cancelled by somebody else a moment ago, or never this team's -- is not an
    error and not a different answer: the caller wanted it gone and it is, and
    an id that belongs to another team says nothing about that team.
    """
    require_team_member(session, user_id=by.id, team_id=team_id)
    result = session.execute(
        sa.delete(AudTeamInvitation).where(
            AudTeamInvitation.id == invitation_id, AudTeamInvitation.team_id == team_id
        )
    )
    gone = int(getattr(result, "rowcount", 0)) > 0
    if gone:
        log.info(
            "team_invitation_cancelled",
            team_id=team_id,
            invitation_id=invitation_id,
            cancelled_by=by.id,
        )
    return gone


def forget_expired(session: Session, *, now: datetime) -> int:
    """Delete every invitation that has lapsed. The retention sweep calls this."""
    result = session.execute(
        sa.delete(AudTeamInvitation).where(AudTeamInvitation.expires_at <= now)
    )
    return int(getattr(result, "rowcount", 0))


def forget_address(session: Session, *, email: str) -> int:
    """Delete every invitation addressed to ``email`` -- for the account being
    deleted. The ones a person *sent* go with their ``users`` row."""
    result = session.execute(
        sa.delete(AudTeamInvitation).where(AudTeamInvitation.email == normalise(email))
    )
    return int(getattr(result, "rowcount", 0))
