"""Module E as tools an agent can call (#260/#261, docs/architecture/agent-layer.md section 4).

Reads (``TOOLS``) over E's existing service functions, and one action
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

from datetime import date
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
from .models import IntelMeetingReport
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

    Returns the team's scored-meeting count, average grade, action-item
    confirmation rate (the quality score's) and -- when B's counts are current
    -- completion rate and overdue count in ``summary``, and its most recent
    scored meetings (newest first, at most five) as items.
    """
    dashboard = service.get_dashboard(session, team_id)
    # B's counts do not wait for E's scores (#800 review): say them either way.
    completion = ""
    if dashboard.action_item_completion_rate is not None:
        completion += (
            f" 최근 4주 회의의 액션아이템 완료율 {dashboard.action_item_completion_rate:.0%}."
        )
    if dashboard.overdue_action_items is not None:
        # Over every kept meeting, not the four weeks, and shown on its own floor.
        completion += f" 기한 지난 항목 {dashboard.overdue_action_items}건(보관 중인 회의 전체)."
    if dashboard.meeting_count == 0:
        return _result(summary="이 팀에는 점수가 매겨진 회의가 없습니다." + completion, items=[])

    summary = (
        f"점수가 매겨진 회의 {dashboard.meeting_count}건, "
        f"평균 {dashboard.average_grade}등급 ({dashboard.average_score:.2f})."
    )
    if dashboard.action_item_confirmation_rate is not None:
        summary += f" 액션아이템 확정률 {dashboard.action_item_confirmation_rate:.0%}."
    summary += completion
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
    severity are not quoted. A gap the team dismissed in C after the meeting,
    or one a template switch removed, drops out once C's republished
    ``GapReport`` reaches E and the meeting is re-aggregated (#471). Until
    then -- and, rarely, if two republishes arrive out of order or one is lost
    (see C's ``republish_report``) -- it may still be quoted.
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


def meeting_report_draft(
    session: Session, team_id: str, meeting_id: str, draft_id: str
) -> dict[str, Any]:
    """Use this to show the person approving a meeting report's post the text it
    would post. Do not use it to write or change a report.

    Returns the stored report as one item -- ``title`` is its header line,
    ``body`` the rest, footer included -- when ``draft_id`` names the draft
    stored now, with ``posted`` saying whether it already went out. Returns no
    item when a later run replaced that draft or no report is stored: the
    approval names text that is no longer there (#570, #571).
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None or meeting.team_id != team_id:
        return _refused("meeting not found", "회의를 찾을 수 없습니다.")
    row = session.get(IntelMeetingReport, meeting_id)
    if row is None or row.draft_id != draft_id:
        return _result(summary="이 승인에 해당하는 리포트 초안이 더 이상 없습니다.", items=[])
    # The stored document is "📋 <title> · <date>", a blank line, the body and
    # the footer (``service.meeting_report_document``).
    header, _, rest = row.body_markdown.partition("\n\n")
    return _result(
        summary="게시될 리포트 초안입니다.",
        items=[
            {
                "title": header.removeprefix("📋").strip(),
                "body": rest,
                "score": 1.0,
                "id": meeting_id,
                "posted": row.sent_at is not None,
            }
        ],
    )


def meeting_report_awaiting_approval(
    session: Session, team_id: str, meeting_id: str
) -> dict[str, Any]:
    """Use this when a person changed a meeting's report and its post must be
    proposed for approval. Do not use it to read the report's text -- that is
    ``meeting_report_draft`` or ``meeting_report_correction``.

    Returns what waits as one item: ``kind`` is ``"draft"`` with the
    ``draft_id`` of an edited draft before the report is posted, or
    ``"correction"`` with the ``correction_id`` of a correction to a posted
    report (#674). ``ok=False`` with reason ``already posted`` when the report
    is posted and no correction waits: nothing about this meeting's report is
    to be proposed, a new draft least of all. No item when no report is stored.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None or meeting.team_id != team_id:
        return _refused("meeting not found", "회의를 찾을 수 없습니다.")
    awaiting = service.meeting_report_awaiting_approval(session, meeting_id)
    if awaiting is None:
        if service.meeting_report_posted(session, meeting_id):
            return _refused("already posted", "이미 게시된 리포트입니다.")
        return _result(summary="승인을 기다리는 리포트가 없습니다.", items=[])
    key = "draft_id" if awaiting.kind == "draft" else "correction_id"
    title = "리포트 초안" if awaiting.kind == "draft" else "리포트 수정본"
    return _result(
        summary=f"승인을 기다리는 {title}이 있습니다.",
        items=[
            {
                "title": title,
                "body": "",
                "score": 1.0,
                "id": meeting_id,
                "kind": awaiting.kind,
                key: awaiting.id,
            }
        ],
    )


def meeting_report_correction(
    session: Session, team_id: str, meeting_id: str, correction_id: str
) -> dict[str, Any]:
    """Use this to show the person approving a correction's post the text it
    would post. Do not use it to write or change a correction.

    Returns the correction as one item -- ``title`` its header line, ``body``
    the text -- when ``correction_id`` names the one stored now. No item when a
    later correction replaced it (#674).
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None or meeting.team_id != team_id:
        return _refused("meeting not found", "회의를 찾을 수 없습니다.")
    text = service.meeting_report_correction(session, meeting_id, correction_id)
    if text is None:
        return _result(summary="이 승인에 해당하는 수정본이 더 이상 없습니다.", items=[])
    header, _, body = text.partition("\n\n")
    return _result(
        summary="게시될 리포트 수정본입니다.",
        items=[{"title": header, "body": body, "score": 1.0, "id": meeting_id}],
    )


_LIST_BODY_CHARS = 120
_REPORT_BODY_CHARS = 1500
"""Per-tool budgets (spec section 4, "Tool contracts"): the ask loop's 80-character
cut would halve a report, so E's tools cut their own."""


def _report_item(read: Any, draft_id: str | None, held: Any, body: str) -> dict[str, Any]:
    status = "posted" if read.status == "posted" else "draft"
    return {
        "title": read.title,
        "body": body,
        "score": 1.0,
        "id": read.meeting_id,
        "meeting_id": read.meeting_id,
        "status": status,
        "draft_id": draft_id,
        "editor": read.edited_by_name,
        "date": held.astimezone(service._KST).date().isoformat(),
        "correction": read.correction_status,
    }


def meeting_reports(
    session: Session,
    team_id: str,
    since: str | None = None,
    until: str | None = None,
    title_contains: str | None = None,
    limit: int = MAX_ITEMS,
) -> dict[str, Any]:
    """Use this to find a team's meeting reports by date or title, or to say
    whether one is posted. Do not use it for a report's text -- that is
    ``meeting_report_body``.

    ``since`` and ``until`` are Korean dates (YYYY-MM-DD), both inclusive.
    Returns up to five, newest first, each with its meeting id, date, status
    (draft or posted), the member who edited it and the correction state.
    """
    try:
        start = date.fromisoformat(since) if since else None
        end = date.fromisoformat(until) if until else None
    except ValueError:
        return _refused("bad date", "날짜는 YYYY-MM-DD로 주세요.")
    rows = service.team_meeting_reports(
        session,
        team_id,
        since=start,
        until=end,
        title_contains=title_contains,
        limit=max(1, min(limit, MAX_ITEMS)) + 1,
    )
    items = [
        _report_item(
            read,
            draft_id,
            held,
            f"{held.astimezone(service._KST):%m/%d} · "
            + ("게시됨" if read.status == "posted" else "초안")
            + (f" · {read.edited_by_name} 수정" if read.edited_by_name else ""),
        )
        for read, draft_id, held in rows
    ]
    for item in items:
        item["body"] = item["body"][:_LIST_BODY_CHARS]
    if not items:
        return _result(summary="조건에 맞는 회의 리포트가 없습니다.", items=[])
    shown = max(1, min(limit, MAX_ITEMS))
    result = _result(summary=f"회의 리포트 {min(len(items), shown)}건입니다.", items=items[:shown])
    result["truncated"] = len(items) > shown
    return result


def meeting_report_body(session: Session, team_id: str, meeting_id: str) -> dict[str, Any]:
    """Use this to show one meeting's report text. Do not use it to find a
    meeting -- that is ``meeting_reports``.

    Returns the report as one item: its header, its body cut to 1,500
    characters, its status, its ``draft_id`` and the member who edited it.
    No item when the meeting has no report yet.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None or meeting.team_id != team_id:
        return _refused("meeting not found", "회의를 찾을 수 없습니다.")
    for read, draft_id, held in service.team_meeting_reports(session, team_id, limit=200):
        if read.meeting_id == meeting_id:
            body = f"{read.body}\n{read.footer}".strip()[:_REPORT_BODY_CHARS]
            return _result(
                summary="회의 리포트입니다.", items=[_report_item(read, draft_id, held, body)]
            )
    return _result(summary="이 회의에는 아직 리포트가 없습니다.", items=[])


TOOLS = [
    meeting_quality,
    team_trend,
    recurring_gaps,
    misalignment_risk,
    meeting_report_draft,
    meeting_report_awaiting_approval,
    meeting_report_correction,
]
"""Collected by the agent layer by iterating modules (invariant 6), never registered by hand."""

RUN_SCOPE = ("team_id",)
"""Arguments the run fills from its authenticated scope, never the model -- the
convention B's ``tools.py`` set (#492, review of #449). A model that could choose
``team_id`` could read another team's trend or post into another team's channel."""


# --- actions: E's writes, for the main agent to run --------------------------------
#
# agent-layer.md section 8, rule 2: the Report subagent never writes; it proposes,
# and the main agent runs these. Shaped like B's actions: no session argument,
# each owns its transaction and commits before anything leaves, and none is in
# ``TOOLS``, so a model never calls one directly. The module sets each one's
# level (#509): ``L1_ACTIONS`` run without approval, the rest of ``ACTIONS`` only
# after a person approves.


def _refused(reason: str, summary: str) -> dict[str, Any]:
    return _result(ok=False, reason=reason, summary=summary, items=[], confidence=0.0)


def _acted(summary: str, meeting_id: str) -> dict[str, Any]:
    # B's actions answer with the changed thing by id; an executor reads both alike.
    return _result(
        summary=summary, items=[{"title": summary, "body": "", "score": 1.0, "id": meeting_id}]
    )


def _not_found() -> dict[str, Any]:
    # Unknown and other-team read the same, so the answer does not reveal which,
    # and it never echoes the id: the model wrote it. #449's scope check says the
    # same words.
    return _refused("meeting not found", "회의를 찾을 수 없습니다.")


def draft_meeting_report(
    team_id: str,
    meeting_id: str,
    body_markdown: str,
    pending_review: bool = False,
    draft_id: str | None = None,
    replaces_draft_id: str | None = None,
) -> dict[str, Any]:
    """Store a meeting's finished report as a draft -- what the Report subagent
    proposes after a meeting's analysis finishes. Nothing is posted.

    L1 -- runs without approval; the person is told after. Posting is the separate
    ``publish_meeting_report``. Never call it with text another meeting said.
    Adds the header (title, date) and the "자동 생성" footer; the title is left out
    when it holds personal data. ``pending_review`` adds a button to B's review
    board when the report is posted. ``draft_id`` names this draft, so a post
    approved for it is not made with a later one. Replaces an unposted draft.
    ``replaces_draft_id`` is the draft a re-draft was proposed against; a changed
    or member-edited draft is then left as it is (``draft changed``).
    Refused for another team's meeting, a report already posted, one over the
    length cap, or a body that still holds personal data (by category, never
    the text).
    """
    with session_scope() as session:
        meeting = session.get(Meeting, meeting_id)
        if meeting is None or meeting.team_id != team_id:
            return _not_found()
        document = service.meeting_report_document(meeting, body_markdown)
        try:
            service.save_meeting_report(
                session,
                meeting_id,
                document,
                pending_review=pending_review,
                draft_id=draft_id,
                expected_draft_id=replaces_draft_id,
            )
        except service.DraftChangedError:
            return _refused("draft changed", "그사이 초안이 바뀌어 다시 만들지 않았습니다.")
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
    return _acted("리포트 초안을 저장했습니다.", meeting_id)


def publish_meeting_report(
    team_id: str, meeting_id: str, draft_id: str | None = None
) -> dict[str, Any]:
    """Post a meeting's stored report draft to the team channel.

    L2 -- runs only after a person approves (a channel post moves people;
    agent-layer.md section 8). Posts what ``draft_meeting_report`` stored, once:
    delivery claims the report before it posts, so a second approval or a retry
    sends nothing. With ``draft_id`` it posts that draft only. When the stored
    draft is another one, the approval posts nothing: a later run replaced it,
    or this run's draft was never stored (refused for personal data or length)
    and an earlier one is still there. Refused for another team's meeting, a
    meeting with no draft, a draft that is not the approved one, or a report
    already posted.
    """
    with session_scope() as session:
        meeting = session.get(Meeting, meeting_id)
        if meeting is None or meeting.team_id != team_id:
            return _not_found()
        row = session.get(IntelMeetingReport, meeting_id)
        if row is None:
            return _refused("no draft", "게시할 리포트 초안이 없습니다.")
        if row.sent_at is not None:
            return _refused("already posted", "이미 게시된 리포트입니다.")
        if draft_id is not None and row.draft_id != draft_id:
            # Either a later run replaced it or this run's draft was never
            # stored; the stored row cannot tell which, so the words say both.
            return _refused(
                "draft not current",
                "승인한 초안이 지금 저장된 초안이 아닙니다. "
                "새 초안으로 바뀌었거나 저장되지 않았습니다.",
            )
    # The transaction has committed: a worker that picks this up finds the row.
    try:
        # The draft can still be replaced before the task claims it; the claim checks again.
        tasks.deliver_meeting_report.apply_async((meeting_id, draft_id))
    except Exception as exc:  # the draft is stored; the caller must not see a failure
        # Stored and unclaimed: approving the post again enqueues it.
        log.warning(
            "intelligence_meeting_report_enqueue_failed",
            meeting_id=meeting_id,
            error=type(exc).__name__,
        )
    return _acted("리포트 게시를 예약했습니다.", meeting_id)


def publish_meeting_report_correction(
    team_id: str, meeting_id: str, correction_id: str
) -> dict[str, Any]:
    """Post a member's correction under the meeting's posted report.

    L2 -- runs only after a person approves (#674). Posts the correction
    ``correction_id`` names, once, as a reply under the original post (or a new
    message when that thread is out of reach). When a later correction replaced
    it, the approval posts nothing. Refused for another team's meeting and for
    a report with no such correction waiting.
    """
    with session_scope() as session:
        meeting = session.get(Meeting, meeting_id)
        if meeting is None or meeting.team_id != team_id:
            return _not_found()
        awaiting = service.meeting_report_awaiting_approval(session, meeting_id)
        if awaiting is None or awaiting.kind != "correction" or awaiting.id != correction_id:
            return _refused(
                "correction not current",
                "승인한 수정본이 지금 기다리는 수정본이 아닙니다. "
                "새 수정본으로 바뀌었거나 이미 보냈습니다.",
            )
    try:
        # The correction can still be replaced before the task claims it; the claim checks again.
        tasks.deliver_meeting_report_correction.apply_async((meeting_id, correction_id))
    except Exception as exc:  # the correction is stored; the caller must not see a failure
        log.warning(
            "intelligence_meeting_report_correction_enqueue_failed",
            meeting_id=meeting_id,
            error=type(exc).__name__,
        )
    return _acted("리포트 수정본 게시를 예약했습니다.", meeting_id)


ACTIONS = [draft_meeting_report, publish_meeting_report, publish_meeting_report_correction]
"""E's writes. Kept out of ``TOOLS`` on purpose: the registry offers ``TOOLS`` to
models, and the main agent's executor alone runs these."""

L1_ACTIONS = [draft_meeting_report]
"""The reversible ones (#509): a draft is not seen by anyone until it is posted,
and is replaced by the next draft. Everything else in ``ACTIONS`` is L2."""
