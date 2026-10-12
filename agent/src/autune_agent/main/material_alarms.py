"""Tell a team's approvers that an upload was stopped, and let them see it (#817, #1201).

Module B refuses a file that carries a confidentiality marking, keeps nothing
of it, and writes one ``ext_material_alarms`` row: the team, the time and the
kind of marking. It cannot say who a team's approvers are -- ``agent_approvers``
is this layer's table and nothing below imports this layer -- so the telling
and the check of who may look are here.

**Module B is read through its ``tools.py`` and nothing else** (the layer's
owner on #1201, as ``live/routes`` reads A's ``consent_attested``): four
functions that are in neither ``TOOLS`` nor ``ACTIONS``, so no model can
call them, and no model of B's is imported here. What comes back is a
``StoppedUpload`` -- team, time, kind of marking, told or not.

**A count and a link, nothing else, as for a waiting proposal** (#632,
``notify.py``). The DM leaves the system; the time and the kind of marking
stay behind the page's sign-in. No file name and no person could be sent:
module B never kept either.

**Each alarm is told once.** A periodic task finds the teams module B holds an
untold alarm for, sends each approver one DM with how many wait in the team,
and has module B set ``told_at`` on exactly the alarms it counted as new. An
alarm stays untold while nobody could be reached -- a team without Slack, no
approver, nobody who linked Slack -- and the next run tries again; it is on
the page either way and module B deletes it after thirty days.

**Who is an approver here is one constant**, ``ALARM_SCOPES``: the holders of
``any``. privacy.md says "the team's approvers" and does not choose between
them and everyone with a narrower scope; the layer's owner chose the
narrower reading (#1201 question 2, 2026-10-12), because the time of an
attempt can point at one person in a small team.

**Acknowledging deletes the row and records nobody.** No ``agent_runs`` row,
no log line with a user id, and no log line with an alarm's id: module B's
function takes no user, and nothing here writes one down.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_agent.models import AgentApprover
from autune_core import TeamMember, get_settings, load_integration
from autune_core.errors import PermissionDeniedError, PrivacyViolationError
from autune_extraction.tools import (
    StoppedUpload,
    acknowledge_stopped_upload,
    mark_stopped_uploads_told,
    stopped_uploads,
    teams_with_untold_stopped_uploads,
)
from autune_integrations import SlackApi, SlackClient

from .notify import APPROVALS_PATH
from .pending import approver_scopes

log = logging.getLogger(__name__)

ALARM_SCOPES = frozenset({"any"})
"""The approver scopes that are told of, see and acknowledge a stopped upload."""

TELL_EVERY = timedelta(hours=1)
"""How often the periodic task looks for an untold alarm, so how late the DM
can be. Whoever tried the upload was told at once, by the refusal itself."""


class NotAnAlarmApproverError(PermissionDeniedError):
    def __init__(self) -> None:
        super().__init__("only the team's approvers see and acknowledge a stopped upload")


def may_see(session: Session, team_id: str, user_id: str) -> bool:
    """Whether ``user_id`` is a current member holding one of ``ALARM_SCOPES``."""
    return bool(approver_scopes(session, team_id, user_id) & ALARM_SCOPES)


def teams_of(session: Session, user_id: str) -> list[str]:
    """The teams where ``user_id`` holds one of ``ALARM_SCOPES``. A row kept
    by someone who left a team is among them: ``waiting`` asks whether they
    are still a member, as it does for a team the caller names."""
    return list(
        session.scalars(
            select(AgentApprover.team_id)
            .where(AgentApprover.user_id == user_id, AgentApprover.scope.in_(ALARM_SCOPES))
            .distinct()
            .order_by(AgentApprover.team_id)
        )
    )


def waiting(session: Session, team_ids: list[str], user_id: str) -> list[StoppedUpload]:
    """The unacknowledged alarms of those of ``team_ids`` where ``user_id`` is
    an approver, the oldest first within a team. A team where they are not
    one contributes nothing, which reads the same as a team with no alarm."""
    return [
        alarm
        for team_id in team_ids
        if may_see(session, team_id, user_id)
        for alarm in stopped_uploads(session, team_id)
    ]


def acknowledge(session: Session, team_id: str, alarm_id: str, user_id: str) -> None:
    """An approver has seen it: module B deletes the row; the caller commits.

    ``user_id`` is read for the check and goes no further. The check comes
    before the lookup, so someone who is not an approver learns nothing about
    which ids exist.
    """
    if not may_see(session, team_id, user_id):
        raise NotAnAlarmApproverError()
    acknowledge_stopped_upload(session, team_id, alarm_id)


def tell_approvers_of_stopped_uploads(
    session: Session, *, slack: SlackApi | None = None, now: datetime | None = None
) -> int:
    """The periodic task's body: every team with an untold alarm is told once.

    Commits after each team, so one team's failure leaves the others told.
    Returns how many teams had somebody told. ``slack`` is for tests.
    """
    told = unreachable = 0
    for team_id in teams_with_untold_stopped_uploads(session):
        try:
            sent = tell_team(session, team_id, slack=slack, now=now)
            session.commit()
        except PrivacyViolationError:
            raise
        except Exception as exc:  # noqa: BLE001 - one team's Slack must not stop the others
            session.rollback()
            log.warning("agent_material_alarm_team_failed error=%s", type(exc).__name__)
            sent = []
        told += bool(sent)
        unreachable += not sent
    if told or unreachable:
        # Counts only: this line needs neither the team nor an approver.
        log.info("agent_material_alarms_told teams=%d unreachable=%d", told, unreachable)
    return told


def tell_team(
    session: Session, team_id: str, *, slack: SlackApi | None = None, now: datetime | None = None
) -> list[str]:
    """DM each of the team's approvers how many stopped uploads wait, then have
    module B mark the ones counted as new. Returns the user ids messaged; the
    caller commits.

    Nothing is marked when nobody was reached, so the next run tries again.
    One approver reached is enough to mark: the others find the alarm on the
    page, and a DM for every later run would be the notification bot this
    layer must not be (#638 review).
    """
    alarms = stopped_uploads(session, team_id)
    new = [alarm.id for alarm in alarms if not alarm.told]
    if not new:
        return []
    recipients = _approvers(session, team_id)
    if not recipients:
        return []
    owned: SlackClient | None = None
    if slack is None:
        config = load_integration(session, team_id, "slack")
        if config is None:
            return []
        slack = owned = SlackClient(config.require_secret())
    link = get_settings().web_base_url.rstrip("/") + APPROVALS_PATH
    text = f"기밀·대외비 표시가 있어 받지 않은 자료 업로드가 {len(alarms)}건 있습니다. {link}"
    sent: list[str] = []
    try:
        for user_id in recipients:
            try:
                slack.send_dm(user_id, text)
            except PrivacyViolationError:
                raise
            except Exception as exc:  # noqa: BLE001 - one approver's Slack must not stop the others
                # Neither the approver nor the team: the count of failures is
                # in the caller's line, and the class of the error is enough here.
                log.warning("agent_material_alarm_dm_failed error=%s", type(exc).__name__)
                continue
            sent.append(user_id)
    finally:
        if owned is not None:
            owned.close()
    if sent:
        mark_stopped_uploads_told(session, team_id, new, now=now)
    return sent


def _approvers(session: Session, team_id: str) -> list[str]:
    """The current members of the team who hold one of ``ALARM_SCOPES``."""
    return sorted(
        set(
            session.scalars(
                select(AgentApprover.user_id)
                .join(
                    TeamMember,
                    (TeamMember.team_id == AgentApprover.team_id)
                    & (TeamMember.user_id == AgentApprover.user_id),
                )
                .where(AgentApprover.team_id == team_id, AgentApprover.scope.in_(ALARM_SCOPES))
            )
        )
    )
