"""Integration tests for the service functions and routes behind
``/api/context``.

Exercises ``get_topic_links``, ``confirm_topic_link``, ``get_decision_lineage``
and ``list_decisions`` against a real PostgreSQL — the visibility joins these
share with the write side (``_thread_heads``, ``_rethread``) are exactly what a
unit test's fakes can't stand in for. A handful of tests call the ``router``
functions directly (they're plain callables — FastAPI's ``Depends``/DI only
matters when going through the actual ASGI app) to cover response-shaping
logic that lives in ``router.py`` rather than ``service.py``, since this
repo has no HTTP test harness for any module yet.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from autune_context import router, service
from autune_context.config import get_settings
from autune_context.models import CtxDecision, CtxDecisionVersion, CtxTopicLink
from autune_context.pipeline import reset_cache
from autune_contracts.extraction import Decision, ExtractionResult
from autune_core import Meeting, Team, session_scope
from autune_core.errors import ConflictError, NotFoundError

_D1 = "검색 정렬은 최신순으로 한다"


@pytest.fixture(autouse=True)
def _fake_models(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for knob in ("EMBEDDER", "RERANKER", "NLI", "LLM"):
        monkeypatch.setenv(f"AUTUNE_CONTEXT_{knob}_IMPL", "fake")
    get_settings.cache_clear()
    reset_cache()
    yield
    get_settings.cache_clear()
    reset_cache()


@pytest.fixture
def team_id(db_engine: object) -> Iterator[str]:  # db_engine ensures migrations ran
    with session_scope() as s:
        row = Team(name="read-api-test")
        s.add(row)
        s.flush()
        tid = row.id
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))


def _meeting(team_id: str, *, days_ago: int = 0, expires_at: datetime | None = None) -> str:
    with session_scope() as s:
        row = Meeting(
            team_id=team_id,
            title="회의",
            status="analyzing",
            started_at=datetime.now(tz=UTC) - timedelta(days=days_ago),
            expires_at=expires_at,
        )
        s.add(row)
        s.flush()
        return row.id


def _extraction(meeting_id: str, decisions: list[tuple[str, str, float]]) -> ExtractionResult:
    return ExtractionResult(
        meeting_id=meeting_id,
        decisions=[
            Decision(id=dec_id, statement=statement, source_utterance_ids=[], confidence=conf)
            for dec_id, statement, conf in decisions
        ],
    )


def _topic_link(meeting_id: str, *, status: str, confidence: float = 0.5) -> int:
    with session_scope() as s:
        row = CtxTopicLink(
            meeting_id=meeting_id,
            topic_label="검색 정렬",
            similarity=0.7,
            rerank_score=0.7,
            confidence=confidence,
            status=status,
            retriever_version="test",
            reranker_version="test",
        )
        s.add(row)
        s.flush()
        return row.id


def _decision_thread(
    team_id: str, meeting_id: str, topic_label: str, *, change_type: str = "new"
) -> str:
    """A thread + single version, built directly rather than through
    ``build_decision_lineage`` — the topic-filter tests don't want their
    threading to depend on the fake embedder's similarity scores."""
    with session_scope() as s:
        thread = CtxDecision(team_id=team_id, topic_label=topic_label)
        s.add(thread)
        s.flush()
        s.add(
            CtxDecisionVersion(
                thread_id=thread.id,
                source_decision_id="dec_direct",
                meeting_id=meeting_id,
                current_statement=topic_label,
                change_type=change_type,
                confidence=0.9,
                nli_version="test",
            )
        )
        return thread.id


def _thread_id_for(meeting_id: str) -> str:
    with session_scope() as s:
        return s.scalar(
            select(CtxDecisionVersion.thread_id).where(CtxDecisionVersion.meeting_id == meeting_id)
        )


# --------------------------------------------------------------------------- #
# get_topic_links / confirm_topic_link
# --------------------------------------------------------------------------- #


