"""HTTP entry point for module C.

Routes parse, delegate to ``service``, and format the result. No business logic
here — it cannot be reused by ``tasks.py`` if it lives in a route.

The prefix ``/api/gap`` is applied by apps/api; declare paths relative to it.

**Every route but ``/health`` takes ``CurrentUser``**, and every route naming a
meeting calls ``service.require_readable_meeting`` before anything else (#276).
A gap report carries the participation matrix, and until this landed anyone who
knew a meeting id could read it. ``/health`` stays open because apps/api's
health check calls it and it carries nothing.

A new route here inherits none of that by default. If it names a meeting, it
calls the same function on its first line.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from autune_contracts.enums import GapSeverity
from autune_contracts.gap import GapReport
from autune_core import CurrentUser, User, get_session

from . import service
from .enqueue import enqueue_publish_report
from .schemas import (
    GapDismissal,
    GapExplanations,
    TeamGapRead,
    TemplateComparison,
    TemplateRead,
    TemplateSelection,
    TopicGraphRead,
)

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/health")
def health() -> dict[str, str]:
    return {"module": "gap", "status": "ok"}


@router.get("/reports/{meeting_id}", response_model=GapReport)
def get_report(meeting_id: str, session: SessionDep, reader: CurrentUser) -> GapReport:
    """The gap report for S20, assembled from the stored rows.

    The same payload E received on ``autune.gap.completed`` — read from the
    rows rather than from a copy of the event, so a report reopened a week
    later shows the dismissals made since.

    ``participation`` is lists of ids with no number attached, and reading it
    along a *person* rather than along a topic rebuilds the speaking ratio that
    shape exists to prevent (docs/architecture/privacy.md section 3). The
    screen renders a topic's silent side; it does not total a person's. That
    is why ``spoke`` is a boolean and not a count — but a shape is only half of
    it, and who may ask at all is the other half (#276).
    """
    service.require_readable_meeting(session, meeting_id, reader)
    return service.build_report(session, meeting_id)


@router.get("/topics/{meeting_id}", response_model=TopicGraphRead)
def get_topic_graph(meeting_id: str, session: SessionDep, reader: CurrentUser) -> TopicGraphRead:
    """The topic graph behind the report, for the visualization on S20.

    Topic labels are transcript text, so this is meeting content even though it
    carries no participation.
    """
    service.require_readable_meeting(session, meeting_id, reader)
    return service.topic_graph(session, meeting_id)


@router.get("/explanations/{meeting_id}", response_model=GapExplanations)
def get_explanations(meeting_id: str, session: SessionDep, reader: CurrentUser) -> GapExplanations:
    """Why each gap was raised -- its evidence, and how its score was reached.

    Quotes masked utterances, so it is meeting content and takes the same check
    as the report. No speaker and no participation: the quote is what was said
    and when.
    """
    service.require_readable_meeting(session, meeting_id, reader)
    return service.explain(session, meeting_id)


@router.get("/gaps", response_model=list[TeamGapRead])
def list_team_gaps(
    team_id: str,
    session: SessionDep,
    reader: CurrentUser,
    severity: Annotated[list[GapSeverity], Query()] = [GapSeverity.HIGH],  # noqa: B006
) -> list[TeamGapRead]:
    """Every open gap across a team's meetings -- the sidebar's "갭 리포트" (#550).

    Names a team rather than a meeting, so the check is the team's: the caller
    must belong to it, and an unknown team is the same 404 as another team's.

    ``severity`` repeats (``?severity=high&severity=medium``) and defaults to
    ``high``, the only level S20 surfaces without being asked. See
    ``service.team_gaps``.
    """
    service.require_readable_team(session, team_id, reader)
    return service.team_gaps(session, team_id, severities=severity)


@router.post("/gaps/{gap_id}/dismiss", response_model=GapDismissal)
def dismiss_gap(gap_id: str, session: SessionDep, reader: CurrentUser) -> GapDismissal:
    """Mark one gap a false positive — "해당 없음" on S20.

    The gap leaves the report and stays in the table, marked; threshold tuning
    reads the mark (ADR 0006). The rail keeps the item and says it was
    dismissed, because a false positive is a judgement about the gap and not
    evidence the meeting covered the item.

    Named by the gap rather than the meeting, so the membership check is the
    service's: an unknown gap and a gap on another team's meeting are the same
    404 — see ``service.set_dismissed``.

    E is sent the report again, so a gap the team called wrong stops being
    quoted and scored (#471).
    """
    return _dismiss(session, gap_id, reader, dismissed=True)


@router.delete("/gaps/{gap_id}/dismiss", response_model=GapDismissal)
def undo_dismiss_gap(gap_id: str, session: SessionDep, reader: CurrentUser) -> GapDismissal:
    """Take a dismissal back. The gap returns to the report as it was raised.

    A button pressed by mistake has to be undoable from the screen, or the only
    way to correct it is a row nobody can see — and the mistake would sit in the
    data threshold tuning reads.
    """
    return _dismiss(session, gap_id, reader, dismissed=False)


def _dismiss(session: Session, gap_id: str, reader: User, *, dismissed: bool) -> GapDismissal:
    """Set the mark, commit it, then queue the republish.

    Committed here rather than left to ``get_session``, which commits after the
    route returns: a worker that picked the task up first would publish the
    report as it was before the mark.
    """
    result = service.set_dismissed(session, gap_id, reader, dismissed=dismissed)
    session.commit()
    enqueue_publish_report(result.meeting_id)
    return result


@router.get("/templates", response_model=list[TemplateRead])
def list_templates(reader: CurrentUser) -> list[TemplateRead]:
    """Every domain template a meeting can be compared against.

    Reference data read from files in the package, so no session and no meeting
    — the list is the same for everyone and does not change at runtime.

    It still takes a caller. Nothing here is meeting content and this could be
    open, but an unauthenticated route in a router where every other route is
    authenticated is the one somebody copies when they add the next endpoint.
    """
    return service.available_templates()


@router.get("/templates/{meeting_id}", response_model=TemplateComparison)
def get_meeting_template(
    meeting_id: str, session: SessionDep, reader: CurrentUser
) -> TemplateComparison:
    """Which template this meeting is compared against, and how far it got with
    each of its items — the template-comparison rail on S20.

    A meeting nobody chose a template for answers with the configured default
    rather than with an empty body: there is always a template in force, and a
    rail showing nothing selected would misreport that. A meeting nobody has
    analysed answers ``analysed: false`` with no coverage on any item, which is
    the other thing the rail must not get wrong — see
    ``service.template_comparison``.

    The response is a superset of ``TemplateSelection``: ``PUT`` still takes and
    returns the selection alone, because choosing a template is choosing a name
    and a request body that carried a read-only comparison would invite a caller
    to send one back.
    """
    service.require_readable_meeting(session, meeting_id, reader)
    return service.template_comparison(session, meeting_id)


@router.put("/templates/{meeting_id}", response_model=TemplateSelection)
def set_meeting_template(
    meeting_id: str,
    selection: TemplateSelection,
    session: SessionDep,
    reader: CurrentUser,
) -> TemplateSelection:
    """Point this meeting at a template, and re-compare against it.

    The gaps on ``/reports/{meeting_id}`` reflect the new template as soon as
    this returns, and E is sent the report again on the worker — see
    ``service.set_template``.

    One of the writes under ``/api/gap``, with the dismissal routes above: the
    routes where an unauthenticated caller could have changed what a team sees
    rather than just read it.
    """
    service.require_readable_meeting(session, meeting_id, reader)
    chosen = service.set_template(session, meeting_id, selection.template_key)

    # Committed here rather than left to `get_session`, which commits after the
    # route returns: `detect_gaps` opens its own session — it is the same call
    # the Celery task makes — and an uncommitted override is one it cannot see.
    # Without this the re-comparison would run against the previous template.
    session.commit()
    service.detect_gaps(meeting_id)
    enqueue_publish_report(meeting_id)

    return TemplateSelection(template_key=chosen)
