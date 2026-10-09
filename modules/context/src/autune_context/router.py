"""HTTP entry point for module D.

Routes parse, delegate to ``service``, and format the result. No business logic
here — it cannot be reused by ``tasks.py`` if it lives in a route.

The prefix ``/api/context`` is applied by apps/api; declare paths relative to it.

**Every route that names a meeting, a link, a thread or a team takes
``CurrentUser`` and calls one of ``service.require_readable_*`` /
``require_writable_link`` on its first line** (#189). A caller outside the team
gets the same 404 as an unknown id. ``/health`` stays open: apps/api's health
check calls it and it carries nothing. A new route inherits none of this by
default -- ``tests/unit/test_route_auth.py`` fails until it does.
"""

from __future__ import annotations

from fastapi import APIRouter

from autune_core import CurrentUser, SessionDep

from . import briefs, service
from .schemas import (
    AgendaItemRead,
    BriefDecisionRead,
    BriefRead,
    BriefRecapRead,
    DecisionLineageRead,
    DecisionSummaryRead,
    DecisionVersionRead,
    LinkConfirmRequest,
    TopicLinkRead,
    TopicLinksRead,
)

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"module": "context", "status": "ok"}


@router.get("/links/{meeting_id}", response_model=TopicLinksRead)
def get_links(meeting_id: str, session: SessionDep, reader: CurrentUser) -> TopicLinksRead:
    """This meeting's topic links, asserted (incl. user-confirmed) and pending."""
    service.require_readable_meeting(session, meeting_id, reader)
    asserted, pending = service.get_topic_links(session, meeting_id)
    return TopicLinksRead(
        asserted=[TopicLinkRead.model_validate(link) for link in asserted],
        pending=[TopicLinkRead.model_validate(link) for link in pending],
    )


@router.post("/links/{link_id}/confirm", response_model=TopicLinkRead)
def confirm_link(
    link_id: int, payload: LinkConfirmRequest, session: SessionDep, reader: CurrentUser
) -> TopicLinkRead:
    """A user confirms or rejects a pending link -- a member of the link's
    meeting's team, since a link id can be reached by counting (#189)."""
    service.require_writable_link(session, link_id, reader)
    link = service.confirm_topic_link(session, link_id, payload.status)
    session.commit()
    return TopicLinkRead.model_validate(link)


@router.get("/decisions/{thread_id}", response_model=DecisionLineageRead)
def get_decision_thread(
    thread_id: str, session: SessionDep, reader: CurrentUser
) -> DecisionLineageRead:
    """A decision thread's full lineage timeline, oldest version first.

    A version's ``previous_statement``/``previous_meeting_id`` are blanked here
    (not in ``service``) when the predecessor they quote has since expired —
    see ``service.get_decision_lineage`` for why that can't be done by editing
    the ORM row itself. ``topic_label`` comes from the latest *visible*
    version's own ``current_statement`` (``versions`` is oldest-first and
    guaranteed non-empty here), not ``thread.topic_label`` — that cached
    column is only refreshed by ``_rethread``/``sweep_stale_topic_labels``,
    neither of which runs on a mere expiry, so it can still quote a version
    that just aged out of visibility while an earlier one is the true head.
    """
    service.require_readable_thread(session, thread_id, reader)
    thread, versions, visible_prior_meeting_ids = service.get_decision_lineage(session, thread_id)
    version_reads = []
    for version in versions:
        version_read = DecisionVersionRead.model_validate(version)
        if (
            version_read.previous_meeting_id is not None
            and version_read.previous_meeting_id not in visible_prior_meeting_ids
        ):
            version_read = version_read.model_copy(
                update={"previous_statement": None, "previous_meeting_id": None}
            )
        version_reads.append(version_read)
    return DecisionLineageRead(
        thread_id=thread.id,
        topic_label=versions[-1].current_statement[:400],
        versions=version_reads,
    )


@router.get("/decisions", response_model=list[DecisionSummaryRead])
def list_decision_threads(
    team_id: str,
    session: SessionDep,
    reader: CurrentUser,
    topic: str | None = None,
    change_type: str | None = None,
) -> list[DecisionSummaryRead]:
    """A team's decision threads by their current head, filterable by topic
    and/or change type.

    ``topic_label`` comes from each head version's own ``current_statement``,
    not the cached ``thread.topic_label`` column — see
    ``service.list_decisions`` for why (same reasoning as ``get_decision_thread``).
    """
    service.require_readable_team(session, team_id, reader)
    pairs = service.list_decisions(session, team_id, topic=topic, change_type=change_type)
    return [
        DecisionSummaryRead(
            thread_id=thread.id,
            topic_label=version.current_statement[:400],
            meeting_id=version.meeting_id,
            change_type=version.change_type,
            confidence=version.confidence,
            updated_at=version.updated_at,
        )
        for thread, version in pairs
    ]


@router.get("/briefs/{meeting_id}", response_model=BriefRead)
def get_brief(meeting_id: str, session: SessionDep, reader: CurrentUser) -> BriefRead:
    """A scheduled meeting's pre-meeting brief, once it has been composed.

    404 until ``brief_lead_minutes`` before the start -- the brief is composed
    by the worker then, not on request -- and to anyone outside the meeting's
    team. The first route here to check its caller; the rest are #189.
    """
    brief = briefs.get_brief(session, meeting_id, reader)
    recap = brief.recap
    return BriefRead(
        meeting_id=brief.meeting_id,
        title=brief.title,
        starts_at=brief.starts_at,
        recap=(
            BriefRecapRead(
                meeting_id=recap.meeting_id,
                title=recap.title,
                day=recap.day,
                topics=list(recap.topics),
                decisions=[
                    BriefDecisionRead(statement=d.statement, change_type=d.change_type.value)
                    for d in recap.decisions
                ],
            )
            if recap is not None
            else None
        ),
        recap_gone=brief.recap_gone,
        match_reason=brief.match_reason,
        agenda=[
            AgendaItemRead(title=item.title, key=item.key, status=item.status, url=item.url)
            for item in brief.agenda
        ],
        sent_at=brief.sent_at,
    )
