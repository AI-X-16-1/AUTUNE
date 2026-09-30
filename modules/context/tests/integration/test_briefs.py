"""Pre-meeting briefs against a real PostgreSQL: which meetings are due, which
past meeting a brief recaps, the claim, and what the recap reads back.

Rows are built directly, as ``test_notify.py`` does -- these are about the
brief, not about how topic linking or lineage built the rows it reads. The
models are the ``fake`` implementations: the re-ranker is token overlap, so a
title that shares its words with a past meeting's topics is a confident match.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, update

from autune_context import briefs
from autune_context.config import get_settings
from autune_context.models import (
    CtxBrief,
    CtxDecision,
    CtxDecisionVersion,
    CtxEmbedding,
    CtxMeetingStatus,
    CtxTeamAgenda,
)
from autune_context.pipeline import get_embedder, reset_cache
from autune_contracts import AGENDA_STALE_AFTER, AgendaIssue, ChangeType, TeamAgenda
from autune_core import (
    Meeting,
    Participant,
    Team,
    TeamMember,
    User,
    Utterance,
    session_scope,
)
from autune_core.errors import NotFoundError

NOW = datetime(2026, 9, 30, 5, 50, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fake_models(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for knob in ("EMBEDDER", "RERANKER", "NLI"):
        monkeypatch.setenv(f"AUTUNE_CONTEXT_{knob}_IMPL", "fake")
    get_settings.cache_clear()
    reset_cache()
    yield
    get_settings.cache_clear()
    reset_cache()


@pytest.fixture
def team_id(db_engine: object) -> Iterator[str]:  # db_engine ensures migrations ran
    with session_scope() as s:
        row = Team(name="brief-test")
        s.add(row)
        s.flush()
        tid = row.id
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))


@pytest.fixture
def member(team_id: str) -> Iterator[User]:
    """A user in ``team_id``, as the API's ``CurrentUser`` would be."""
    with session_scope() as s:
        user = User(email=f"{team_id}@brief.test", display_name="브리프 팀원")
        s.add(user)
        s.flush()
        s.add(TeamMember(team_id=team_id, user_id=user.id))
        s.flush()
        s.expunge(user)
    yield user
    with session_scope() as s:
        s.execute(delete(User).where(User.id == user.id))


def _scheduled(
    team_id: str,
    *,
    title: str = "주간 회의",
    starts_in: timedelta = timedelta(minutes=10),
    status: str = "scheduled",
) -> str:
    with session_scope() as s:
        row = Meeting(
            team_id=team_id,
            title=title,
            status=status,
            started_at=NOW + starts_in,
            expires_at=NOW + timedelta(days=90),
        )
        s.add(row)
        s.flush()
        return row.id


def _analyzed(
    team_id: str,
    *,
    title: str,
    days_ago: int,
    topics: tuple[str, ...] = (),
    decisions: tuple[tuple[str, ChangeType], ...] = (),
    expires_at: datetime | None = None,
    consented: bool = True,
    unchecked_topics: tuple[str, ...] = (),
) -> str:
    """A past meeting D has processed: its status row, topics and decisions.

    Each of ``topics`` is cut from one utterance by a speaker whose consent is
    ``consented``, as topic linking stores it. ``unchecked_topics`` are stored
    with no ``utterance_ids``, as rows from before that column are.
    """
    embedder = get_embedder()
    with session_scope() as s:
        meeting = Meeting(
            team_id=team_id,
            title=title,
            status="complete",
            started_at=NOW - timedelta(days=days_ago),
            expires_at=expires_at or NOW + timedelta(days=90),
        )
        s.add(meeting)
        s.flush()
        s.add(CtxMeetingStatus(meeting_id=meeting.id, topic_linking_done=True))
        speaker = Participant(meeting_id=meeting.id, speaker_label="화자", consented=consented)
        s.add(speaker)
        s.flush()
        labels = topics + unchecked_topics
        for i, (label, vector) in enumerate(zip(labels, embedder.embed(list(labels)), strict=True)):
            utterance_ids = None
            if i < len(topics):
                utterance = Utterance(
                    meeting_id=meeting.id,
                    participant_id=speaker.id,
                    speaker_label="화자",
                    start_sec=float(i),
                    end_sec=float(i) + 1,
                    text=f"{label} 이야기를 했다",
                )
                s.add(utterance)
                s.flush()
                utterance_ids = [utterance.id]
            s.add(
                CtxEmbedding(
                    meeting_id=meeting.id,
                    kind="topic",
                    ref_label=label,
                    embedding=vector,
                    model_version=embedder.model_version,
                    utterance_ids=utterance_ids,
                )
            )
        for statement, change_type in decisions:
            thread = CtxDecision(team_id=team_id, topic_label=statement)
            s.add(thread)
            s.flush()
            s.add(
                CtxDecisionVersion(
                    thread_id=thread.id,
                    source_decision_id="dec_test",
                    meeting_id=meeting.id,
                    current_statement=statement,
                    change_type=change_type.value,
                    confidence=0.9,
                    nli_version="test",
                )
            )
        return meeting.id


