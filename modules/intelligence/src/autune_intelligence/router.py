"""HTTP entry point for module E.

Routes parse, delegate to ``service``, and format the result. No business logic
here — it cannot be reused by ``tasks.py`` if it lives in a route.

The prefix ``/api/intelligence`` is applied by apps/api; declare paths relative to it.

Every route here is read-only. Auth is not enforced yet — apps/api has no auth
middleware wired (#156, #189). Most of these are team-level aggregates (quality,
alignment, gap distribution) that carry no per-person data, so the gap is
tolerable until then. ``/gap-titles`` is the exception: it returns gap *title
text*, not a number, to anyone who knows a ``team_id`` — narrower than a
transcript, but real content, not an aggregate. Tracked in #156/#189 rather
than solved here. ``/me/speaking-ratio`` is the one route that must authorise
on the subject regardless of when the rest gets auth, and it is not built here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from autune_core import CurrentUser, get_session
from autune_core.errors import NotFoundError

from . import service
from .models import IntelReport, IntelScore
from .schemas import DashboardRead, HeatmapCell, ReportRead, ScoreRead, SpeakingRatioRead

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/health")
def health() -> dict[str, str]:
    return {"module": "intelligence", "status": "ok"}


@router.get("/scores/{meeting_id}", response_model=ScoreRead)
def get_score(meeting_id: str, session: SessionDep) -> IntelScore:
    """One meeting's quality score and the components behind it."""
    return service.get_score(session, meeting_id)


@router.get("/dashboard/{team_id}", response_model=DashboardRead)
def get_dashboard(team_id: str, session: SessionDep) -> DashboardRead:
    """Team rollup: quality trend, gap-type distribution, action completion."""
    return service.get_dashboard(session, team_id)


@router.get("/gap-titles/{team_id}", response_model=dict[str, list[str]])
def get_gap_titles(team_id: str, session: SessionDep) -> dict[str, list[str]]:
    """The high-severity gap titles behind each pattern in the gap distribution.

    Unlike this router's other routes, the response is content text, not an
    aggregate number — see the module docstring's auth note.
    """
    return service.gap_titles_by_pattern(session, team_id)


@router.get("/heatmap/{team_id}", response_model=list[HeatmapCell])
def get_heatmap(team_id: str, session: SessionDep) -> list[HeatmapCell]:
    """Cross-role alignment, averaged across the team's meetings."""
    return service.get_heatmap(session, team_id)


@router.get("/reports/{team_id}", response_model=list[ReportRead])
def list_reports(team_id: str, session: SessionDep) -> list[IntelReport]:
    """The team's generated weekly reports, newest period first."""
    return service.list_reports(session, team_id)


@router.get("/me/speaking-ratio/{meeting_id}", response_model=SpeakingRatioRead)
def my_speaking_ratio(meeting_id: str, user: CurrentUser, session: SessionDep) -> SpeakingRatioRead:
    """This meeting's speaking share for the authenticated user, and nobody else.

    There is deliberately no subject in the path: no form of this endpoint
    returns another person's ratio, and there is no admin override. The value is
    computed on demand and never stored. See docs/architecture/privacy.md
    section 3.
    """
    ratio = service.speaking_ratio_for_user(session, meeting_id, user.id)
    if ratio is None:
        raise NotFoundError("speaking ratio", meeting_id)
    return ratio
