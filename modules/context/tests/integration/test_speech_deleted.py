"""A person deleting their own speech takes their words out of D (#587, #614).

Seeds the rows directly, as C's test does: what is under test is which rows the
hook changes, not how linking or lineage built them. ``service`` opens its own
sessions, so these commit real rows and clean up by deleting the team.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from autune_context import service
from autune_context.config import get_settings
from autune_context.constants import EMBEDDING_DIM, SPEECH_DELETED_TEXT
from autune_context.models import (
    CtxDecision,
    CtxDecisionVersion,
    CtxEmbedding,
    CtxTeamAgenda,
    CtxTopicLink,
)
from autune_context.pipeline import reset_cache
from autune_contracts.extraction import Decision, ExtractionResult
from autune_core import Meeting, Participant, Team, Utterance, session_scope


class _NoBroker:
    def send_task(self, name: str, *, args: list) -> None:
        pass


_VECTOR = [1.0] + [0.0] * (EMBEDDING_DIM - 1)


@pytest.fixture
def team_id(db_engine: object) -> Iterator[str]:  # db_engine ensures migrations ran
    with session_scope() as s:
        row = Team(name="context-speech-deleted-test")
        s.add(row)
        s.flush()
        tid = row.id
    yield tid
    with session_scope() as s:
        s.execute(delete(CtxDecision).where(CtxDecision.team_id == tid))
        s.execute(delete(Team).where(Team.id == tid))


@dataclass(frozen=True)
class Seeded:
    earlier: str  # a meeting the person did not speak in
    meeting: str
    later: str
    mine: str
    theirs: str
    other: str  # a line in `later`, said by someone else
    thread: str
    earlier_version: int
    only_mine: int
    mine_and_theirs: int
    cited_nothing: int
    legacy: int
    follower: int


def _meeting(s, team_id: str, days_ago: int) -> str:  # noqa: ANN001
    row = Meeting(
        team_id=team_id,
        title="회의",
        status="complete",
        started_at=datetime.now(tz=UTC) - timedelta(days=days_ago),
    )
    s.add(row)
    s.flush()
    return row.id


def _line(s, meeting_id: str, label: str, text: str) -> str:  # noqa: ANN001
    person = Participant(meeting_id=meeting_id, speaker_label=label, consented=True)
    s.add(person)
    s.flush()
    line = Utterance(
        meeting_id=meeting_id,
        participant_id=person.id,
        speaker_label=label,
        start_sec=0.0,
        end_sec=1.0,
        text=text,
    )
    s.add(line)
    s.flush()
    return line.id


def _topic(meeting_id: str, label: str, said_in: list[str] | None) -> CtxEmbedding:
    return CtxEmbedding(
        meeting_id=meeting_id,
        kind="topic",
        ref_label=label,
        utterance_ids=said_in,
        embedding=_VECTOR,
        model_version="fake",
    )


def _link(meeting_id: str, label: str, past: str | None = None) -> CtxTopicLink:
    return CtxTopicLink(
        meeting_id=meeting_id,
        topic_label=label,
        linked_meeting_id=past,
        similarity=0.9,
        rerank_score=0.9,
        confidence=0.9,
        retriever_version="fake",
        reranker_version="fake",
    )


def _version(
    thread_id: str,
    meeting_id: str,
    statement: str,
    said_in: list[str] | None,
    *,
    previous: CtxDecisionVersion | None = None,
) -> CtxDecisionVersion:
    return CtxDecisionVersion(
        thread_id=thread_id,
        source_decision_id=f"dec_{statement}",
        source_utterance_ids=said_in,
        meeting_id=meeting_id,
        current_statement=statement,
        previous_version_id=previous.id if previous else None,
        previous_statement=previous.current_statement if previous else None,
        previous_meeting_id=previous.meeting_id if previous else None,
        change_type="modified" if previous else "new",
        confidence=0.9,
        nli_version="fake",
    )


def seed(team_id: str) -> Seeded:
    """Three meetings of one team. In the middle one the person spoke ("mine")
    beside somebody else ("theirs"); in the last, someone else spoke ("other")
    and its decision follows the person's.

    Topics: "개인 검색" only mine; "결제 API" in both; "배포 일정" only theirs;
    "예전 주제" with no record of its lines. One thread, the head of which is the
    person's statement, plus four more statements in the middle meeting."""
    with session_scope() as s:
        earlier = _meeting(s, team_id, 30)
        meeting = _meeting(s, team_id, 20)
        later = _meeting(s, team_id, 10)
        mine = _line(s, meeting, "화자0", "개인 검색을 먼저 하기로 했습니다")
        theirs = _line(s, meeting, "화자1", "결제 API는 다음 주에 합니다")
        before = _line(s, earlier, "화자2", "검색을 정렬한다")
        other = _line(s, later, "화자3", "개인 검색은 미룹니다")

        s.add_all(
            [
                _topic(meeting, "개인 검색", [mine]),
                _topic(meeting, "결제 API", [mine, theirs]),
                _topic(meeting, "배포 일정", [theirs]),
                _topic(meeting, "예전 주제", None),
                _link(meeting, "개인 검색", past=earlier),
                _link(meeting, "결제 API", past=earlier),
                _link(meeting, "배포 일정", past=earlier),
                _link(meeting, "예전 주제", past=earlier),
                # Another meeting's link *to* this one carries that meeting's own label.
                _link(later, "다른 회의의 주제", past=meeting),
                CtxTeamAgenda(
                    team_id=team_id,
                    as_of=datetime.now(tz=UTC),
                    issues=[{"title": "이슈", "key": "A-1"}],
                ),
            ]
        )
        thread = CtxDecision(team_id=team_id, topic_label="개인 검색은 미룬다")
        s.add(thread)
        s.flush()
        first = _version(thread.id, earlier, "검색을 정렬한다", [before])
        s.add(first)
        s.flush()
        head = _version(thread.id, meeting, "개인 검색은 미룬다", [mine], previous=first)
        s.add(head)
        s.flush()
        follower = _version(thread.id, later, "개인 검색을 다시 한다", [other], previous=head)
        both = _version(thread.id, meeting, "결제 API는 다음 주", [mine, theirs])
        nothing = _version(thread.id, meeting, "인용 없는 결정", [])
        legacy = _version(thread.id, meeting, "예전 결정", None)
        s.add_all([follower, both, nothing, legacy])
        s.flush()
        # The thread reads as the person's statement did when it opened.
        return Seeded(
            earlier=earlier,
            meeting=meeting,
            later=later,
            mine=mine,
            theirs=theirs,
            other=other,
            thread=thread.id,
            earlier_version=first.id,
            only_mine=head.id,
            mine_and_theirs=both.id,
            cited_nothing=nothing.id,
            legacy=legacy.id,
            follower=follower.id,
        )


