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

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from autune_contracts.gap import GapReport
from autune_core import CurrentUser, get_session

from . import service
from .schemas import TemplateRead, TemplateSelection, TopicGraphRead

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


@router.get("/templates/{meeting_id}", response_model=TemplateSelection)
def get_meeting_template(
    meeting_id: str, session: SessionDep, reader: CurrentUser
) -> TemplateSelection:
    """Which template this meeting is compared against.

    A meeting nobody chose one for answers with the configured default rather
    than with an empty body: there is always a template in force, and a rail
    showing nothing selected would misreport that.
    """
    service.require_readable_meeting(session, meeting_id, reader)
    return TemplateSelection(template_key=service.selected_template_key(session, meeting_id))


@router.put("/templates/{meeting_id}", response_model=TemplateSelection)
def set_meeting_template(
    meeting_id: str,
    selection: TemplateSelection,
    session: SessionDep,
    reader: CurrentUser,
) -> TemplateSelection:
    """Point this meeting at a template, and re-compare against it.

    The gaps on ``/reports/{meeting_id}`` reflect the new template as soon as
    this returns. It does not republish ``autune.gap.completed`` — see
    ``service.set_template``.

    The only write under ``/api/gap``, so it is the one route where an
    unauthenticated caller could have changed what a team sees rather than just
    read it.
    """
    service.require_readable_meeting(session, meeting_id, reader)
    chosen = service.set_template(session, meeting_id, selection.template_key)

    # Committed here rather than left to `get_session`, which commits after the
    # route returns: `detect_gaps` opens its own session — it is the same call
    # the Celery task makes — and an uncommitted override is one it cannot see.
    # Without this the re-comparison would run against the previous template.
    session.commit()
    service.detect_gaps(meeting_id)

    return TemplateSelection(template_key=chosen)
