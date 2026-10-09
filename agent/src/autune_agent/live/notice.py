"""After the upload: tell each person that live documents are ready (spec section 7).

**Who.** The meeting's participants with an account, and whoever asked for one
of its ``done`` documents -- on a fresh upload no speaker is identified yet,
and the person who ran the live session is the one who knows to look. Only
current members of the meeting's team.

**A count and a link, never the text.** A Slack message cannot be recalled, and
a document holds meeting words its speaker may later delete (invariant 11) --
the reason ``main/notify.py`` sends a count too. Sent without approval: the
owner decided it (2026-10-09), and the message carries nothing a person would
approve.

**At most once per person.** ``transcript.ready`` is published again when a
meeting is reprocessed; a person's notice row is committed before their DM, so
a later event skips them and reaches only people identified since. A lost send
is not retried.

A team without Slack, a participant with no account, one who has left the team,
or one Slack refusal is logged by type and skipped.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autune_agent.models import AgentLiveResearch, AgentLiveResearchNotice
from autune_core import Meeting, Participant, TeamMember, get_settings, load_integration
from autune_core.errors import PrivacyViolationError
from autune_integrations import SlackApi, SlackClient

log = logging.getLogger(__name__)

ANCHOR = "#live-research"


def tell_participants(session: Session, meeting_id: str) -> None:
    try:
        notify_participants(session, meeting_id)
    except PrivacyViolationError:
        raise
    except Exception as exc:  # noqa: BLE001 - telling people is a courtesy
        session.rollback()
        log.warning("live_notice_skipped meeting=%s error=%s", meeting_id, type(exc).__name__)


def _recipients(session: Session, meeting: Meeting) -> list[str]:
    participants = select(Participant.user_id).where(
        Participant.meeting_id == meeting.id, Participant.user_id.is_not(None)
    )
    requesters = select(AgentLiveResearch.requested_by).where(
        AgentLiveResearch.meeting_id == meeting.id,
        AgentLiveResearch.status == "done",
        AgentLiveResearch.requested_by.is_not(None),
    )
    told = select(AgentLiveResearchNotice.user_id).where(
        AgentLiveResearchNotice.meeting_id == meeting.id
    )
    return sorted(
        session.scalars(
            select(TeamMember.user_id).where(
                TeamMember.team_id == meeting.team_id,
                or_(TeamMember.user_id.in_(participants), TeamMember.user_id.in_(requesters)),
                TeamMember.user_id.not_in(told),
            )
        )
    )


def _claim(session: Session, meeting_id: str, user_id: str) -> bool:
    try:
        session.add(AgentLiveResearchNotice(meeting_id=meeting_id, user_id=user_id))
        session.commit()
    except IntegrityError:
        session.rollback()
        return False
    return True


def notify_participants(
    session: Session, meeting_id: str, *, slack: SlackApi | None = None
) -> list[str]:
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        return []
    done = session.scalar(
        select(func.count())
        .select_from(AgentLiveResearch)
        .where(AgentLiveResearch.meeting_id == meeting_id, AgentLiveResearch.status == "done")
    )
    if not done:
        return []
    recipients = _recipients(session, meeting)
    if not recipients:
        return []
    owned: SlackClient | None = None
    if slack is None:
        config = load_integration(session, meeting.team_id, "slack")
        if config is None:
            log.info("live_notice_no_slack team_id=%s", meeting.team_id)
            return []
        slack = owned = SlackClient(config.require_secret())
    link = get_settings().web_base_url.rstrip("/") + f"/meetings/{meeting_id}{ANCHOR}"
    text = f"회의 중 조사 문서 {done}건이 준비됐습니다. {link}"
    sent: list[str] = []
    try:
        for user_id in recipients:
            if not _claim(session, meeting_id, user_id):
                continue
            try:
                slack.send_dm(user_id, text)
            except PrivacyViolationError:
                raise
            except Exception as exc:  # noqa: BLE001 - one person's Slack must not stop the others
                log.warning("live_notice_failed user_id=%s error=%s", user_id, type(exc).__name__)
                continue
            sent.append(user_id)
    finally:
        if owned is not None:
            owned.close()
    return sent