def statement(version_id: int) -> str:
    with session_scope() as s:
        row = s.get(CtxDecisionVersion, version_id)
        assert row is not None
        return row.current_statement


def previous(version_id: int) -> str | None:
    with session_scope() as s:
        row = s.get(CtxDecisionVersion, version_id)
        assert row is not None
        return row.previous_statement


def labels(meeting_id: str, model: type) -> set[str]:
    column = model.ref_label if model is CtxEmbedding else model.topic_label
    with session_scope() as s:
        return set(s.scalars(select(column).where(model.meeting_id == meeting_id)))


def forget(ids: list[str]) -> service.SpeechForgotten:
    with session_scope() as s:
        return service.forget_speech(s, ids)


def test_a_topic_only_the_person_named_goes_with_its_embedding_and_links(team_id: str) -> None:
    seeded = seed(team_id)

    done = forget([seeded.mine])

    # "개인 검색" (only mine) and "예전 주제" (no record of its lines) go.
    assert labels(seeded.meeting, CtxEmbedding) == {"결제 API", "배포 일정"}
    assert labels(seeded.meeting, CtxTopicLink) == {"결제 API", "배포 일정"}
    assert (done.topics_deleted, done.links_deleted) == (2, 2)


def test_a_topic_somebody_else_also_named_stays(team_id: str) -> None:
    seeded = seed(team_id)

    forget([seeded.mine])

    assert "결제 API" in labels(seeded.meeting, CtxEmbedding)
    assert "결제 API" in labels(seeded.meeting, CtxTopicLink)


