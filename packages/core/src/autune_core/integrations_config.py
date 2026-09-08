"""Per-team integration configuration.

Every module that talks to an outside service asks here for the credentials,
rather than reading an environment variable. The difference matters: a token in
the environment belongs to the deployment, and a product with more than one
customer team needs the token to belong to the team.

Modules read. Writing is the settings layer's job (screen S28), the same way
``users`` and ``teams`` are written by the auth layer and not by a module.
See docs/architecture/data-model.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from .crypto import decrypt, encrypt
from .entities import TeamIntegration
from .errors import NotFoundError, ValidationError

SERVICES: Final = ("notion", "jira", "slack", "calendar")
"""Kept in step with the check constraint on ``team_integrations.service``."""


@dataclass(frozen=True)
class IntegrationConfig:
    """One team's settings for one service, with the secret already decrypted.

    Short-lived by intention: build it, make the call, drop it. Do not cache it
    on a module-level object, and never log it — ``repr`` is overridden so an
    accidental f-string does not publish the token.
    """

    service: str
    team_id: str
    secret: str | None = None
    config: dict[str, Any] = field(default_factory=dict)

    def require_secret(self) -> str:
        """The token, or a clear error naming what the team has not connected."""
        if not self.secret:
            raise ValidationError(
                f"team {self.team_id} has no credential stored for {self.service}",
                field="secret",
            )
        return self.secret

    def require(self, key: str) -> Any:
        """One config value, or a clear error rather than a KeyError at call time."""
        if key not in self.config:
            raise ValidationError(
                f"{self.service} is connected for team {self.team_id} but '{key}' is "
                "not configured",
                field=key,
            )
        return self.config[key]

    def __repr__(self) -> str:
        held = "set" if self.secret else "unset"
        return (
            f"IntegrationConfig(service={self.service!r}, team_id={self.team_id!r}, "
            f"secret=<{held}>, config_keys={sorted(self.config)})"
        )


def load_integration(session: Session, team_id: str, service: str) -> IntegrationConfig | None:
    """This team's configuration for ``service``, or None if never connected.

    None is the ordinary answer for a team that has not set the service up, so
    callers should skip the feature rather than fail the task.
    """
    _check_service(service)
    row = session.scalars(
        select(TeamIntegration).where(
            TeamIntegration.team_id == team_id, TeamIntegration.service == service
        )
    ).one_or_none()
    if row is None:
        return None
    return IntegrationConfig(
        service=row.service,
        team_id=row.team_id,
        secret=decrypt(row.secret) if row.secret else None,
        config=dict(row.config or {}),
    )


def require_integration(session: Session, team_id: str, service: str) -> IntegrationConfig:
    """Like :func:`load_integration`, for a path that cannot proceed without it."""
    config = load_integration(session, team_id, service)
    if config is None:
        raise NotFoundError("integration", f"{service} for team {team_id}")
    return config


def save_integration(
    session: Session,
    team_id: str,
    service: str,
    *,
    secret: str | None = None,
    config: dict[str, Any] | None = None,
    connected_by: str | None = None,
) -> None:
    """Create or update one team's settings for one service.

    ``secret`` is encrypted here, so callers pass the plaintext token and no
    caller has to remember to encrypt. Passing ``None`` leaves the stored secret
    alone — that is what saving a changed property mapping does.
    """
    _check_service(service)
    row = session.scalars(
        select(TeamIntegration).where(
            TeamIntegration.team_id == team_id, TeamIntegration.service == service
        )
    ).one_or_none()

    if row is None:
        row = TeamIntegration(team_id=team_id, service=service, config={})
        session.add(row)

    if secret is not None:
        row.secret = encrypt(secret)
    if config is not None:
        row.config = config
    if connected_by is not None:
        row.connected_by = connected_by


def disconnect_integration(session: Session, team_id: str, service: str) -> None:
    """Remove a team's connection. The credential goes with it, not just a flag."""
    _check_service(service)
    row = session.scalars(
        select(TeamIntegration).where(
            TeamIntegration.team_id == team_id, TeamIntegration.service == service
        )
    ).one_or_none()
    if row is not None:
        session.delete(row)


def _check_service(service: str) -> None:
    if service not in SERVICES:
        raise ValidationError(
            f"unknown integration service {service!r}; expected one of {', '.join(SERVICES)}",
            field="service",
        )
