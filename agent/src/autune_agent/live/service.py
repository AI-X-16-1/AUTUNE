"""Open a live document and research it (spec sections 3.3 and 4).

A document is opened ``running`` before any model call, so the panel shows
조사 중… at once, and always ends ``done`` or ``failed``. Only a
``PrivacyViolationError`` escapes, as everywhere in the layer: the row is
marked ``failed`` with no body first, so the panel does not wait forever, and
the task fails loudly. A web answer that is not masked is left out, as a
failed web call is.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autune_agent.main.gemini import WebAnswer
from autune_agent.main.registry import CallBudget, RunScope, Tool, Toolbox, collect_tools
from autune_agent.models import AgentLiveResearch, AgentLiveResearchSource
from autune_core import Meeting
from autune_core.errors import PrivacyViolationError
from autune_integrations import assert_masked

from .model import MAX_QUESTION_CHARS, LiveModel, Quote, Row, normalise

log = logging.getLogger(__name__)

MAX_AUTO = 5
MAX_MANUAL = 20
MAX_QUOTES = 5
SEARCH = "audio.search_team_meetings"
_COUNTED = ("running", "done")
"""A ``failed`` document holds no answer: it neither blocks the question nor uses up a cap."""


def known_questions(session: Session, meeting_id: str) -> list[str]:
    return list(
        session.scalars(
            select(AgentLiveResearch.question)
            .where(
                AgentLiveResearch.meeting_id == meeting_id,
                AgentLiveResearch.status.in_(_COUNTED),
            )
            .order_by(AgentLiveResearch.created_at)
        )
    )


def remaining(session: Session, meeting_id: str, origin: str) -> int:
    used = session.scalar(
        select(func.count())
        .select_from(AgentLiveResearch)
        .where(
            AgentLiveResearch.meeting_id == meeting_id,
            AgentLiveResearch.origin == origin,
            AgentLiveResearch.status.in_(_COUNTED),
        )
    )
    cap = MAX_AUTO if origin == "auto" else MAX_MANUAL
    return max(cap - int(used or 0), 0)


def open_document(
    session: Session,
    *,
    team_id: str,
    meeting_id: str,
    user_id: str | None,
    origin: str,
    question: str,
) -> AgentLiveResearch | None:
    question = " ".join(question.split())[:MAX_QUESTION_CHARS]
    if not question or remaining(session, meeting_id, origin) == 0:
        return None
    if normalise(question) in {normalise(q) for q in known_questions(session, meeting_id)}:
        return None
    assert_masked(question, destination="agent_live_research")
    doc = AgentLiveResearch(
        team_id=team_id,
        meeting_id=meeting_id,
        requested_by=user_id,
        origin=origin,
        status="running",
        question=question,
        web_sources=[],
        meeting_sources=[],
    )
    session.add(doc)
    session.commit()
    return doc


def _meeting_part(title: str) -> str:
    """``"<date> <meeting title> · <mm:ss> <speaker>"`` without the speaker."""
    head, sep, _ = title.rpartition(" · ")
    return head if sep else title


def _quotes(
    session: Session, doc: AgentLiveResearch, terms: Sequence[str], tools: Mapping[str, Tool]
) -> list[Quote]:
    box = Toolbox(
        tools,
        session,
        CallBudget(),
        allowed=(SEARCH,),
        scope=RunScope(team_id=doc.team_id, meeting_id=doc.meeting_id, user_id=doc.requested_by),
    )
    seen: set[str] = set()
    quotes: list[Quote] = []
    for term in terms:
        found = box.call(SEARCH, query=term, exclude_meeting_id=doc.meeting_id)
        for item in found.items if found.ok else []:
            uid = getattr(item, "id", None)
            meeting_id = (item.model_extra or {}).get("meeting_id") or getattr(
                item, "meeting_id", ""
            )
            if not uid or uid in seen or not meeting_id:
                continue
            seen.add(uid)
            quotes.append(
                Quote(meeting_id=meeting_id, title=_meeting_part(item.title), body=item.body)
            )
    return quotes[:MAX_QUOTES]


def research(
    session: Session,
    document_id: str,
    *,
    context: Sequence[Row],
    model: LiveModel,
    web: bool,
    terms: Sequence[str] = (),
    tools: Mapping[str, Tool] | None = None,
) -> None:
    doc = session.get(AgentLiveResearch, document_id)
    if doc is None or doc.status != "running":
        return
    try:
        _research(session, doc, context=context, model=model, web=web, terms=terms, tools=tools)
    except PrivacyViolationError:
        session.rollback()
        doc.status = "failed"
        doc.body = None
        session.commit()
        raise


def _masked_web(doc: AgentLiveResearch, answer: WebAnswer) -> WebAnswer | None:
    try:
        assert_masked(answer.text, destination="agent_live_research")
        for title, _ in answer.sources:
            assert_masked(title, destination="agent_live_research")
    except PrivacyViolationError:
        log.warning("live_research_web_masked doc=%s", doc.id)
        return None
    return answer


def _research(
    session: Session,
    doc: AgentLiveResearch,
    *,
    context: Sequence[Row],
    model: LiveModel,
    web: bool,
    terms: Sequence[str],
    tools: Mapping[str, Tool] | None,
) -> None:
    if doc.origin == "manual":
        # The pointed line is what was said; the model keeps only what to find
        # out, and that question is the one searched and shown (#1162 review).
        try:
            asked = model.ask(doc.question, context)
        except PrivacyViolationError:
            raise
        except Exception as exc:  # noqa: BLE001 - shown as 조사하지 못했습니다
            log.warning("live_research_ask_failed doc=%s error=%s", doc.id, type(exc).__name__)
            asked = None
        if asked is None:
            doc.status = "failed"
            session.commit()
            return
        assert_masked(asked.question, destination="agent_live_research")
        doc.question = asked.question
        session.commit()
        terms = terms or asked.terms
    try:
        wanted = list(terms) or model.terms(doc.question)
    except PrivacyViolationError:
        raise
    except Exception as exc:  # noqa: BLE001 - no terms is a note without past quotes
        log.warning("live_research_terms_failed doc=%s error=%s", doc.id, type(exc).__name__)
        wanted = []
    try:
        quotes = _quotes(session, doc, wanted, collect_tools() if tools is None else tools)
    except PrivacyViolationError:
        raise
    except Exception as exc:  # noqa: BLE001 - the team's meetings are one source of two
        log.warning("live_research_search_failed doc=%s error=%s", doc.id, type(exc).__name__)
        quotes = []
    answer: WebAnswer | None = None
    if web:
        try:
            answer = model.web(doc.question)
        except PrivacyViolationError:
            raise
        except Exception as exc:  # noqa: BLE001 - the web is one source of two
            log.warning("live_research_web_failed doc=%s error=%s", doc.id, type(exc).__name__)
    if answer is not None:
        answer = _masked_web(doc, answer)
    try:
        body = model.write(doc.question, context, quotes, answer)
    except PrivacyViolationError:
        raise
    except Exception as exc:  # noqa: BLE001 - shown as 조사하지 못했습니다
        log.warning("live_research_write_failed doc=%s error=%s", doc.id, type(exc).__name__)
        body = ""
    if not body:
        doc.status = "failed"
        session.commit()
        return
    assert_masked(body, destination="agent_live_research")
    titles = {q.meeting_id: q.title for q in quotes}
    doc.body = body
    doc.status = "done"
    doc.meeting_sources = [{"meeting_id": m, "title": t} for m, t in titles.items()]
    doc.web_sources = [{"title": t, "url": u} for t, u in (answer.sources if answer else [])]
    session.add_all(AgentLiveResearchSource(document_id=doc.id, meeting_id=m) for m in titles)
    session.commit()


def detect_and_research(
    session: Session,
    *,
    team_id: str,
    meeting_id: str,
    user_id: str | None,
    rows: Sequence[Row],
    model: LiveModel,
    tools: Mapping[str, Tool] | None = None,
) -> list[str]:
    # A window that waited in the queue may outlive its meeting; nothing is sent
    # for a meeting that is gone (#1162 review).
    if session.get(Meeting, meeting_id) is None or remaining(session, meeting_id, "auto") == 0:
        return []
    try:
        found = model.detect(rows, known_questions(session, meeting_id))
    except PrivacyViolationError:
        raise
    except Exception as exc:  # noqa: BLE001 - a lost window, the next one comes in 45 s
        log.warning("live_detect_failed meeting=%s error=%s", meeting_id, type(exc).__name__)
        return []
    made: list[str] = []
    for item in found:
        doc = open_document(
            session,
            team_id=team_id,
            meeting_id=meeting_id,
            user_id=user_id,
            origin="auto",
            question=item.question,
        )
        if doc is None:
            continue
        research(
            session, doc.id, context=rows, model=model, web=item.web, terms=item.terms, tools=tools
        )
        made.append(doc.id)
    return made
