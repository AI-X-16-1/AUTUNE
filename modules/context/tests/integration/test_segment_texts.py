"""``HybridRetriever.segment_texts``: every topic segment of a past meeting, and
only what a consenting speaker said -- what the keyphrase check behind a link
reads (``service._subjects_agree``)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete

from autune_context.constants import EMBEDDING_DIM
from autune_context.models import CtxEmbedding
from autune_context.pipeline.retrieval import HybridRetriever
from autune_core import Meeting, Participant, Team, Utterance, session_scope


@pytest.fixture
def meeting_id(db_engine: object) -> Iterator[str]:  # db_engine ensures migrations ran
    with session_scope() as s:
        team = Team(name="segment-texts-test")
        s.add(team)
        s.flush()
        meeting = Meeting(
            team_id=team.id, title="회의", status="analyzing", started_at=datetime.now(tz=UTC)
        )
        s.add(meeting)
        s.flush()
        yes = Participant(meeting_id=meeting.id, speaker_label="SPEAKER_00", consented=True)
        no = Participant(meeting_id=meeting.id, speaker_label="SPEAKER_01", consented=False)
        s.add_all([yes, no])
        s.flush()
        lines = [
            (yes, "도크 대기 시간이 길어요."),
            (no, "제 전화번호는 따로 드릴게요."),
            (yes, "입고 예약제를 하죠."),
            (yes, "지게차 통로가 겹쳐요."),
        ]
        for i, (who, text) in enumerate(lines):
            s.add(
                Utterance(
                    id=f"utt_{meeting.id}_{i}",
                    meeting_id=meeting.id,
                    participant_id=who.id,
                    speaker_label=who.speaker_label,
                    start_sec=float(i),
                    end_sec=float(i) + 1,
                    text=text,
                    confidence=0.9,
                )
            )
        for label, ids in (("도크", [0, 1, 2]), ("지게차", [3])):
            s.add(
                CtxEmbedding(
                    meeting_id=meeting.id,
                    kind="topic",
                    ref_label=label,
                    utterance_ids=[f"utt_{meeting.id}_{i}" for i in ids],
                    embedding=[0.0] * EMBEDDING_DIM,
                    model_version="test",
                )
            )
        team_id, mid = team.id, meeting.id
    yield mid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == team_id))


def test_every_segment_is_read_and_no_unconsented_line_is(meeting_id: str) -> None:
    with session_scope() as s:
        texts = HybridRetriever(s, retrieve_top_k=50, rrf_k=60).segment_texts([meeting_id])

    dock = "도크 대기 시간이 길어요. 입고 예약제를 하죠."  # the unconsented line between is gone
    assert texts == {meeting_id: [dock, "지게차 통로가 겹쳐요."]}


def test_no_meetings_asks_nothing(db_engine: object) -> None:
    with session_scope() as s:
        assert HybridRetriever(s, retrieve_top_k=50, rrf_k=60).segment_texts([]) == {}
