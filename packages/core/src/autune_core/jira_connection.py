"""A team's Jira connection: an access token on demand, the rotation handled.

Atlassian refresh tokens rotate -- each refresh returns a new one and retires
the old. A module that refreshed inside its own transaction and then failed
would roll the new token back and be left holding a dead one, and the team's
Jira would be gone for good. So ``jira_access`` refreshes in **its own**
transaction, holding the team's row lock so two workers cannot rotate at once,
and commits the new token before returning. The caller gets a short-lived
access token and never sees the refresh token.

Written by core, read by modules: this is the settings layer's table
(``team_integrations``), and keeping its token current is the settings layer's
job, not the module's (data-model.md).
"""

from __future__ import annotations

from sqlalchemy import select

from .crypto import decrypt, encrypt
from .db import session_scope
from .entities import TeamIntegration
from .logging import get_logger
from .oauth.atlassian import AtlassianOAuthClient, JiraReconnectRequiredError, get_atlassian_client

log = get_logger(__name__)

JIRA = "jira"


class JiraAccess:
    """What a module needs for one run of calls: never the refresh token."""

    __slots__ = ("access_token", "cloud_id", "project_key")

    def __init__(self, access_token: str, cloud_id: str, project_key: str | None) -> None:
        self.access_token = access_token
        self.cloud_id = cloud_id
        self.project_key = project_key

    def __repr__(self) -> str:
        return f"JiraAccess(cloud_id={self.cloud_id!r}, project_key={self.project_key!r})"


def jira_access(
    team_id: str, *, client: AtlassianOAuthClient | None = None, check_project: bool = False
) -> JiraAccess | None:
    """A fresh access token for the team's Jira, or ``None`` when the team has
    not connected Jira or its connection needs a person to reconnect.

    A refused refresh token marks the connection ``needs_reconnect`` (committed)
    and raises ``JiraReconnectRequiredError`` once; later calls return ``None`` until
    someone connects again, so a dead grant is not retried on every edit.

    ``check_project`` also confirms the chosen project still exists. One that
    was deleted is recorded as ``project_missing`` (its key) with no project
    chosen, and the answer carries ``project_key=None``: the module skips, and
    the screen asks for a new project -- whose choice brings every confirmed
    item back (module B's backfill). Nothing here creates a project: that
    needs Jira's admin scope, which this connection does not ask for.
    """
    with session_scope() as session:
        row = session.scalars(
            select(TeamIntegration)
            .where(TeamIntegration.team_id == team_id, TeamIntegration.service == JIRA)
            .with_for_update()
        ).one_or_none()
        if row is None or not row.secret:
            return None
        config = dict(row.config or {})
        if config.get("needs_reconnect") or not config.get("cloud_id"):
            return None
        atlassian = client or get_atlassian_client()
        try:
            tokens = atlassian.refresh(decrypt(row.secret))
        except JiraReconnectRequiredError:
            row.config = {**config, "needs_reconnect": True}
            log.warning("jira_needs_reconnect", team_id=team_id)
            session.commit()
            raise
        if tokens.refresh_token:
            row.secret = encrypt(tokens.refresh_token)
        project_key = config.get("project_key")
        cloud_id = str(config["cloud_id"])
        if (
            check_project
            and project_key
            and not atlassian.project_exists(tokens.access_token, cloud_id, project_key)
        ):
            row.config = {**config, "project_key": None, "project_missing": project_key}
            log.warning("jira_project_missing", team_id=team_id)
            project_key = None
        # Committed by session_scope on the way out, before the caller's calls.
        return JiraAccess(
            access_token=tokens.access_token, cloud_id=cloud_id, project_key=project_key
        )
