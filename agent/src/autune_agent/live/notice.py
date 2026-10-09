"""After the upload: tell each participant that live documents are ready (spec section 7).

**A count and a link, never the text.** A Slack message cannot be recalled, and
a document holds meeting words its speaker may later delete (invariant 11) --
the reason ``main/notify.py`` sends a count too. Sent without approval: the
owner decided it (2026-10-09), and the message carries nothing a person would
approve.

**At most once per meeting.** ``transcript.ready`` is published again when a
meeting is reprocessed; the notice row is committed before the first DM, so a
second event finds it and sends nothing. A lost send is not retried.

A team without Slack, a participant with no account, one who has left the team,
or one Slack refusal is logged by type and skipped.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
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


def notify_participants(
    session: Session, meeting_id: str, *, slack: SlackApi | None = None
) -> list[str]:
    meeting = session.get(Meeting, meeting_id)
    if meeting is None or session.get(AgentLiveResearchNotice, meeting_id) is not None:
        return []
    done = session.scalar(
        select(func.count())
        .select_from(AgentLiveResearch)
        .where(AgentLiveResearch.meeting_id == meeting_id, AgentLiveResearch.status == "done")
    )
    if not done:
        return []
    recipients = sorted(
        {
            user_id
            for user_id in session.scalars(
                select(Participant.user_id)
                .join(
                    TeamMember,
                    (TeamMember.user_id == Participant.user_id)
                    & (TeamMember.team_id == meeting.team_id),
                )
                .where(Participant.meeting_id == meeting_id, Participant.user_id.is_not(None))
            )
            if user_id is not None
        }
    )
    if not recipients:
        return []
    owned: SlackClient | None = None
    if slack is None:
        config = load_integration(session, meeting.team_id, "slack")
        if config is None:
            log.info("live_notice_no_slack team_id=%s", meeting.team_id)
            return []
        slack = owned = SlackClient(config.require_secret())
    try:
        session.add(AgentLiveResearchNotice(meeting_id=meeting_id))
        session.commit()
    except IntegrityError:
        session.rollback()
        if owned is not None:
            owned.close()
        return []
    link = get_settings().web_base_url.rstrip("/") + f"/meetings/{meeting_id}{ANCHOR}"
    text = f"회의 중 조사 문서 {done}건이 준비됐습니다. {link}"
    sent: list[str] = []
    try:
        for user_id in recipients:
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