def test_topic_links_group_asserted_and_confirmed_apart_from_pending(team_id: str) -> None:
    meeting = _meeting(team_id)
    asserted_id = _topic_link(meeting, status="asserted", confidence=0.9)
    confirmed_id = _topic_link(meeting, status="confirmed", confidence=0.8)
    pending_id = _topic_link(meeting, status="pending", confidence=0.5)
    _topic_link(meeting, status="rejected", confidence=0.5)

    with session_scope() as s:
        asserted, pending = service.get_topic_links(s, meeting)
        assert {link.id for link in asserted} == {asserted_id, confirmed_id}
        assert {link.id for link in pending} == {pending_id}


def test_confirming_a_pending_link_updates_its_status(team_id: str) -> None:
    meeting = _meeting(team_id)
    link_id = _topic_link(meeting, status="pending")

    with session_scope() as s:
        link = service.confirm_topic_link(s, link_id, "confirmed")
        assert link.status == "confirmed"

    with session_scope() as s:
        assert s.get(CtxTopicLink, link_id).status == "confirmed"


def test_confirming_an_already_settled_link_conflicts(team_id: str) -> None:
    meeting = _meeting(team_id)
    link_id = _topic_link(meeting, status="asserted")

    with session_scope() as s, pytest.raises(ConflictError):
        service.confirm_topic_link(s, link_id, "confirmed")


def test_confirming_an_unknown_link_404s(team_id: str) -> None:
    with session_scope() as s, pytest.raises(NotFoundError):
        service.confirm_topic_link(s, 999_999_999, "confirmed")


def test_topic_links_of_an_expired_meeting_are_hidden(team_id: str) -> None:
    meeting = _meeting(team_id, expires_at=datetime.now(tz=UTC) - timedelta(days=1))
    _topic_link(meeting, status="asserted")
    _topic_link(meeting, status="pending")

    with session_scope() as s:
        asserted, pending = service.get_topic_links(s, meeting)
        assert asserted == []
        assert pending == []


def test_confirming_a_link_on_an_expired_meeting_404s(team_id: str) -> None:
    meeting = _meeting(team_id, expires_at=datetime.now(tz=UTC) - timedelta(days=1))
    link_id = _topic_link(meeting, status="pending")

    with session_scope() as s, pytest.raises(NotFoundError):
        service.confirm_topic_link(s, link_id, "confirmed")


# --------------------------------------------------------------------------- #
# get_decision_lineage
# --------------------------------------------------------------------------- #


def test_lineage_walks_the_chain_oldest_first(team_id: str) -> None:
    first = _meeting(team_id, days_ago=10)
    second = _meeting(team_id, days_ago=0)
    service.build_decision_lineage(_extraction(first, [("dec_1", _D1, 0.9)]))
    service.build_decision_lineage(_extraction(second, [("dec_2", _D1, 0.8)]))

    thread_id = _thread_id_for(first)
    with session_scope() as s:
        thread, versions, _visible_prior = service.get_decision_lineage(s, thread_id)
        assert thread.id == thread_id
        assert [v.meeting_id for v in versions] == [first, second]
        assert versions[0].change_type == "new"
        assert versions[1].change_type == "unchanged"


def test_unknown_thread_404s(team_id: str) -> None:
    with session_scope() as s, pytest.raises(NotFoundError):
        service.get_decision_lineage(s, "thr_does_not_exist")


def test_lineage_drops_a_since_expired_version_but_keeps_the_rest(team_id: str) -> None:
    # Both meetings are visible when the thread is built, so `second` correctly
    # threads onto `first`. Only afterwards does `first` age past retention —
    # nothing re-chains the thread on a mere expiry (see the docstring on
    # `get_decision_lineage`), so this also checks the read survives a
    # previous_version_id chain whose root is no longer visible.
    first = _meeting(team_id, days_ago=10)
    second = _meeting(team_id, days_ago=0)
    service.build_decision_lineage(_extraction(first, [("dec_1", _D1, 0.9)]))
    service.build_decision_lineage(_extraction(second, [("dec_2", _D1, 0.8)]))

    thread_id = _thread_id_for(second)
    with session_scope() as s:
        s.get(Meeting, first).expires_at = datetime.now(tz=UTC) - timedelta(days=1)

    with session_scope() as s:
        _thread, versions, visible_prior = service.get_decision_lineage(s, thread_id)
        assert [v.meeting_id for v in versions] == [second]
        # `second`'s row still quotes `first` in previous_meeting_id/statement
        # (only an actual deletion, not a mere expiry, blanks it) — the caller
        # is expected to mask that using this set, since `first` isn't in it.
        assert versions[0].previous_meeting_id == first
        assert visible_prior == set()