def _compose(meeting_id: str, *, will_send: bool = True) -> briefs.Brief | None:
    with session_scope() as s:
        return briefs.compose_due_brief(s, meeting_id, now=NOW, will_send=will_send)


# --------------------------------------------------------------------------- #
# Which meetings are due
# --------------------------------------------------------------------------- #


def test_due_is_a_scheduled_meeting_inside_the_lead_time(team_id: str) -> None:
    due = _scheduled(team_id, starts_in=timedelta(minutes=10))
    soon = _scheduled(team_id, starts_in=timedelta(minutes=2))
    later = _scheduled(team_id, starts_in=timedelta(minutes=11))
    started = _scheduled(team_id, starts_in=timedelta(minutes=-1))
    recording = _scheduled(team_id, status="recording")

    with session_scope() as s:
        ids = {meeting_id for meeting_id, _ in briefs.due_meeting_starts(s, NOW)}

    assert {due, soon} <= ids
    assert not {later, started, recording} & ids


def test_a_meeting_with_a_brief_is_no_longer_due(team_id: str) -> None:
    meeting = _scheduled(team_id)
    _compose(meeting)

    with session_scope() as s:
        assert meeting not in {m for m, _ in briefs.due_meeting_starts(s, NOW)}


# --------------------------------------------------------------------------- #
# Which past meeting it recaps
# --------------------------------------------------------------------------- #


def test_a_recurring_meeting_recaps_its_last_occurrence(team_id: str) -> None:
    last_week = _analyzed(team_id, title="주간  회의", days_ago=7)
    _analyzed(team_id, title="결제 모듈 점검", days_ago=1)  # newer, but another series
    meeting = _scheduled(team_id, title="주간 회의")

    brief = _compose(meeting)

    assert brief is not None
    assert brief.recap is not None and brief.recap.meeting_id == last_week
    assert brief.match_reason == briefs.SERIES


def test_a_new_title_recaps_the_meeting_that_discussed_its_topic(team_id: str) -> None:
    about_search = _analyzed(team_id, title="스프린트 3", days_ago=5, topics=("검색 정렬 개선",))
    _analyzed(team_id, title="스프린트 4", days_ago=1, topics=("결제 모듈 출시",))
    meeting = _scheduled(team_id, title="검색 정렬 개선")

    brief = _compose(meeting)

    assert brief is not None
    assert brief.recap is not None and brief.recap.meeting_id == about_search
    assert brief.match_reason == briefs.TOPIC


def test_with_no_confident_match_the_latest_meeting_is_recapped(team_id: str) -> None:
    _analyzed(team_id, title="스프린트 3", days_ago=5, topics=("검색 정렬",))
    latest = _analyzed(team_id, title="스프린트 4", days_ago=1, topics=("결제 모듈",))
    meeting = _scheduled(team_id, title="분기 회고")

    brief = _compose(meeting)

    assert brief is not None
    assert brief.recap is not None and brief.recap.meeting_id == latest
    assert brief.match_reason == briefs.LATEST


