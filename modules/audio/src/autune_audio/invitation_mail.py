"""An invitation link mailed from the inviter's own Gmail (#552).

The link is the invitation; mail is one way to hand it over, and the copy
button stays the other. Nothing here changes what an invitation is or who can
accept it (``invitations``).

**From the inviter, as the inviter.** The message goes out through the
inviter's own ``gmail_send`` grant (``autune_core.user_integrations``), so it
comes from their address, as a link they would have pasted themselves would.
Autune runs no mail server and holds no shared sender.

**The answer says nothing about the address.** ``send`` returns whether Gmail
took the message, which depends on the inviter's grant and on Gmail, never on
whether the invited address has an account here. Gmail takes any well-formed
address and bounces later, to the inviter's own mailbox.

**The token stays in this request.** The link is built and sent here, inside
the request that made it: it is never put in a Celery payload, a log line or
an exception message, and only its hash is stored.

**A failure is not an error.** The invitation exists either way and its link
is in the answer; the screen says the mail did not go and offers the link.
Why it did not go is logged by id and reason, never with the address.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from autune_core import Team, User, get_logger
from autune_core.errors import PrivacyViolationError
from autune_core.settings import get_settings as get_core_settings
from autune_core.user_integrations import load_user_integration
from autune_integrations import GmailClient, IntegrationError, refresh_access_token

log = get_logger(__name__)

GMAIL_SEND = "gmail_send"

SEOUL = ZoneInfo("Asia/Seoul")
"""The team's working time zone; the expiry date in the message is read there."""


def invitation_link(token: str) -> str:
    """The same link the screen builds (``invitationLink.ts``): the token in the
    fragment, so it reaches no server log on the way in."""
    return f"{get_core_settings().web_base_url.rstrip('/')}/invite#{token}"


def compose(*, inviter: str, team: str, link: str, expires_at: datetime) -> tuple[str, str]:
    """Subject and plain-text body. Names the inviter and the team, never the
    invited address -- the recipient knows their own."""
    until = expires_at.astimezone(SEOUL).strftime("%Y-%m-%d")
    subject = f"{inviter} 님이 Autune 팀 '{team}'에 초대했습니다"
    body = (
        f"{inviter} 님이 Autune 팀 '{team}'에 초대했습니다.\n"
        "\n"
        "아래 링크를 열고, 이 메일을 받은 주소의 Google 계정으로 로그인한 뒤 수락해 주세요.\n"
        f"{link}\n"
        "\n"
        f"링크는 {until}까지 쓸 수 있습니다. 수락하기 전에는 팀원이 되지 않습니다.\n"
        "초대받을 일이 없었다면 이 메일은 무시하셔도 됩니다.\n"
    )
    return subject, body


def send(
    session: Session, *, team_id: str, email: str, token: str, expires_at: datetime, by: User
) -> bool:
    """Mail the invitation link from ``by``'s own Gmail; whether Gmail took it."""
    reason = _send(session, team_id=team_id, email=email, token=token, expires_at=expires_at, by=by)
    if reason is None:
        log.info("team_invitation_mailed", team_id=team_id, invited_by=by.id)
        return True
    log.info("team_invitation_not_mailed", team_id=team_id, invited_by=by.id, reason=reason)
    return False


def _send(
    session: Session, *, team_id: str, email: str, token: str, expires_at: datetime, by: User
) -> str | None:
    """``None`` once Gmail took the message, else why not -- a word for the log."""
    grant = load_user_integration(session, by.id, GMAIL_SEND)
    if grant is None or not grant.secret:
        return "not_connected"
    client_id, client_secret = get_core_settings().google_integration_credentials
    if not (client_id and client_secret):
        return "no_google_client"
    issued_to = grant.config.get("client_id")
    if issued_to and issued_to != client_id:
        # Known without asking Google: a refresh with another client's token is
        # refused (``autune_extraction.tasks._calendars``, review of #700).
        return "reconnect_required"
    team = session.get(Team, team_id)
    if team is None:
        return "team_gone"

    link = invitation_link(token)
    subject, body = compose(
        inviter=by.display_name or "팀원", team=team.name, link=link, expires_at=expires_at
    )
    try:
        access = refresh_access_token(
            client_id=client_id, client_secret=client_secret, refresh_token=grant.secret
        )
        client = GmailClient(access)
        try:
            client.send(to=email, subject=subject, body=body, unchecked=[link])
        finally:
            client.close()
    except IntegrationError as exc:
        return exc.code
    except PrivacyViolationError:
        # The team's name or the inviter's carries something that reads as
        # personal data; the guard refused before anything left.
        return "privacy_guard"
    except ValueError:
        # A line break in the team's name, which cannot go in a header.
        return "unsendable"
    return None