def test_lineage_of_a_fully_expired_thread_404s(team_id: str) -> None:
    meeting = _meeting(team_id)
    service.build_decision_lineage(_extraction(meeting, [("dec_1", _D1, 0.9)]))
    thread_id = _thread_id_for(meeting)

    with session_scope() as s:
        s.get(Meeting, meeting).expires_at = datetime.now(tz=UTC) - timedelta(days=1)

    with session_scope() as s, pytest.raises(NotFoundError):
        service.get_decision_lineage(s, thread_id)


# --------------------------------------------------------------------------- #
# list_decisions
# --------------------------------------------------------------------------- #


def test_list_decisions_filters_by_topic_and_change_type(team_id: str) -> None:
    a1 = _meeting(team_id, days_ago=10)
    a2 = _meeting(team_id, days_ago=0)
    b1 = _meeting(team_id, days_ago=5)
    service.build_decision_lineage(_extraction(a1, [("dec_a1", "예산은 5천만원으로 한다", 0.9)]))
    service.build_decision_lineage(_extraction(a2, [("dec_a2", "예산은 5천만원으로 한다", 0.8)]))
    service.build_decision_lineage(_extraction(b1, [("dec_b1", "다음 회의는 화요일에 한다", 0.9)]))

    with session_scope() as s:
        all_threads = service.list_decisions(s, team_id)
        assert len(all_threads) == 2

        by_topic = service.list_decisions(s, team_id, topic="예산")
        assert len(by_topic) == 1
        thread, version = by_topic[0]
        assert thread.topic_label == "예산은 5천만원으로 한다"
        assert version.meeting_id == a2  # the thread's current head

        by_change = service.list_decisions(s, team_id, change_type="new")
        b1_thread_id = s.scalar(
            select(CtxDecisionVersion.thread_id).where(CtxDecisionVersion.meeting_id == b1)
        )
        assert {t.id for t, _v in by_change} == {b1_thread_id}


def test_list_decisions_is_scoped_to_the_team(team_id: str) -> None:
    with session_scope() as s2:
        other_team = Team(name="read-api-other-team")
        s2.add(other_team)
        s2.flush()
        other_team_id = other_team.id
    try:
        meeting = _meeting(other_team_id)
        service.build_decision_lineage(_extraction(meeting, [("dec_1", _D1, 0.9)]))

        with session_scope() as s:
            assert service.list_decisions(s, team_id) == []
            assert len(service.list_decisions(s, other_team_id)) == 1
    finally:
        with session_scope() as s2:
            s2.execute(delete(Team).where(Team.id == other_team_id))


def test_list_decisions_topic_filter_treats_percent_and_underscore_literally(
    team_id: str,
) -> None:
    exact_meeting = _meeting(team_id)
    decoy_meeting = _meeting(team_id)
    _decision_thread(team_id, exact_meeting, "예산_초안")
    # If `_` in the query were left as a live SQL wildcard (matches any one
    # character) rather than escaped, this decoy would match too.
    _decision_thread(team_id, decoy_meeting, "예산X초안")

    with session_scope() as s:
        matches = service.list_decisions(s, team_id, topic="산_초")
        assert {thread.topic_label for thread, _v in matches} == {"예산_초안"}


# --------------------------------------------------------------------------- #
# topic_label is derived from the live head, not the cached column
# --------------------------------------------------------------------------- #