def test_a_model_failure_falls_back_to_the_latest_meeting(
    team_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _analyzed(team_id, title="스프린트 3", days_ago=5, topics=("검색 정렬 개선",))
    latest = _analyzed(team_id, title="스프린트 4", days_ago=1)
    meeting = _scheduled(team_id, title="검색 정렬 개선")

    def _down(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("embedder unreachable")

    monkeypatch.setattr(briefs, "_topic_match", _down)
    brief = _compose(meeting)

    assert brief is not None
    assert brief.recap is not None and brief.recap.meeting_id == latest


def test_expired_and_unanalyzed_meetings_are_never_recapped(team_id: str) -> None:
    _analyzed(team_id, title="주간 회의", days_ago=3, expires_at=NOW - timedelta(minutes=1))
    with session_scope() as s:  # never processed by D: no status row
        s.add(Meeting(team_id=team_id, title="주간 회의", status="failed", started_at=NOW))
    meeting = _scheduled(team_id)

    brief = _compose(meeting)

    assert brief is not None
    assert brief.recap is None and brief.match_reason is None
    assert brief.recap_gone is False


# --------------------------------------------------------------------------- #
# The claim, and what reads back
# --------------------------------------------------------------------------- #


def test_the_recap_carries_topics_and_decisions_with_their_change(team_id: str) -> None:
    _analyzed(
        team_id,
        title="주간 회의",
        days_ago=7,
        topics=("검색 정렬", "결제 모듈", "검색 정렬"),
        decisions=(("결제 모듈 출시는 10월로 미룬다", ChangeType.REVERSED),),
    )
    meeting = _scheduled(team_id)

    brief = _compose(meeting)

    assert brief is not None and brief.recap is not None
    assert brief.recap.topics == ("검색 정렬", "결제 모듈")
    assert [(d.statement, d.change_type) for d in brief.recap.decisions] == [
        ("결제 모듈 출시는 10월로 미룬다", ChangeType.REVERSED)
    ]


def test_a_label_from_a_speaker_who_did_not_consent_is_not_recapped(team_id: str) -> None:
    """The brief goes to the team's Slack channel; a label cut from speech
    without consent must not (#437 review). The decisions still come through."""
    _analyzed(
        team_id,
        title="주간 회의",
        days_ago=7,
        topics=("검색 정렬",),
        decisions=(("결제 모듈 출시는 10월로 미룬다", ChangeType.MODIFIED),),
        consented=False,
    )
    meeting = _scheduled(team_id)

    brief = _compose(meeting)

    assert brief is not None and brief.recap is not None
    assert brief.recap.topics == ()
    assert [d.statement for d in brief.recap.decisions] == ["결제 모듈 출시는 10월로 미룬다"]


def test_a_label_whose_speech_cannot_be_checked_is_not_recapped(team_id: str) -> None:
    """Stored with no ``utterance_ids`` -- before that column, so possibly before
    the consent filter -- a label cannot be shown to come from consenting speech."""
    _analyzed(
        team_id,
        title="주간 회의",
        days_ago=7,
        topics=("결제 모듈",),
        unchecked_topics=("검색 정렬",),
    )
    meeting = _scheduled(team_id)

    brief = _compose(meeting)

    assert brief is not None and brief.recap is not None
    assert brief.recap.topics == ("결제 모듈",)


def test_a_withdrawn_consent_drops_the_label_from_a_brief_already_composed(
    team_id: str, member: User
) -> None:
    previous = _analyzed(team_id, title="주간 회의", days_ago=7, topics=("검색 정렬",))
    meeting = _scheduled(team_id)
    composed = _compose(meeting)
    assert composed is not None and composed.recap is not None
    assert composed.recap.topics == ("검색 정렬",)

    with session_scope() as s:
        s.execute(
            update(Participant).where(Participant.meeting_id == previous).values(consented=False)
        )
    with session_scope() as s:
        brief = briefs.get_brief(s, meeting, member, now=NOW)

    assert brief.recap is not None and brief.recap.topics == ()


def test_a_brief_is_claimed_once(team_id: str) -> None:
    meeting = _scheduled(team_id)

    first = _compose(meeting)
    second = _compose(meeting)

    assert first is not None and first.sent_at == NOW
    assert second is None


def test_a_team_without_slack_gets_a_brief_that_is_not_marked_sent(team_id: str) -> None:
    meeting = _scheduled(team_id)

    brief = _compose(meeting, will_send=False)

    assert brief is not None and brief.sent_at is None
    with session_scope() as s:
        row = s.get(CtxBrief, meeting)
        assert row is not None and row.sent_at is None


def test_a_meeting_that_already_started_gets_no_brief(team_id: str) -> None:
    meeting = _scheduled(team_id, status="recording")

    assert _compose(meeting) is None
    with session_scope() as s:
        assert s.get(CtxBrief, meeting) is None


def test_the_recap_is_read_live_and_shows_a_deleted_meeting_as_gone(
    team_id: str, member: User
) -> None:
    previous = _analyzed(team_id, title="주간 회의", days_ago=7, topics=("검색 정렬",))
    meeting = _scheduled(team_id)
    _compose(meeting)

    with session_scope() as s:
        s.execute(delete(Meeting).where(Meeting.id == previous))

    with session_scope() as s:
        brief = briefs.get_brief(s, meeting, member, now=NOW)
        row = s.get(CtxBrief, meeting)
        assert row is not None and row.previous_meeting_id is None  # SET NULL
    assert brief.recap is None
    assert brief.recap_gone is True


def test_an_expired_past_meeting_is_gone_before_the_sweep_runs(team_id: str, member: User) -> None:
    _analyzed(team_id, title="주간 회의", days_ago=7, expires_at=NOW + timedelta(hours=1))
    meeting = _scheduled(team_id)
    _compose(meeting)

    with session_scope() as s:
        brief = briefs.get_brief(s, meeting, member, now=NOW + timedelta(hours=2))

    assert brief.recap is None
    assert brief.recap_gone is True


def test_no_brief_is_readable_before_it_is_composed(team_id: str, member: User) -> None:
    meeting = _scheduled(team_id)

    with session_scope() as s, pytest.raises(NotFoundError):
        briefs.get_brief(s, meeting, member, now=NOW)


def test_another_teams_brief_reads_as_missing(team_id: str, member: User) -> None:
    # Same answer as an unknown id: a 403 would confirm the meeting exists.
    with session_scope() as s:
        other = Team(name="brief-test-other")
        s.add(other)
        s.flush()
        other_team = other.id
    try:
        meeting = _scheduled(other_team)
        _compose(meeting)

        with session_scope() as s, pytest.raises(NotFoundError):
            briefs.get_brief(s, meeting, member, now=NOW)
    finally:
        with session_scope() as s:
            s.execute(delete(Team).where(Team.id == other_team))


def test_deleting_the_scheduled_meeting_deletes_its_brief(team_id: str) -> None:
    meeting = _scheduled(team_id)
    _compose(meeting)

    with session_scope() as s:
        s.execute(delete(Meeting).where(Meeting.id == meeting))

    with session_scope() as s:
        assert s.get(CtxBrief, meeting) is None


# --------------------------------------------------------------------------- #
# The agenda, from B's TeamAgenda (#436)
# --------------------------------------------------------------------------- #


def _store(team_id: str, *titles: str, as_of: datetime = NOW) -> bool:
    agenda = TeamAgenda(
        team_id=team_id,
        as_of=as_of,
        issues=[
            AgendaIssue(
                title=title,
                key=f"AUT-{i}",
                status="진행 중",
                url=f"https://example.atlassian.net/browse/AUT-{i}",
            )
            for i, title in enumerate(titles, start=1)
        ],
    )
    with session_scope() as s:
        return briefs.store_team_agenda(s, agenda)


def _agenda_titles(team_id: str, *, now: datetime = NOW) -> list[str]:
    meeting_id = _scheduled(team_id)
    with session_scope() as s:
        meeting = s.get(Meeting, meeting_id)
        assert meeting is not None
        return [item.title for item in briefs.agenda_for(s, meeting, now=now)]


def test_the_brief_carries_the_teams_open_issues_in_bs_order(team_id: str) -> None:
    _store(team_id, "결제 모듈 API 명세 정리", "온보딩 화면 시안 공유")
    meeting_id = _scheduled(team_id)

    brief = _compose(meeting_id)

    assert brief is not None
    assert [item.title for item in brief.agenda] == [
        "결제 모듈 API 명세 정리",
        "온보딩 화면 시안 공유",
    ]
    assert brief.agenda[0].key == "AUT-1"
    assert brief.agenda[0].url == "https://example.atlassian.net/browse/AUT-1"


def test_a_snapshot_delivered_late_does_not_replace_a_newer_one(team_id: str) -> None:
    assert _store(team_id, "새 목록", as_of=NOW)
    assert not _store(team_id, "옛 목록", as_of=NOW - timedelta(minutes=5))

    assert _agenda_titles(team_id) == ["새 목록"]


def test_an_empty_snapshot_clears_the_agenda(team_id: str) -> None:
    """B's way of saying the team's last issue closed."""
    _store(team_id, "끝난 일", as_of=NOW - timedelta(minutes=5))
    _store(team_id, as_of=NOW)

    assert _agenda_titles(team_id) == []


def test_a_snapshot_b_stopped_refreshing_is_not_shown(team_id: str) -> None:
    max_age = AGENDA_STALE_AFTER
    _store(team_id, "오래된 이슈", as_of=NOW - max_age - timedelta(minutes=1))

    assert _agenda_titles(team_id) == []


def test_stale_snapshots_are_deleted_and_fresh_ones_kept(team_id: str) -> None:
    max_age = AGENDA_STALE_AFTER
    _store(team_id, "오래된 이슈", as_of=NOW - max_age - timedelta(minutes=1))
    with session_scope() as s:
        other = Team(name="agenda-fresh")
        s.add(other)
        s.flush()
        fresh_team = other.id
    _store(fresh_team, "지금 이슈", as_of=NOW)

    with session_scope() as s:
        briefs.purge_stale_agendas(s, NOW)
    with session_scope() as s:
        assert s.get(CtxTeamAgenda, team_id) is None
        assert s.get(CtxTeamAgenda, fresh_team) is not None
        s.execute(delete(Team).where(Team.id == fresh_team))


def test_an_agenda_for_a_team_that_does_not_exist_is_dropped(db_engine: object) -> None:
    assert not _store("team_gone", "아무 이슈")

    with session_scope() as s:
        assert s.get(CtxTeamAgenda, "team_gone") is None


def test_deleting_the_team_deletes_its_agenda(db_engine: object) -> None:
    with session_scope() as s:
        row = Team(name="agenda-cascade")
        s.add(row)
        s.flush()
        doomed = row.id
    _store(doomed, "팀과 함께 사라질 이슈")

    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == doomed))
    with session_scope() as s:
        assert s.get(CtxTeamAgenda, doomed) is None
