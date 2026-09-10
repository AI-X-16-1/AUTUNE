"""Deletion behaviour of the ctx_* schema against a real PostgreSQL."""

from __future__ import annotations

from sqlalchemy.orm import Session

from autune_context.models import (
    CtxDecision,
    CtxDecisionVersion,
    CtxEmbedding,
    CtxMeetingStatus,
    CtxTopicLink,
)
from autune_context.service import (
    sweep_dangling_previous_statements,
    sweep_orphan_decision_threads,
)
from autune_core import Meeting


def test_deleting_a_meeting_removes_every_ctx_row_keyed_to_it(
    db_session: Session, meeting: str
) -> None:
    db_session.add_all(
        [
            CtxEmbedding(
                meeting_id=meeting,
                kind="topic",
                ref_label="검색 개인화",
                embedding=[0.0] * 1024,
                model_version="fake",
            ),
            CtxTopicLink(
                meeting_id=meeting,
                topic_label="검색 개인화",
                similarity=0.8,
                rerank_score=0.9,
                confidence=0.9,
                status="asserted",
                retriever_version="fake",
                reranker_version="fake",
            ),
            CtxMeetingStatus(meeting_id=meeting, topic_linking_done=True),
        ]
    )
    db_session.flush()

    db_session.delete(db_session.get(Meeting, meeting))
    db_session.flush()

    for model in (CtxEmbedding, CtxTopicLink, CtxMeetingStatus):
        assert db_session.query(model).filter_by(meeting_id=meeting).count() == 0


def test_a_linked_meeting_deletion_nulls_the_link_but_keeps_it(
    db_session: Session, team: str, meeting: str
) -> None:
    past = Meeting(team_id=team, title="지난 회의")
    db_session.add(past)
    db_session.flush()

    link = CtxTopicLink(
        meeting_id=meeting,
        topic_label="정렬 방식",
        linked_meeting_id=past.id,
        similarity=0.7,
        rerank_score=0.8,
        confidence=0.8,
        status="asserted",
        retriever_version="fake",
        reranker_version="fake",
    )
    db_session.add(link)
    db_session.flush()

    db_session.delete(past)
    db_session.flush()
    db_session.refresh(link)

    assert link.linked_meeting_id is None
    assert link.topic_label == "정렬 방식"


def test_a_thread_survives_its_origin_meeting_but_an_emptied_thread_is_swept(
    db_session: Session, team: str, meeting: str
) -> None:
    later = Meeting(team_id=team, title="다음 회의")
    db_session.add(later)
    db_session.flush()

    kept = CtxDecision(team_id=team, topic_label="정렬 방식")
    emptied = CtxDecision(team_id=team, topic_label="가격 정책")
    db_session.add_all([kept, emptied])
    db_session.flush()

    db_session.add_all(
        [
            CtxDecisionVersion(
                thread_id=kept.id,
                source_decision_id="dec_1",
                meeting_id=meeting,
                current_statement="인기순",
                change_type="new",
                confidence=0.9,
                nli_version="fake",
            ),
            CtxDecisionVersion(
                thread_id=kept.id,
                source_decision_id="dec_2",
                meeting_id=later.id,
                current_statement="개인화",
                change_type="modified",
                confidence=0.8,
                nli_version="fake",
            ),
            CtxDecisionVersion(
                thread_id=emptied.id,
                source_decision_id="dec_3",
                meeting_id=meeting,
                current_statement="무료",
                change_type="new",
                confidence=0.7,
                nli_version="fake",
            ),
        ]
    )
    db_session.flush()

    db_session.delete(db_session.get(Meeting, meeting))
    db_session.flush()

    # `kept` still has its `later` version; `emptied` has none.
    assert db_session.get(CtxDecision, kept.id) is not None
    assert db_session.query(CtxDecisionVersion).filter_by(thread_id=kept.id).count() == 1

    swept = sweep_orphan_decision_threads(db_session)
    db_session.flush()

    assert swept == 1
    assert db_session.get(CtxDecision, emptied.id) is None
    assert db_session.get(CtxDecision, kept.id) is not None


def test_sweep_still_clears_a_thread_when_the_versions_table_is_globally_empty(
    db_session: Session, team: str
) -> None:
    """A retention sweep can delete a team's last remaining version anywhere,
    leaving `ctx_decision_versions` empty. That must not stop the orphan sweep —
    it is exactly the case the function exists for."""
    orphan = CtxDecision(team_id=team, topic_label="가격 정책")
    db_session.add(orphan)
    db_session.flush()
    assert db_session.query(CtxDecisionVersion).count() == 0

    swept = sweep_orphan_decision_threads(db_session)
    db_session.flush()

    assert swept == 1
    assert db_session.get(CtxDecision, orphan.id) is None


def test_previous_statement_is_nulled_once_its_meeting_is_gone(
    db_session: Session, team: str, meeting: str
) -> None:
    """`previous_meeting_id` is unconstrained, so deleting `origin` leaves it
    dangling rather than cascading. `previous_statement` must not survive it —
    everything else on the version does."""
    origin = Meeting(team_id=team, title="1월 회의")
    db_session.add(origin)
    db_session.flush()
    origin_id = origin.id

    thread = CtxDecision(team_id=team, topic_label="정렬 방식")
    db_session.add(thread)
    db_session.flush()

    version = CtxDecisionVersion(
        thread_id=thread.id,
        source_decision_id="dec_1",
        meeting_id=meeting,
        current_statement="개인화",
        previous_statement="인기순",
        previous_meeting_id=origin_id,
        change_type="modified",
        nli_label="contradiction",
        confidence=0.9,
        nli_version="fake",
    )
    db_session.add(version)
    db_session.flush()

    db_session.delete(origin)
    db_session.flush()

    swept = sweep_dangling_previous_statements(db_session)
    db_session.flush()
    db_session.refresh(version)

    assert swept == 1
    assert version.previous_statement is None
    assert version.previous_meeting_id == origin_id  # left as a dangling id, not a leak
    assert version.change_type == "modified"
    assert version.nli_label == "contradiction"
    assert version.confidence == 0.9

    assert sweep_dangling_previous_statements(db_session) == 0  # idempotent


def test_previous_statement_survives_while_its_meeting_still_exists(
    db_session: Session, team: str, meeting: str
) -> None:
    origin = Meeting(team_id=team, title="1월 회의")
    db_session.add(origin)
    db_session.flush()

    thread = CtxDecision(team_id=team, topic_label="정렬 방식")
    db_session.add(thread)
    db_session.flush()

    db_session.add(
        CtxDecisionVersion(
            thread_id=thread.id,
            source_decision_id="dec_1",
            meeting_id=meeting,
            current_statement="개인화",
            previous_statement="인기순",
            previous_meeting_id=origin.id,
            change_type="modified",
            confidence=0.9,
            nli_version="fake",
        )
    )
    db_session.flush()

    assert sweep_dangling_previous_statements(db_session) == 0