def _thread_with_a_since_expired_head(
    team_id: str,
) -> tuple[str, str, str]:
    """A -> B thread, built directly (not through build_decision_lineage, so
    this doesn't depend on the fake embedder actually matching two distinct
    statements into one thread), with ``topic_label`` cached to B's wording
    the way ``_rethread`` would set it. B's meeting is then expired, leaving A
    as the only visible version. Returns (thread_id, a_statement, b_statement).
    """
    older = _meeting(team_id, days_ago=10)
    newer = _meeting(team_id, days_ago=0)
    a_statement = "예산은 5천만원으로 한다"
    b_statement = "예산은 6천만원으로 한다"
    with session_scope() as s:
        thread = CtxDecision(team_id=team_id, topic_label=b_statement)
        s.add(thread)
        s.flush()
        version_a = CtxDecisionVersion(
            thread_id=thread.id,
            source_decision_id="dec_a",
            meeting_id=older,
            current_statement=a_statement,
            change_type="new",
            confidence=0.9,
            nli_version="test",
        )
        s.add(version_a)
        s.flush()
        s.add(
            CtxDecisionVersion(
                thread_id=thread.id,
                source_decision_id="dec_b",
                meeting_id=newer,
                previous_version_id=version_a.id,
                previous_statement=a_statement,
                previous_meeting_id=older,
                current_statement=b_statement,
                change_type="modified",
                nli_label="neutral",
                confidence=0.7,
                nli_version="test",
            )
        )
        thread_id = thread.id

    with session_scope() as s:
        s.get(Meeting, newer).expires_at = datetime.now(tz=UTC) - timedelta(days=1)

    return thread_id, a_statement, b_statement


def test_lineage_topic_label_uses_the_visible_head_not_the_stale_cache(team_id: str) -> None:
    thread_id, a_statement, b_statement = _thread_with_a_since_expired_head(team_id)

    with session_scope() as s:
        result = router.get_decision_thread(thread_id, s)
        assert result.topic_label == a_statement
        assert result.topic_label != b_statement
        assert [v.current_statement for v in result.versions] == [a_statement]


def test_list_decisions_topic_label_uses_the_visible_head_not_the_stale_cache(
    team_id: str,
) -> None:
    thread_id, a_statement, _b_statement = _thread_with_a_since_expired_head(team_id)

    with session_scope() as s:
        results = router.list_decision_threads(team_id, s)
        match = next(r for r in results if r.thread_id == thread_id)
        assert match.topic_label == a_statement


def test_list_decisions_topic_filter_matches_the_visible_head_not_the_stale_cache(
    team_id: str,
) -> None:
    thread_id, a_statement, b_statement = _thread_with_a_since_expired_head(team_id)

    with session_scope() as s:
        # The now-invisible head's cached wording no longer matches...
        assert service.list_decisions(s, team_id, topic=b_statement) == []
        # ...but the still-visible version's own wording does.
        by_a = service.list_decisions(s, team_id, topic=a_statement)
        assert {t.id for t, _v in by_a} == {thread_id}


# --------------------------------------------------------------------------- #
# get_topic_links — deletion of the linked meeting
# --------------------------------------------------------------------------- #


def test_topic_link_date_survives_linked_meeting_deletion(team_id: str) -> None:
    meeting = _meeting(team_id)
    linked_meeting = _meeting(team_id, days_ago=5)
    linked_date = datetime.now(tz=UTC) - timedelta(days=5)
    with session_scope() as s:
        link = CtxTopicLink(
            meeting_id=meeting,
            topic_label="검색 정렬",
            linked_meeting_id=linked_meeting,
            linked_meeting_date=linked_date,
            similarity=0.7,
            rerank_score=0.7,
            confidence=0.8,
            status="asserted",
            retriever_version="test",
            reranker_version="test",
        )
        s.add(link)
        s.flush()
        link_id = link.id

    with session_scope() as s:
        s.execute(delete(Meeting).where(Meeting.id == linked_meeting))

    with session_scope() as s:
        asserted, _pending = service.get_topic_links(s, meeting)
        row = next(link for link in asserted if link.id == link_id)
        # `linked_meeting_id` is SET NULL by the FK; `linked_meeting_date` is a
        # plain column the deletion deliberately leaves alone (a date is not
        # reconstructed content — see docs/modules/context.md, "Deletion").
        assert row.linked_meeting_id is None
        assert row.linked_meeting_date is not None