def _delete_lines(ids: list[str]) -> None:
    """What A does after the hook runs: the utterances themselves go."""
    with session_scope() as s:
        s.execute(delete(Utterance).where(Utterance.id.in_(ids)))


def test_a_topic_whose_lines_were_deleted_in_two_rounds_goes_with_the_second(
    team_id: str,
) -> None:
    seeded = seed(team_id)
    forget([seeded.mine])
    _delete_lines([seeded.mine])
    assert "결제 API" in labels(seeded.meeting, CtxEmbedding)  # theirs is still there

    done = forget([seeded.theirs])  # a is already gone and is not in this batch

    assert labels(seeded.meeting, CtxEmbedding) == set()
    assert labels(seeded.meeting, CtxTopicLink) == set()
    assert done.topics_deleted == 2  # "결제 API" and "배포 일정", both cut from theirs only


def test_a_topic_stays_while_a_line_outside_both_rounds_still_exists(team_id: str) -> None:
    seeded = seed(team_id)
    with session_scope() as s:
        third = _line(s, seeded.meeting, "화자4", "세 번째 사람의 말")
        s.add(_topic(seeded.meeting, "세 줄 주제", [seeded.mine, seeded.theirs, third]))
    forget([seeded.mine])
    _delete_lines([seeded.mine])

    forget([seeded.theirs])

    assert "세 줄 주제" in labels(seeded.meeting, CtxEmbedding)  # the third line still exists


def test_another_meetings_link_to_this_one_stays(team_id: str) -> None:
    seeded = seed(team_id)

    forget([seeded.mine])

    assert labels(seeded.later, CtxTopicLink) == {"다른 회의의 주제"}


def test_a_statement_drawn_only_from_the_deleted_lines_is_cleared(team_id: str) -> None:
    seeded = seed(team_id)

    done = forget([seeded.mine])

    assert statement(seeded.only_mine) == SPEECH_DELETED_TEXT
    # ... and the one with no record of its lines (written before the column).
    assert statement(seeded.legacy) == SPEECH_DELETED_TEXT
    # ... and the one that also quotes somebody else's line (next test).
    assert done.statements_cleared == 3


def test_a_statement_quoting_one_deleted_line_among_others_is_cleared(team_id: str) -> None:
    seeded = seed(team_id)

    forget([seeded.mine])

    # B joins the cited lines word for word, so the deleted line's words are in it.
    assert statement(seeded.mine_and_theirs) == SPEECH_DELETED_TEXT


def test_a_statement_whose_lines_are_all_somebody_elses_stays(team_id: str) -> None:
    seeded = seed(team_id)

    forget([seeded.other])

    assert statement(seeded.mine_and_theirs) == "결제 API는 다음 주"
    assert statement(seeded.only_mine) == "개인 검색은 미룬다"


def test_a_decision_with_no_cited_line_stays(team_id: str) -> None:
    seeded = seed(team_id)

    forget([seeded.mine])

    assert statement(seeded.cited_nothing) == "인용 없는 결정"


def test_a_meeting_the_person_did_not_speak_in_is_left_alone(team_id: str) -> None:
    seeded = seed(team_id)

    forget([seeded.mine])

    assert statement(seeded.earlier_version) == "검색을 정렬한다"
    assert statement(seeded.follower) == "개인 검색을 다시 한다"


