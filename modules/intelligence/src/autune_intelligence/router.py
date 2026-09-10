"""HTTP entry point for module E.

Routes parse, delegate to ``service``, and format the result. No business logic
here — it cannot be reused by ``tasks.py`` if it lives in a route.

The prefix ``/api/intelligence`` is applied by apps/api; declare paths relative to it.

Every route here is read-only. Auth is not enforced yet — team-level aggregates
(quality, alignment, gap distribution) carry no per-person data, and apps/api has
no auth middleware wired. ``/me/speaking-ratio`` is the one route that must
authorise on the subject, and it is not built here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from autune_core import get_session

from . import service
from .models import IntelReport, IntelScore
from .schemas import DashboardRead, HeatmapCell, ReportRead, ScoreRead

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


@router.get("/heatmap/{team_id}", response_model=list[HeatmapCell])
def get_heatmap(team_id: str, session: SessionDep) -> list[HeatmapCell]:
    """Cross-role alignment, averaged across the team's meetings."""
    return service.get_heatmap(session, team_id)


@router.get("/reports/{team_id}", response_model=list[ReportRead])
def list_reports(team_id: str, session: SessionDep) -> list[IntelReport]:
    """The team's generated weekly reports, newest period first."""
    return service.list_reports(session, team_id)
