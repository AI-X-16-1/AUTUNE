"""The open issues of a team's Jira project, read to be shown.

Decided with the user, 2026-10-02: what was already in Jira before Autune is
*viewed* here, not imported. Nothing below writes a row. An issue's title, its
status, its assignee's name and its due date go from Jira to the response and
nowhere else -- no table, no log line -- so nothing of the team's Jira is
Autune's to retain, expire or delete.

Read with the team's own connection, which is the grant of the person who
connected it: a member of the team sees these issues whether or not they have
a Jira account of their own. That is the team's choice when it connects a
project, and ``docs/architecture/privacy.md`` says so.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_core import Team, get_logger, jira_access, load_integration
from autune_core.errors import AutuneError, PrivacyViolationError
from autune_core.oauth.atlassian import JiraReconnectRequiredError
from autune_integrations import IntegrationError, JiraClient

from .jira_sync import JIRA
from .models import ExtExternalRef
from .schemas import JiraIssueRead, JiraProjectIssues

log = get_logger(__name__)

ISSUE_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]*-[0-9]+")
"""What goes into a link. A key that is not one gets no link rather than a
path built from whatever the answer carried."""


def project_issues(session: Session, team_id: str) -> JiraProjectIssues | None:
    """The team's open issues, or ``None`` for a team that never connected Jira.

    A connection that cannot answer is said so in ``state`` rather than raised:
    the screen lists every team the reader is on, and one team's dead grant
    must not hide another's issues. The caller has checked the reader.
    """
    config = load_integration(session, team_id, JIRA)
    if config is None:
        return None
    settings = config.config or {}
    name = session.scalar(select(Team.name).where(Team.id == team_id)) or ""
    project_key = settings.get("project_key") or None

    def answer(state: str, **extra: object) -> JiraProjectIssues:
        return JiraProjectIssues.model_validate(
            {
                "team_id": team_id,
                "team_name": name,
                "project_key": project_key,
                "state": state,
                **extra,
            }
        )

    if settings.get("needs_reconnect"):
        return answer("needs_reconnect")
    if not project_key:
        return answer("no_project")
    try:
        access = jira_access(team_id)
    except JiraReconnectRequiredError:
        return answer("needs_reconnect")
    except PrivacyViolationError:
        # An ``AutuneError`` too, and never one to answer around.
        raise
    except AutuneError as exc:
        # Atlassian's token endpoint timing out or answering 5xx is a plain
        # ``AutuneError``, not an integration error. Left to rise, one team's
        # bad minute was a 500 for the whole list -- every other team's
        # issues with it, and Atlassian's words in the body (review of #738).
        log.warning("extraction_jira_open_issues_failed", team_id=team_id, error=exc.code)
        return answer("unavailable")
    if access is None:
        return answer("needs_reconnect")
    if not access.project_key:
        return answer("no_project")
    project_key = access.project_key

    client = JiraClient.for_cloud(access.access_token, access.cloud_id)
    try:
        issues, more = client.open_issues(access.project_key)
    except IntegrationError as exc:
        # By code only. The answer's body is the team's Jira, not ours to log.
        log.warning("extraction_jira_open_issues_failed", team_id=team_id, error=exc.code)
        return answer("unavailable")
    finally:
        client.close()

    keys = [issue.key for issue in issues]
    ours = (
        set(
            session.scalars(
                select(ExtExternalRef.external_id).where(
                    ExtExternalRef.system == JIRA,
                    ExtExternalRef.site == access.cloud_id,
                    ExtExternalRef.external_id.in_(keys),
                )
            )
        )
        if keys
        else set()
    )
    site_url = _https(settings.get("site_url"))
    log.info("extraction_jira_open_issues_read", team_id=team_id, count=len(issues), more=more)
    return answer(
        "ok",
        more=more,
        issues=[
            JiraIssueRead(
                key=issue.key,
                summary=issue.summary,
                status=issue.status,
                status_category=issue.status_category,
                assignee=issue.assignee,
                due_date=issue.due_date,
                url=(
                    f"{site_url}/browse/{issue.key}"
                    if site_url and ISSUE_KEY.fullmatch(issue.key)
                    else None
                ),
                from_autune=issue.key in ours,
            )
            for issue in issues
        ],
    )


def _https(value: object) -> str | None:
    """The site's address if it is one a link may point at. The value was
    written at connect time from Atlassian's answer; a link is still only ever
    built on ``https://``."""
    if isinstance(value, str) and value.startswith("https://"):
        return value.rstrip("/")
    return None
