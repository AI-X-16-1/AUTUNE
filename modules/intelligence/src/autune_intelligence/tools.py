"""Module E as tools an agent can call (#260/#261, docs/architecture/agent-layer.md section 4).

Four reads (``TOOLS``) over E's existing service functions, and one action
(``ACTIONS``) the main agent runs to carry out a Report subagent's proposal.
Each returns a dict in the shape agent-layer.md calls ``ToolResult``::

    {"ok", "reason", "summary", "items", "evidence", "confidence", "truncated"}

**Plain functions, no decorator, no ``autune_agent`` import**, the same shape as
module B's ``tools.py`` (#399, #492): ADR 0010's fourth import-linter contract
forbids a module importing ``autune_agent``, so the agent validates these dicts
into its ``ToolResult`` when it collects them.

What is enforced here rather than trusted to the caller:

- ``items`` holds at most ``MAX_ITEMS``; ``truncated`` says when more existed.
- ``evidence`` is always empty. E aggregates other modules' results and holds
  no utterance ids; an item names its meeting by ``meeting_id`` instead.
- An expected absence (no score yet, prediction gated, another team's meeting)
  is ``ok=False`` with a reason, not an exception.
- ``TOOLS`` only read and are safe to call twice. The one action writes, owns
  its transaction, and is kept out of ``TOOLS``.
- ``team_id`` is filled by the run's authenticated scope (``RUN_SCOPE``), never
  chosen by a model.

**No speaking-ratio tool, on purpose.** These tools feed the Report subagent,
whose output goes to many people, and invariant 11 lets one person's speaking
ratio reach only that person. ``speaking_ratio_for_user`` stays behind its own
authenticated route and is not offered to any agent.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from autune_core import Meeting, get_logger, session_scope
from autune_core.errors import (
    ConflictError,
    NotFoundError,
    PrivacyViolationError,
    ValidationError,
)

from . import service, tasks
from .service import _gap_burden

log = get_logger(__name__)

MAX_ITEMS = 5
"""agent-layer.md section 4: a tool ranks and keeps five; the rest stay in E's tables."""

_EXAMPLE_TITLES = 3
"""High-severity gap titles quoted per pattern -- enough to say what the pattern means."""

_MISSING_SOURCE_PENALTY = 0.25
"""Confidence lost per module that had not reported when the score was built."""


def _result(
    *,
    summary: str,
    items: list[dict[str, Any]],
    confidence: float = 1.0,
    ok: bool = True,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "reason": reason,
        "summary": summary,
        "items": items[:MAX_ITEMS],
        "evidence": [],
        "confidence": confidence,
        "truncated": len(items) > MAX_ITEMS,
    }


def meeting_quality(session: Session, meeting_id: str) -> dict[str, Any]:
    """Use this right after a meeting's analysis finishes, or when asked how well
    a meeting went. Do not use it to compare against the team's other meetings --
    that is ``team_trend``.

    Returns the meeting's quality grade and score, and its four components with
    the weakest first. A component that could not be measured says so and is
    listed last; it was not scored as zero. ``summary`` names any module that
    had not reported when the score was built, and ``confidence`` drops with
    each one.
    """
    try:
        score = service.get_score(session, meeting_id)
    except NotFoundError:
        return _result(
            ok=False,
            reason=f"no quality score for meeting {meeting_id}",
            summary="이 회의의 품질 점수가 아직 없습니다.",
            items=[],
            confidence=0.0,
        )

    measured: list[dict[str, Any]] = []
    unmeasured: list[dict[str, Any]] = []
    for title, value, body in (
        ("결정 밀도", score.decision_density, None),
        (
            "고위험 갭",
            _gap_burden(score.gap_count) if score.gap_count is not None else None,
            f"{score.gap_count}건",
        ),
        ("액션아이템 확정률", score.action_item_completion_rate, None),
        ("참여 균형", score.participation_balance, None),
    ):
        if value is None:
            unmeasured.append({"title": title, "body": "측정 안 됨", "score": 0.0})
        else:
            measured.append({"title": title, "body": body or f"{value:.2f}", "score": value})
    measured.sort(key=lambda item: item["score"])

    missing = list(score.missing_sources)
    summary = f"회의 품질 {score.grade}등급 ({score.value:.2f})."
    if missing:
        summary += f" 집계 시 결과가 없던 모듈: {', '.join(missing)}."
    return _result(
        summary=summary,
        items=measured + unmeasured,
        confidence=max(0.0, 1.0 - _MISSING_SOURCE_PENALTY * len(missing)),
    )


def team_trend(session: Session, team_id: str) -> dict[str, Any]:
    """Use this to put one meeting in context -- whether the team's recent
    meetings are getting better or worse, or how this one compares with the
    team's average. Do not use it for one meeting's breakdown -- that is
    ``meeting_quality``.

    Returns the team's scored-meeting count, average grade and action-item
    completion rate in ``summary``, and its most recent scored meetings (newest
    first, at most five) as items.
    """
    dashboard = service.get_dashboard(session, team_id)
    if dashboard.meeting_count == 0:
        return _result(summary="이 팀에는 점수가 매겨진 회의가 없습니다.", items=[])

    summary = (
        f"점수가 매겨진 회의 {dashboard.meeting_count}건, "
        f"평균 {dashboard.average_grade}등급 ({dashboard.average_score:.2f})."
    )
    if dashboard.action_item_completion_rate is not None:
        summary += f" 액션아이템 확정률 {dashboard.action_item_completion_rate:.0%}."
    return _result(
        summary=summary,
        items=[
            {
                "title": f"{entry.grade}등급",
                "body": f"{entry.created_at.date().isoformat()} · {entry.value:.2f}",
                "score": entry.value,
                "meeting_id": entry.meeting_id,
            }
            for entry in dashboard.recent_scores
        ],
    )