def test_the_copy_in_the_next_version_and_the_threads_label_read_the_same(team_id: str) -> None:
    seeded = seed(team_id)

    forget([seeded.mine])

    assert previous(seeded.follower) == SPEECH_DELETED_TEXT
    with session_scope() as s:
        thread = s.get(CtxDecision, seeded.thread)
        assert thread is not None
        assert thread.topic_label == SPEECH_DELETED_TEXT
        follower = s.get(CtxDecisionVersion, seeded.follower)
        only_mine = s.get(CtxDecisionVersion, seeded.only_mine)
        assert follower is not None and only_mine is not None
        # What changed, how, and when is the team's work and stays.
        assert follower.change_type == "modified"
        assert only_mine.change_type == "modified"
        assert only_mine.previous_statement == "검색을 정렬한다"


def test_a_cleared_statement_is_no_head_to_match_a_new_decision_against(team_id: str) -> None:
    seeded = seed(team_id)
    forget([seeded.mine, seeded.other])

    class _Embedder:
        model_version = "fake"
        dim = EMBEDDING_DIM

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [_VECTOR for _ in texts]

    with session_scope() as s:
        heads = service._thread_heads(s, team_id, _Embedder())  # type: ignore[arg-type]

    assert all(head.statement != SPEECH_DELETED_TEXT for head in heads)


def test_the_teams_agenda_snapshot_is_dropped(team_id: str) -> None:
    seeded = seed(team_id)

    done = forget([seeded.mine])

    with session_scope() as s:
        assert s.get(CtxTeamAgenda, team_id) is None
    assert done.agendas_dropped == 1


def test_forgetting_twice_changes_nothing_more(team_id: str) -> None:
    seeded = seed(team_id)
    forget([seeded.mine])

    again = forget([seeded.mine])

    assert again == service.SpeechForgotten(
        topics_deleted=0, links_deleted=0, statements_cleared=0, agendas_dropped=0
    )


def test_no_utterances_and_unknown_utterances_change_nothing(team_id: str) -> None:
    seeded = seed(team_id)

    assert forget([]) == service.SpeechForgotten()
    assert forget(["utt_unknown"]) == service.SpeechForgotten()

    assert statement(seeded.only_mine) == "개인 검색은 미룬다"


def test_the_hook_runs_before_the_utterance_is_deleted(team_id: str) -> None:
    seeded = seed(team_id)

    service.forget_deleted_speech("user_x", [seeded.mine])

    assert statement(seeded.only_mine) == SPEECH_DELETED_TEXT
    with session_scope() as s:
        assert s.get(Utterance, seeded.mine) is not None  # D deletes none of A's rows


def test_a_decision_is_stored_with_the_lines_b_drew_it_from(
    team_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for knob in ("EMBEDDER", "RERANKER", "NLI", "LLM"):
        monkeypatch.setenv(f"AUTUNE_CONTEXT_{knob}_IMPL", "fake")
    monkeypatch.setattr(service, "current_app", _NoBroker())
    get_settings.cache_clear()
    reset_cache()
    with session_scope() as s:
        meeting = _meeting(s, team_id, 5)
        line = _line(s, meeting, "화자0", "검색은 인기순으로 한다")
    result = ExtractionResult(
        meeting_id=meeting,
        decisions=[
            Decision(
                id="dec_stored",
                statement="검색은 인기순",
                source_utterance_ids=[line],
                confidence=0.9,
            )
        ],
    )

    service.build_decision_lineage(result)

    with session_scope() as s:
        version = s.scalars(
            select(CtxDecisionVersion).where(CtxDecisionVersion.meeting_id == meeting)
        ).one()
        assert version.source_utterance_ids == [line]


def test_the_hook_is_registered_where_a_router_is_imported() -> None:
    """In a fresh interpreter, as the API process starts: it imports routers and
    never ``tasks``. This file has already imported ``service``, so an in-process
    check would pass without the router."""
    probe = (
        "import autune_context.router\n"
        "from autune_core.deletion import registered_speech_modules\n"
        "assert 'context' in registered_speech_modules()\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
