"""Per-person integration credentials -- a person's own calendar or mailbox.

The per-team store (``integrations_config``) holds what a team connects once
for everybody: a Notion workspace, a Slack workspace. This one holds what only
a person can grant and only for themselves (#59, #435, #431). The shape is the
same on purpose -- load, save, disconnect, a secret that is decrypted only on
the way out -- so a caller that knows one knows the other.

Read by the module acting *for that user*, never to show one person's
connection or data to another. Written by the settings layer; until S28's
OAuth flow exists (#428) the local-only dev route is the one exception (#401).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from .crypto import decrypt, encrypt
from .entities import UserIntegration
from .errors import ValidationError
from .logging import get_logger

log = get_logger(__name__)

USER_SERVICES: Final = ("calendar", "slack", "gmail_send", "drive")
"""Kept in step with the check constraint on ``user_integrations.service``.
``slack`` is a person's own Slack identity for direct messages (#255);
``gmail_send`` is a grant to send mail as the person and nothing else (#552);
``drive`` is a grant to read the Drive files the person picks, and no others
(``drive.file``, #817).
``gmail`` -- reading a mailbox -- is added with #431's decision, not before it."""

GOOGLE_SERVICES: Final = ("calendar", "gmail_send", "drive")
"""The personal grants that are Google refresh tokens, revoked at Google when
the account is deleted (``revoke_google_grants``): the calendar and, since
#760, the grant to send mail."""


@dataclass(frozen=True)
class UserIntegrationConfig:
    """One person's settings for one service, secret decrypted. Build it, make
    the call, drop it; ``repr`` never shows the secret."""

    service: str
    user_id: str
    secret: str | None = None
    config: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        held = "set" if self.secret else "unset"
        return (
            f"UserIntegrationConfig(service={self.service!r}, user_id={self.user_id!r}, "
            f"secret=<{held}>, config_keys={sorted(self.config)})"
        )


def _row(session: Session, user_id: str, service: str) -> UserIntegration | None:
    _check_service(service)
    return session.scalars(
        select(UserIntegration).where(
            UserIntegration.user_id == user_id, UserIntegration.service == service
        )
    ).one_or_none()


def load_user_integration(
    session: Session, user_id: str, service: str
) -> UserIntegrationConfig | None:
    """This person's configuration for ``service``, or None if they never
    connected it -- the ordinary answer, so callers skip rather than fail.

    **Call it only for the person the work is for.** Any ``user_id`` is
    accepted; that one person's grant is used only on that person's behalf --
    their own tasks on their own calendar -- is the caller's rule to keep, the
    same as privacy.md section 3 keeps a speaking ratio with its speaker."""
    row = _row(session, user_id, service)
    if row is None:
        return None
    return UserIntegrationConfig(
        service=row.service,
        user_id=row.user_id,
        secret=decrypt(row.secret) if row.secret else None,
        config=dict(row.config or {}),
    )


def save_user_integration(
    session: Session,
    user_id: str,
    service: str,
    *,
    secret: str | None = None,
    config: dict[str, Any] | None = None,
) -> None:
    """Create or update one person's settings for one service. ``secret`` is
    encrypted here; ``config`` is stored as written, so a credential never goes
    in it. ``None`` leaves the stored secret alone -- what changing which
    calendar is used does."""
    row = _row(session, user_id, service)
    if row is None:
        row = UserIntegration(user_id=user_id, service=service, config={})
        session.add(row)
    if secret is not None:
        row.secret = encrypt(secret)
    if config is not None:
        row.config = config


def disconnect_user_integration(session: Session, user_id: str, service: str) -> None:
    """Remove a person's connection, credential and all -- here.

    It does not revoke the grant at the provider; the caller does that first.
    ``POST /api/auth/google/calendar/disconnect`` revokes at Google, then calls
    this."""
    row = _row(session, user_id, service)
    if row is not None:
        session.delete(row)


def revoke_google_grants(user_id: str) -> None:
    """End this person's Google grants at Google, before their account and
    its ``user_integrations`` rows go (#763).

    The rows cascade with ``users``, which leaves no copy of the token here,
    but the grant stays valid at Google -- and Autune stays listed under the
    person's third-party access -- until they remove it there by hand. This
    ends it from our side.

    **After every module's user hook** (``deletion.run_user_hooks``): B
    removes the due dates it put on that calendar with this same grant, and
    a revoked grant could not.

    **Once per Google account and client.** Revoking one refresh token ends
    everything that account granted that client, so a second grant with the
    same ``google_sub`` and ``client_id`` is already gone; revoking it again
    would only log a refusal. A grant saved before either was recorded is
    revoked on its own.

    **Never raises, never waits on Google past its timeout.** An account
    deletion does not stop because Google is unreachable or refuses (the
    grant may already be revoked, or have expired); that is logged by
    service and outcome, never with the token, and the deletion goes on, as
    the calendar cleanup does (privacy.md section 4). Safe to run twice: a
    second run finds the grant already revoked, logs it, and changes nothing.

    **Before the account's own ``DELETE`` commits.** If that delete then
    fails, the account stays with its grants revoked at Google; the person's
    connections then ask to be connected again, and deleting again finishes
    the job.
    """
    from .db import session_scope
    from .oauth.google import revoke_token

    grants: list[UserIntegrationConfig] = []
    for service in GOOGLE_SERVICES:
        # One try per service: a row that will not decrypt must not keep the
        # others from being revoked (mkkim68, review of #766).
        try:
            with session_scope() as session:
                grant = load_user_integration(session, user_id, service)
        except Exception as exc:  # noqa: BLE001 -- see the docstring
            log.warning(
                "user_google_grant_unreadable",
                user_id=user_id,
                service=service,
                error=type(exc).__name__,
            )
            continue
        if grant is not None and grant.secret:
            grants.append(grant)

    done: set[tuple[str, str]] = set()
    for grant in grants:
        account, client = grant.config.get("google_sub"), grant.config.get("client_id")
        key = (str(account), str(client)) if account and client else None
        if key is not None and key in done:
            log.info(
                "user_google_grant_revoked", user_id=user_id, service=grant.service, shared=True
            )
            continue
        assert grant.secret is not None
        try:
            revoked = revoke_token(grant.secret)
        except Exception as exc:  # noqa: BLE001 -- the right to delete comes first
            log.warning(
                "user_google_grant_not_revoked",
                user_id=user_id,
                service=grant.service,
                error=type(exc).__name__,
            )
            continue
        if key is not None and revoked:
            done.add(key)
        log.info(
            "user_google_grant_revoked" if revoked else "user_google_grant_not_revoked",
            user_id=user_id,
            service=grant.service,
        )


def users_with_integration(session: Session, service: str) -> list[str]:
    """The ids of everyone who has connected ``service`` -- for a periodic task
    that works through connected people one at a time, each with their own
    credential."""
    _check_service(service)
    return list(
        session.scalars(
            select(UserIntegration.user_id)
            .where(UserIntegration.service == service, UserIntegration.secret.is_not(None))
            .order_by(UserIntegration.user_id)
        )
    )


def users_linked_to_slack_member(session: Session, member_id: str) -> list[str]:
    """Autune users whose linked Slack account is ``member_id``. One Slack
    person is one Autune person: a second link to the same member id is a
    shared browser's leftover Slack session, not a second owner (#478)."""
    return list(
        session.scalars(
            select(UserIntegration.user_id).where(
                UserIntegration.service == "slack",
                UserIntegration.config["slack_user_id"].as_string() == member_id,
            )
        )
    )


def _check_service(service: str) -> None:
    if service not in USER_SERVICES:
        raise ValidationError(
            f"unknown personal integration {service!r}; expected one of {', '.join(USER_SERVICES)}",
            field="service",
        )


def slack_member_id(user_id: str) -> str | None:
    """The Slack member id a person linked for direct messages (#255), or
    ``None`` when they have not linked one. Its own short session: the Slack
    client asks at the moment it addresses a DM, from inside whatever the
    caller is doing."""
    from .db import session_scope

    with session_scope() as session:
        linked = load_user_integration(session, user_id, "slack")
    member = linked.config.get("slack_user_id") if linked is not None else None
    return str(member) if member else None