def recurring_gaps(session: Session, team_id: str) -> dict[str, Any]:
    """Use this when asked what a team keeps leaving undiscussed across its
    meetings. Do not use it for one meeting's gaps -- module C's tools answer
    that.

    Returns the team's gap patterns, most frequent first (at most five), each
    with a few of the high-severity gap titles behind it. Titles below high
    severity are not quoted. A gap the team dismissed in C after the meeting
    may still be quoted: C does not republish ``autune.gap.completed`` on a
    dismissal, so E's stored payload never learns of it.
    """
    distribution = service.get_dashboard(session, team_id).gap_distribution
    if not distribution:
        return _result(summary="이 팀에서 반복되는 갭 패턴이 없습니다.", items=[])

    titles = service.gap_titles_by_pattern(session, team_id)
    ranked = sorted(distribution.items(), key=lambda kv: (-kv[1], kv[0]))
    return _result(
        summary=f"갭 패턴 {len(ranked)}종, 총 {sum(distribution.values())}건.",
        items=[
            {
                "title": pattern,
                "body": " · ".join(titles.get(pattern, [])[:_EXAMPLE_TITLES]),
                "score": count,
            }
            for pattern, count in ranked
        ],
    )


def misalignment_risk(session: Session, team_id: str) -> dict[str, Any]:
    """Use this only when a report or briefing needs a forward-looking warning
    about the team drifting out of agreement. Do not present it as a fact; it is
    a model's probability.

    Returns the team's latest misalignment probability over its horizon. Before
    the team has enough history (#27's gate), or with no prediction yet, it is
    ``ok=False`` with that reason and no number at all.
    """
    read = service.get_predictions(session, team_id)
    if read.prediction is None:
        return _result(
            ok=False,
            reason=read.reason,
            summary="아직 불일치 위험을 보여줄 수 없습니다.",
            items=[],
            confidence=0.0,
        )

    p = read.prediction
    return _result(
        summary=f"{p.horizon_days}일 안에 팀 의견이 어긋날 위험 {p.probability:.0%} (모델 추정).",
        items=[
            {
                "title": "불일치 위험",
                "body": f"{p.horizon_days}일 · {p.probability:.0%}",
                "score": p.probability,
                "meeting_id": p.meeting_id,
            }
        ],
    )


TOOLS = [meeting_quality, team_trend, recurring_gaps, misalignment_risk]
"""Collected by the agent layer by iterating modules (invariant 6), never registered by hand."""

RUN_SCOPE = ("team_id",)
"""Arguments the run fills from its authenticated scope, never the model -- the
convention B's ``tools.py`` set (#492, review of #449). A model that could choose
``team_id`` could read another team's trend or post into another team's channel."""


# --- actions: E's write, for the main agent to run ------------------------------
#
# agent-layer.md section 8, rule 2: the Report subagent never posts; it proposes,
# and the main agent runs this. Shaped like B's actions: no session argument, the
# action owns its transaction and commits before anything leaves, and it is kept
# out of ``TOOLS`` so a model never calls it directly.


def _refused(reason: str, summary: str) -> dict[str, Any]:
    return _result(ok=False, reason=reason, summary=summary, items=[], confidence=0.0)


def publish_meeting_report(
    team_id: str, meeting_id: str, body_markdown: str, pending_review: bool = False
) -> dict[str, Any]:
    """Store a meeting's finished report and schedule its post to the team channel
    -- what the Report subagent proposes after a meeting's analysis finishes.

    L1 -- runs without approval; the person is told after (proposed on #261).
    Never call it with text another meeting said. Adds the header (title, date)
    and the "자동 생성" footer; the title is left out when it holds personal
    data. ``pending_review`` adds a button to B's review board. Refused for
    another team's meeting, a report already posted, one over the length cap,
    or a body that still holds personal data (by category, never the text).
    """
    with session_scope() as session:
        meeting = session.get(Meeting, meeting_id)
        if meeting is None or meeting.team_id != team_id:
            return _refused(
                f"no meeting {meeting_id} on team {team_id}", "회의를 찾을 수 없습니다."
            )
        document = service.meeting_report_document(meeting, body_markdown)
        try:
            service.save_meeting_report(
                session, meeting_id, document, pending_review=pending_review
            )
        except ConflictError:
            return _refused("already posted", "이미 게시된 리포트입니다.")
        except ValidationError:
            return _refused(
                "report too long", f"리포트가 {service.MEETING_REPORT_MAX_CHARS}자를 넘습니다."
            )
        except PrivacyViolationError as exc:
            # A model wrote the body, so a phone number in it is a route to
            # correct, not a bug in E. Nothing was written; name categories only.
            categories = ", ".join(exc.details.get("categories", []))
            return _refused(
                f"unmasked personal data: {categories}", "리포트에 개인정보가 남아 있습니다."
            )
    # The transaction has committed: a worker that picks this up finds the row.
    try:
        tasks.deliver_meeting_report.apply_async((meeting_id,))
    except Exception as exc:  # the report is stored; the caller must not see a failure
        # Stored and unclaimed: running this action again enqueues it.
        log.warning(
            "intelligence_meeting_report_enqueue_failed",
            meeting_id=meeting_id,
            error=type(exc).__name__,
        )
    summary = "리포트를 저장했고 발송을 예약했습니다."
    # B's actions answer with the changed thing by id; an executor reads both alike.
    return _result(
        summary=summary, items=[{"title": summary, "body": "", "score": 1.0, "id": meeting_id}]
    )


ACTIONS = [publish_meeting_report]
"""E's writes. Kept out of ``TOOLS`` on purpose: the registry offers ``TOOLS`` to
models, and the main agent's executor alone runs these."""
