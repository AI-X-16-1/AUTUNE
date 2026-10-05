"""HTTP entry point for module E.

Routes parse, delegate to ``service``, and format the result. No business logic
here — it cannot be reused by ``tasks.py`` if it lives in a route.

The prefix ``/api/intelligence`` is applied by apps/api; declare paths relative to it.

Every route here is read-only but the edit of a meeting report, and every
route but ``/health`` authenticates and checks team membership (#800, #812
reviews): apps/api has no auth middleware (#156, #189), so each route does it
itself. A team route refuses anyone not on the team; ``/scores/{meeting_id}``
answers another team's person with the same 404 as a meeting with no score, so
whether a meeting exists does not leak. What they carry: action-item counts,
gap *title text* (meeting content), meeting reports. Nothing here posts.
``/me/speaking-ratio`` authorises on the subject, not the team.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from autune_core import CurrentUser, get_logger, get_session
from autune_core.errors import NotFoundError

from . import enqueue, service
from .models import IntelReport, IntelScore
from .schemas import (
    DashboardRead,
    HeatmapCell,
    MeetingReportCorrection,
    MeetingReportEdit,
    MeetingReportRead,
    PredictionsRead,
    ReportRead,
    ScoreRead,
    SpeakingRatioRead,
    WeeklyReportScheduleEdit,
    WeeklyReportScheduleRead,
)

router = APIRouter()
log = get_logger(__name__)

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/health")
def health() -> dict[str, str]:
    return {"module": "intelligence", "status": "ok"}


@router.get("/scores/{meeting_id}", response_model=ScoreRead)
def get_score(meeting_id: str, user: CurrentUser, session: SessionDep) -> IntelScore:
    """One meeting's quality score and the components behind it. Its team only."""
    return service.get_score(session, meeting_id, user_id=user.id)


@router.get("/dashboard/{team_id}", response_model=DashboardRead)
def get_dashboard(team_id: str, user: CurrentUser, session: SessionDep) -> DashboardRead:
    """Team rollup: quality trend, gap-type distribution, action completion.
    Members only."""
    service.require_team_member(session, user_id=user.id, team_id=team_id)
    return service.get_dashboard(session, team_id)


@router.get("/gap-titles/{team_id}", response_model=dict[str, list[str]])
def get_gap_titles(team_id: str, user: CurrentUser, session: SessionDep) -> dict[str, list[str]]:
    """The high-severity gap titles behind each pattern in the gap distribution.

    Content text, not an aggregate number. Members only.
    """
    service.require_team_member(session, user_id=user.id, team_id=team_id)
    return service.gap_titles_by_pattern(session, team_id)


@router.get("/heatmap/{team_id}", response_model=list[HeatmapCell])
def get_heatmap(team_id: str, user: CurrentUser, session: SessionDep) -> list[HeatmapCell]:
    """Cross-role alignment, averaged across the team's meetings. Members only."""
    service.require_team_member(session, user_id=user.id, team_id=team_id)
    return service.get_heatmap(session, team_id)


@router.get("/predictions/{team_id}", response_model=PredictionsRead)
def get_predictions(team_id: str, user: CurrentUser, session: SessionDep) -> PredictionsRead:
    """The team's latest misalignment prediction, withheld until #27's gate clears.
    Members only."""
    service.require_team_member(session, user_id=user.id, team_id=team_id)
    return service.get_predictions(session, team_id)


@router.get("/reports/{team_id}", response_model=list[ReportRead])
def list_reports(team_id: str, user: CurrentUser, session: SessionDep) -> list[IntelReport]:
    """The team's generated weekly reports, newest period first. Members only."""
    service.require_team_member(session, user_id=user.id, team_id=team_id)
    return service.list_reports(session, team_id)


@router.get("/weekly-report-schedule/{team_id}", response_model=WeeklyReportScheduleRead)
def get_weekly_report_schedule(
    team_id: str, user: CurrentUser, session: SessionDep
) -> service.WeeklyReportSchedule:
    """When the team's weekly report goes out. Members only."""
    service.require_team_member(session, user_id=user.id, team_id=team_id)
    return service.weekly_report_schedule(session, team_id)


@router.put("/weekly-report-schedule/{team_id}", response_model=WeeklyReportScheduleRead)
def set_weekly_report_schedule(
    team_id: str, edit: WeeklyReportScheduleEdit, user: CurrentUser, session: SessionDep
) -> service.WeeklyReportSchedule:
    """A member sets the weekday, the hour (Korean time) and whether an empty
    week is posted; the next slot follows it."""
    return service.set_weekly_report_schedule(
        session,
        team_id,
        weekday=edit.weekday,
        hour=edit.hour,
        send_empty=edit.send_empty,
        user_id=user.id,
    )


@router.get("/meeting-reports/{team_id}", response_model=list[MeetingReportRead])
def list_meeting_reports(
    team_id: str, user: CurrentUser, session: SessionDep
) -> list[MeetingReportRead]:
    """The team's meeting reports for the dashboard card. Members only: a report
    is meeting text, not an aggregate."""
    return service.list_meeting_reports(session, team_id, user_id=user.id)


@router.put("/meeting-reports/{meeting_id}", response_model=MeetingReportRead)
def edit_meeting_report(
    meeting_id: str, edit: MeetingReportEdit, user: CurrentUser, session: SessionDep
) -> MeetingReportRead:
    """A team member edits a draft before it is posted; refused once posted.

    Nothing is posted from here. The edit is committed first, so the Report
    subagent woken by the announcement reads it, and then announced; its post
    goes to the approval queue (#674). Ids only to the task (#275).
    """
    report = service.edit_meeting_report(
        session, meeting_id, edit.body, user_id=user.id, base_updated_at=edit.base_updated_at
    )
    session.commit()
    _announce(meeting_id)
    return report


@router.post(
    "/meeting-reports/{meeting_id}/corrections",
    response_model=MeetingReportRead,
    status_code=202,
)
def correct_meeting_report(
    meeting_id: str, correction: MeetingReportCorrection, user: CurrentUser, session: SessionDep
) -> MeetingReportRead:
    """A member corrects a posted report; it waits for approval (#674).

    Nothing is posted from here. Committed, then announced, so the Report
    subagent woken by it reads the correction and proposes its post; once
    approved it goes out as a reply under the post. Ids only (#275).
    """
    report = service.correct_meeting_report(session, meeting_id, correction.body, user_id=user.id)
    session.commit()
    _announce(meeting_id)
    return report


def _announce(meeting_id: str) -> None:
    """Queue the announcement of a committed change. A queue that refuses it does
    not fail the request -- the change is saved, and the sweep announces it
    within minutes (#698). Ids only in the log."""
    try:
        enqueue.announce_meeting_report_changed(meeting_id)
    except Exception as exc:
        log.warning(
            "intelligence_meeting_report_announce_enqueue_failed",
            meeting_id=meeting_id,
            error=type(exc).__name__,
        )


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
