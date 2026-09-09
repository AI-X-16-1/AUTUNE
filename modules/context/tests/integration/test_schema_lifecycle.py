"""Deletion behaviour of the ctx_* schema against a real PostgreSQL.

Skipped until ``autune_core.testing`` ships the shared ``db_session`` fixture
(see docs/engineering/testing.md). Migration round-trip is covered by the CI
gate (``alembic upgrade heads`` then downgrade), not here.
"""

from __future__ import annotations

import pytest

pytest.importorskip("autune_core.testing")

from autune_context.models import (  # noqa: E402
    CtxDecision,
    CtxDecisionVersion,
    CtxEmbedding,
    CtxMeetingStatus,
    CtxTopicLink,
)
from autune_context.service import cleanup_orphan_decision_threads  # noqa: E402
from autune_core import Meeting, Team  # noqa: E402


def _meeting(session, team: Team) -> Meeting:
    meeting = Meeting(team_id=team.id, title="스프린트 회의")
    session.add(meeting)
    session.flush()
    return meeting


def test_deleting_a_meeting_removes_every_ctx_row_keyed_to_it(db_session):
    team = Team(name="팀")
    db_session.add(team)
    db_session.flush()
    meeting = _meeting(db_session, team)

    db_session.add_all(
        [
            CtxEmbedding(
                meeting_id=meeting.id,
                kind="topic",
                ref_label="검색 개인화",
                embedding=[0.0] * 1024,
                model_version="fake",
            ),
            CtxTopicLink(
                meeting_id=meeting.id,
                topic_label="검색 개인화",
                similarity=0.8,
                rerank_score=0.9,
                confidence=0.9,
                status="asserted",
                retriever_version="fake",
                reranker_version="fake",
            ),
            CtxMeetingStatus(meeting_id=meeting.id, topic_linking_done=True),
        ]
    )
    db_session.flush()

    db_session.delete(meeting)
    db_session.flush()

    for model in (CtxEmbedding, CtxTopicLink, CtxMeetingStatus):
        assert db_session.query(model).filter_by(meeting_id=meeting.id).count() == 0


def test_a_thread_survives_its_origin_meeting_but_an_emptied_thread_is_swept(db_session):
    team = Team(name="팀")
    db_session.add(team)
    db_session.flush()
    origin = _meeting(db_session, team)
    later = _meeting(db_session, team)

    kept = CtxDecision(team_id=team.id, topic_label="정렬 방식")
    emptied = CtxDecision(team_id=team.id, topic_label="가격 정책")
    db_session.add_all([kept, emptied])
    db_session.flush()

    db_session.add_all(
        [
            CtxDecisionVersion(
                thread_id=kept.id,
                source_decision_id="dec_1",
                meeting_id=origin.id,
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
                meeting_id=origin.id,
                current_statement="무료",
                change_type="new",
                confidence=0.7,
                nli_version="fake",
            ),
        ]
    )
    db_session.flush()

    db_session.delete(origin)
    db_session.flush()

    # `kept` still has its `later` version; `emptied` has none.
    assert db_session.get(CtxDecision, kept.id) is not None
    assert db_session.query(CtxDecisionVersion).filter_by(thread_id=kept.id).count() == 1

    cleanup_orphan_decision_threads(origin.id)

    assert db_session.get(CtxDecision, emptied.id) is None
    assert db_session.get(CtxDecision, kept.id) is not None
