"""A failed extraction is counted, tried again, said after three tries, and a
person can ask for a meeting's extraction again (the user, 2026-10-06).

On dev, 2026-10-05, one refused request cost a meeting every action item and
decision, and nothing tried again or said so. Everything here goes through
the real tasks and routes; only the classifier, Slack and the broker are fakes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_contracts.transcript import (
    PrivacyFlags,
    TranscriptMetadata,
    TranscriptReady,
    TranscriptSource,
)
from autune_contracts.transcript import Utterance as SpokenLine
from autune_core import (
    AutuneError,
    Base,
    Meeting,
    Participant,
    PrivacyViolationError,
    TeamMember,
    User,
    Utterance,
    get_session,
)
from autune_extraction import attempts, tasks
from autune_extraction.models import (
    ExtActionItem,
    ExtClassification,
    ExtDecision,
    ExtEditEvent,
    ExtExtractionAttempt,
    ExtExtractionRun,
)
from autune_extraction.pipeline import FakeClassifier, FakeNli
from autune_extraction.router import router
from autune_integrations import TransientIntegrationError

from .conftest import sign_in

MEETING = "mtg_1"
OTHER = "mtg_2"
ELSEWHERE = "mtg_other_team"
PREFIX = "/api/extraction"
TITLE = "주간 회의"
SAID = "제가 금요일까지 정리하겠습니다"

# Endings the fake reads: 겠습니다 commitment, 기로 했 decision.
LINES = [
    ("utt_1", 0.0, "그럼 이번 분기는 A안으로 가기로 했습니다"),
    ("utt_2", 4.0, SAID),
]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Meeting, User, TeamMember, Participant, Utterance)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id="team_1", title=TITLE))
        s.add(Meeting(id=OTHER, team_id="team_1", title="기획 회의"))
        s.add(Meeting(id=ELSEWHERE, team_id="team_2", title="남의 회의"))
        for meeting_id in (MEETING, OTHER, ELSEWHERE):
            s.add(
                Participant(
                    id=f"par_{meeting_id}", meeting_id=meeting_id, speaker_label="A", consented=True
                )
            )
            for uid, start, text in LINES:
                s.add(
                    Utterance(
                        id=f"{uid}_{meeting_id}",
                        meeting_id=meeting_id,
                        participant_id=f"par_{meeting_id}",
                        speaker_label="SPEAKER_00",
                        start_sec=start,
                        end_sec=start + 3.0,
                        text=text,
                        confidence=0.9,
                    )
                )
        # Committed: the scope below rolls back as the real one does.
        s.commit()
        yield s


class Classifier(FakeClassifier):
    """The fake, failing while ``broken`` holds an error. The error's message
    quotes what was said, as a real one can."""

    broken: list[BaseException] = []
    calls = 0

    def classify(self, texts: list[str]):  # type: ignore[no-untyped-def]
        type(self).calls += 1
        if type(self).broken:
            raise type(self).broken[0]
        return super().classify(texts)


@pytest.fixture
def wired(session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    @contextmanager
    def scope() -> Iterator[Session]:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise

    Classifier.broken = []
    Classifier.calls = 0
    # A sweep in these tests is "the next sweep": whatever failed before it is
    # past the hold. The tests of the hold itself put the real one back.
    monkeypatch.setattr(attempts, "RETRY_HOLD", timedelta(0))
    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "get_classifier", Classifier)
    monkeypatch.setattr(tasks, "get_nli", FakeNli)
    monkeypatch.setattr(tasks, "publish", lambda event, payload: [])
    return session


def breaks(error: BaseException | None = None) -> None:
    Classifier.broken = [error or RuntimeError(f"the provider refused: {SAID}")]


def mends() -> None:
    Classifier.broken = []


def event(meeting_id: str = MEETING) -> dict:
    return TranscriptReady(
        meeting_id=meeting_id,
        utterances=[
            SpokenLine(
                id=f"{uid}_{meeting_id}",
                speaker="Speaker 1",
                start=start,
                end=start + 3.0,
                text=text,
                confidence=0.9,
            )
            for uid, start, text in LINES
        ],
        metadata=TranscriptMetadata(
            duration=20.0,
            source=next(iter(TranscriptSource)),
            privacy=PrivacyFlags(original_audio_deleted=True, pii_masked=True),
        ),
    ).model_dump(mode="json")


def fails(meeting_id: str = MEETING, error: BaseException | None = None) -> None:
    """The event's run of this meeting raises."""
    breaks(error)
    with pytest.raises(type(Classifier.broken[0])):
        tasks.on_transcript_ready(event(meeting_id))


def row(session: Session, meeting_id: str = MEETING) -> ExtExtractionAttempt | None:
    session.expire_all()
    return session.get(ExtExtractionAttempt, meeting_id)


class Slack:
    """Stands in for ``SlackClient``: what was posted where, or the error to raise."""

    posts: list[tuple[str, str]] = []
    error: list[BaseException] = []

    def __init__(self, secret: str) -> None:
        assert secret == "xoxb-test"

    def post_message(self, channel: str, text: str) -> str:
        if type(self).error:
            raise type(self).error[0]
        type(self).posts.append((channel, text))
        return "1.0"

    def close(self) -> None:
        pass


@pytest.fixture
def channel(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    """team_1 has Slack connected with a channel; clear ``["channel"]`` to
    disconnect it."""
    connected: dict[str, str | None] = {"channel": "C_TEAM"}
    Slack.posts = []
    Slack.error = []

    def load(_session: Session, team_id: str, service: str):  # type: ignore[no-untyped-def]
        assert (team_id, service) == ("team_1", "slack")
        if connected["channel"] is None:
            return None
        return SimpleNamespace(secret="xoxb-test", config={"channel": connected["channel"]})

    monkeypatch.setattr(tasks, "load_integration", load)
    monkeypatch.setattr(tasks, "SlackClient", Slack)
    return connected


# --- a failure is counted ---------------------------------------------------------


def test_a_run_that_raises_is_counted_and_still_raises(wired: Session) -> None:
    with capture_logs() as logs:
        fails()

    kept = row(wired)
    assert kept is not None and kept.failures == 1
    assert kept.reason == "RuntimeError" and kept.failed_at is not None
    assert wired.query(ExtClassification).count() == 0
    # The class of the error and nothing it said: its message quotes the meeting.
    (entry,) = [e for e in logs if e["event"] == "extraction_failed"]
    assert entry["reason"] == "RuntimeError" and entry["failures"] == 1
    assert SAID not in repr(logs) and SAID not in repr(vars(kept))


def test_a_count_that_cannot_be_written_does_not_hide_the_error(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def gone(*_args: object, **_kwargs: object) -> int:
        raise LookupError("the meeting was deleted")

    monkeypatch.setattr(attempts, "note_failure", gone)

    fails()  # the extraction's own error, not LookupError

    assert row(wired) is None


def test_a_run_that_goes_through_leaves_nothing_counted(wired: Session) -> None:
    tasks.on_transcript_ready(event())

    assert row(wired) is None
    assert wired.query(ExtActionItem).count() == 1


# --- the sweep tries again ----------------------------------------------------------


def test_the_sweep_tries_a_failed_meeting_again_and_a_run_that_works_ends_the_count(
    wired: Session, channel: dict
) -> None:
    fails()
    mends()

    assert tasks.retry_failed_extractions() == [MEETING]

    kept = row(wired)
    assert kept is not None and kept.failures == 0 and kept.failed_at is None
    assert wired.query(ExtActionItem).count() == 1
    assert wired.query(ExtDecision).count() == 1
    assert tasks.retry_failed_extractions() == []
    assert Slack.posts == []


def test_three_attempts_in_all_and_then_no_more(wired: Session, channel: dict) -> None:
    fails()
    assert tasks.retry_failed_extractions() == []
    assert row(wired).failures == 2  # type: ignore[union-attr]
    assert tasks.retry_failed_extractions() == []
    assert row(wired).failures == 3  # type: ignore[union-attr]
    assert Classifier.calls == 3

    tasks.retry_failed_extractions()
    tasks.retry_failed_extractions()

    assert Classifier.calls == 3
    assert row(wired).failures == 3  # type: ignore[union-attr]


def test_one_meeting_failing_again_does_not_stop_the_next(wired: Session, channel: dict) -> None:
    fails(MEETING)
    fails(OTHER)
    wired.delete(wired.get(Utterance, f"utt_2_{MEETING}"))  # no commitment left to draft
    wired.commit()
    mends()

    assert tasks.retry_failed_extractions() == [MEETING, OTHER]
    assert [i.meeting_id for i in wired.query(ExtActionItem)] == [OTHER]


def test_a_privacy_refusal_on_a_retry_is_counted_and_raised_once_the_rest_is_done(
    wired: Session, channel: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    fails(MEETING)
    fails(OTHER)
    mends()
    extract = tasks._extract

    def refusing(meeting_id: str, utterances: list) -> None:
        if meeting_id == MEETING:
            raise PrivacyViolationError("refusing to send unmasked personal data")
        extract(meeting_id, utterances)

    monkeypatch.setattr(tasks, "_extract", refusing)

    with pytest.raises(PrivacyViolationError, match=MEETING):
        tasks.retry_failed_extractions()

    assert row(wired, MEETING).failures == 2  # type: ignore[union-attr]
    assert row(wired, MEETING).reason == "PrivacyViolationError"  # type: ignore[union-attr]
    assert row(wired, OTHER).failures == 0  # type: ignore[union-attr]


# --- a meeting nobody extracted -------------------------------------------------------


def stored_since(session: Session, meeting_id: str, age: timedelta) -> None:
    """The meeting's lines as if module A had stored them ``age`` ago."""
    for line in session.query(Utterance).filter_by(meeting_id=meeting_id):
        line.created_at = datetime.now(tz=UTC) - age
    session.commit()


def test_a_transcript_with_no_extraction_on_record_is_taken_for_a_failed_one(
    wired: Session, channel: dict
) -> None:
    """dev, 2026-10-05: the run raised before failures were counted, so there
    was no row to retry from -- only module A's lines and nothing of B's."""
    stored_since(wired, MEETING, timedelta(hours=11))

    assert tasks.retry_failed_extractions() == [MEETING]

    assert wired.query(ExtActionItem).count() == 1
    assert row(wired).failures == 0  # type: ignore[union-attr]
    assert row(wired, OTHER) is None  # stored a moment ago: the event's own run has it
    assert tasks.retry_failed_extractions() == []
    assert Classifier.calls == 1


def test_an_adopted_meeting_that_keeps_failing_is_counted_from_one_and_said(
    wired: Session, channel: dict
) -> None:
    stored_since(wired, MEETING, timedelta(hours=11))
    breaks()

    tasks.retry_failed_extractions()
    assert row(wired).failures == 2  # type: ignore[union-attr]
    tasks.retry_failed_extractions()

    assert row(wired).failures == 3 and len(Slack.posts) == 1  # type: ignore[union-attr]


def test_a_meeting_already_extracted_or_too_old_is_not_adopted(
    wired: Session, channel: dict
) -> None:
    tasks.on_transcript_ready(event(MEETING))
    stored_since(wired, MEETING, timedelta(hours=11))
    stored_since(wired, OTHER, attempts.ADOPT_WINDOW + timedelta(hours=1))

    assert tasks.retry_failed_extractions() == []

    assert row(wired, MEETING) is None and row(wired, OTHER) is None
    assert Classifier.calls == 1


# --- no sweep runs a meeting twice at once, and none holds the worker ------------------

HOLD = attempts.RETRY_HOLD


def aged(session: Session, meeting_id: str, age: timedelta) -> None:
    """The meeting's last failure, or hold, as if it were ``age`` old."""
    kept = session.get(ExtExtractionAttempt, meeting_id)
    assert kept is not None
    kept.failed_at = datetime.now(tz=UTC) - age
    session.commit()


def test_a_failure_is_left_alone_until_the_hold_has_passed(
    wired: Session, channel: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(attempts, "RETRY_HOLD", HOLD)
    fails()
    mends()

    assert tasks.retry_failed_extractions() == []  # a moment after the failure
    assert Classifier.calls == 1

    aged(wired, MEETING, HOLD + timedelta(seconds=1))
    assert tasks.retry_failed_extractions() == [MEETING]


def test_a_meeting_one_sweep_has_taken_is_passed_over_by_the_next(
    wired: Session, channel: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mminjae97 and lsh2217, review of #868: after a deploy one sweep can still
    be working through its meetings when the next starts."""
    monkeypatch.setattr(attempts, "RETRY_HOLD", HOLD)
    fails()
    aged(wired, MEETING, HOLD + timedelta(minutes=1))

    assert attempts.claim_retry(wired, MEETING) is True
    wired.commit()

    # The first sweep is running it now. The second finds it held: not due,
    # and not claimable had it read the list a moment earlier.
    assert attempts.due_for_retry(wired) == []
    assert attempts.claim_retry(wired, MEETING) is False
    assert row(wired).failures == 1  # type: ignore[union-attr]  (a hold counts nothing)


def test_two_sweeps_at_once_run_a_meeting_once(
    wired: Session, channel: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second sweep starts while the first is in the middle of the meeting,
    having read it as due a moment before the first took it."""
    monkeypatch.setattr(attempts, "RETRY_HOLD", HOLD)
    fails()
    mends()
    aged(wired, MEETING, HOLD + timedelta(minutes=1))
    monkeypatch.setattr(attempts, "due_for_retry", lambda session, **_: [MEETING])
    run = tasks.reextract_meeting
    runs: list[str] = []

    def running(meeting_id: str) -> None:
        runs.append(meeting_id)
        if len(runs) == 1:
            assert tasks.retry_failed_extractions() == []  # the other sweep
        run(meeting_id)

    monkeypatch.setattr(tasks, "reextract_meeting", running)

    assert tasks.retry_failed_extractions() == [MEETING]
    assert runs == [MEETING] and Classifier.calls == 2


def test_a_sweep_retries_five_meetings_at_most_and_the_next_takes_the_rest(
    wired: Session, channel: dict
) -> None:
    extra = [f"mtg_x{n}" for n in range(6)]
    for meeting_id in extra:
        wired.add(Meeting(id=meeting_id, team_id="team_1", title="밀린 회의"))
        wired.add(
            Participant(
                id=f"par_{meeting_id}", meeting_id=meeting_id, speaker_label="A", consented=True
            )
        )
    wired.commit()
    for meeting_id in [MEETING, *extra]:  # seven failed meetings
        wired.add(
            ExtExtractionAttempt(
                meeting_id=meeting_id,
                failures=1,
                reason="RuntimeError",
                failed_at=datetime.now(tz=UTC) - timedelta(hours=1),
            )
        )
    wired.commit()

    first = tasks.retry_failed_extractions()
    second = tasks.retry_failed_extractions()

    assert len(first) == attempts.RETRY_CAP == 5
    assert len(second) == 2 and not set(first) & set(second)


# --- the consent sweep stops with the others ------------------------------------------


def consent_changed(session: Session, meeting_id: str = MEETING) -> None:
    """What the consent sweep reads as "this meeting's consenting speech is not
    what its last extraction read"."""
    run = session.get(ExtExtractionRun, meeting_id)
    assert run is not None
    run.consent_key = "stale"
    session.commit()


def test_a_failure_in_the_consent_sweep_is_counted_and_then_left_to_the_retry_sweep(
    wired: Session, channel: dict
) -> None:
    """The user, 2026-10-06: after three failures no automatic attempt of any
    kind. Before, this sweep tried a failing meeting every ten minutes for good
    and counted nothing."""
    tasks.on_transcript_ready(event())
    consent_changed(wired)
    breaks()

    assert tasks.reextract_consent_changes() == []
    assert row(wired).failures == 1 and Classifier.calls == 2  # type: ignore[union-attr]

    # Its row still disagrees; it is the retry sweep's meeting now.
    assert tasks.reextract_consent_changes() == []
    assert Classifier.calls == 2

    tasks.retry_failed_extractions()
    tasks.retry_failed_extractions()
    assert row(wired).failures == 3 and Classifier.calls == 4  # type: ignore[union-attr]
    assert len(Slack.posts) == 1

    for _ in range(3):
        tasks.reextract_consent_changes()
        tasks.retry_failed_extractions()
    assert Classifier.calls == 4  # nothing automatic after the third


def test_a_persons_request_runs_whatever_the_count_and_the_sweeps_come_back_after_it(
    client: TestClient, wired: Session, channel: dict
) -> None:
    """The user, 2026-10-06: the cap is on the automatic attempts; "다시 추출"
    is never blocked by it."""
    tasks.on_transcript_ready(event())
    consent_changed(wired)
    breaks()
    tasks.reextract_consent_changes()
    tasks.retry_failed_extractions()
    tasks.retry_failed_extractions()
    assert row(wired).failures == 3  # type: ignore[union-attr]

    assert client.post(url()).status_code == 202
    assert tasks.run_requested_extractions() == []  # asked for, run, failed again
    assert row(wired).failures == 4  # type: ignore[union-attr]

    mends()
    aged_request = row(wired)
    assert aged_request is not None
    aged_request.requested_at = datetime.now(tz=UTC) - attempts.REQUEST_COOLDOWN * 2
    wired.commit()
    assert client.post(url()).status_code == 202
    assert tasks.run_requested_extractions() == [MEETING]
    assert row(wired).failures == 0  # type: ignore[union-attr]

    # Extracted again, so a later consent change is the consent sweep's as before.
    consent_changed(wired)
    assert tasks.reextract_consent_changes() == [MEETING]


# --- the team is told ---------------------------------------------------------------


def exhaust(meeting_id: str = MEETING) -> None:
    fails(meeting_id)
    tasks.retry_failed_extractions()
    tasks.retry_failed_extractions()


def test_after_the_third_failure_the_teams_channel_is_told_once(
    wired: Session, channel: dict
) -> None:
    fails()
    tasks.retry_failed_extractions()
    assert Slack.posts == []  # two failures: still trying

    tasks.retry_failed_extractions()

    ((where, text),) = Slack.posts
    assert where == "C_TEAM"
    assert TITLE in text and "3번" in text and "다시 추출" in text
    assert text.endswith(f"/meetings/{MEETING}/actions")
    assert SAID not in text and "RuntimeError" not in text
    assert row(wired).told_at is not None  # type: ignore[union-attr]
    tasks.retry_failed_extractions()
    assert len(Slack.posts) == 1


def test_a_team_with_no_channel_is_not_told_and_the_screen_still_says_it(
    wired: Session, channel: dict
) -> None:
    channel["channel"] = None

    exhaust()

    assert Slack.posts == []
    assert row(wired).told_at is None  # type: ignore[union-attr]
    state = attempts.state(wired, MEETING)
    assert state.failures == 3 and not state.will_retry


def test_a_slack_that_did_not_answer_is_asked_again_by_the_next_sweep(
    wired: Session, channel: dict
) -> None:
    Slack.error = [TransientIntegrationError("slack timed out")]
    exhaust()
    assert Slack.posts == [] and row(wired).told_at is None  # type: ignore[union-attr]

    Slack.error = []
    tasks.retry_failed_extractions()

    assert len(Slack.posts) == 1 and row(wired).told_at is not None  # type: ignore[union-attr]


def test_a_notice_is_not_owed_for_a_failure_older_than_a_day(wired: Session, channel: dict) -> None:
    channel["channel"] = None
    exhaust()
    kept = row(wired)
    assert kept is not None
    kept.failed_at = datetime.now(tz=UTC) - attempts.NOTICE_WINDOW - timedelta(minutes=1)
    wired.commit()
    channel["channel"] = "C_TEAM"

    tasks.retry_failed_extractions()

    assert Slack.posts == []


def test_a_notice_the_outbound_check_refuses_is_raised_by_id_and_not_sent_again(
    wired: Session, channel: dict
) -> None:
    Slack.error = [PrivacyViolationError("refusing to send unmasked personal data to slack")]
    fails()
    tasks.retry_failed_extractions()

    with pytest.raises(PrivacyViolationError, match=MEETING):
        tasks.retry_failed_extractions()

    Slack.error = []
    tasks.retry_failed_extractions()
    assert Slack.posts == []


def test_a_meeting_that_works_again_is_told_about_afresh_if_it_fails_again(
    wired: Session, channel: dict
) -> None:
    exhaust()
    mends()
    tasks.reextract_meeting(MEETING)
    assert row(wired).failures == 0 and row(wired).told_at is None  # type: ignore[union-attr]

    exhaust()

    assert len(Slack.posts) == 2


# --- "다시 추출" ---------------------------------------------------------------------


@pytest.fixture
def client(wired: Session) -> Iterator[TestClient]:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: wired
    sign_in(app, wired)
    wired.commit()
    yield TestClient(app)


def url(meeting_id: str = MEETING) -> str:
    return f"{PREFIX}/meetings/{meeting_id}/extraction"


def test_a_request_waits_for_the_worker_which_runs_it_once(client: TestClient) -> None:
    asked = client.post(url())

    assert asked.status_code == 202
    assert asked.json()["requested"] is True and asked.json()["extracted_at"] is None
    assert Classifier.calls == 0  # nothing ran in the request

    assert tasks.run_requested_extractions() == [MEETING]

    after = client.get(url()).json()
    assert after["requested"] is False and after["extracted_at"] is not None
    assert after["failures"] == 0
    assert tasks.run_requested_extractions() == []
    assert Classifier.calls == 1


def test_a_request_extracts_a_meeting_that_was_out_of_tries(
    client: TestClient, wired: Session, channel: dict
) -> None:
    exhaust()
    failed = client.get(url()).json()
    assert failed["failures"] == 3 and failed["will_retry"] is False
    mends()

    assert client.post(url()).status_code == 202
    tasks.run_requested_extractions()

    assert client.get(url()).json()["failures"] == 0
    assert wired.query(ExtActionItem).count() == 1


def test_a_requested_run_that_fails_is_counted_and_not_started_again_by_itself(
    client: TestClient, wired: Session, channel: dict
) -> None:
    exhaust()
    client.post(url())

    assert tasks.run_requested_extractions() == []
    assert tasks.run_requested_extractions() == []

    state = client.get(url()).json()
    assert state["failures"] == 4 and state["requested"] is False
    assert Classifier.calls == 4
    assert len(Slack.posts) == 1  # told at the third; the fourth says nothing new


def test_a_requested_run_keeps_an_item_list_a_person_has_edited(
    client: TestClient, wired: Session
) -> None:
    tasks.on_transcript_ready(event())
    (item,) = wired.query(ExtActionItem).all()
    item.description = "사람이 고친 설명"
    wired.add(
        ExtEditEvent(
            action_item_id=item.id, meeting_id=MEETING, kind="edited", fields="description"
        )
    )
    wired.commit()

    client.post(url())
    assert tasks.run_requested_extractions() == [MEETING]

    assert [i.description for i in wired.query(ExtActionItem)] == ["사람이 고친 설명"]


def test_a_second_request_within_the_cooldown_is_refused(
    client: TestClient, wired: Session
) -> None:
    assert client.post(url()).status_code == 202

    again = client.post(url())

    assert again.status_code == 429
    assert again.json()["error"]["code"] == "retry_too_soon"
    kept = row(wired)
    assert kept is not None
    kept.requested_at = datetime.now(tz=UTC) - attempts.REQUEST_COOLDOWN - timedelta(seconds=1)
    wired.commit()
    assert client.post(url()).status_code == 202


def test_a_meeting_with_no_transcript_has_nothing_to_extract(
    client: TestClient, wired: Session
) -> None:
    wired.add(Meeting(id="mtg_empty", team_id="team_1", title="아직 올리지 않은 회의"))
    wired.commit()

    refused = client.post(url("mtg_empty"))

    assert refused.status_code == 409
    assert row(wired, "mtg_empty") is None


@pytest.mark.parametrize("meeting_id", [ELSEWHERE, "mtg_unknown"])
def test_another_teams_meeting_reads_as_unknown(
    client: TestClient, wired: Session, meeting_id: str
) -> None:
    assert client.get(url(meeting_id)).status_code == 404
    assert client.post(url(meeting_id)).status_code == 404
    assert row(wired, meeting_id) is None


def test_the_state_says_whether_the_worker_will_try_again(client: TestClient) -> None:
    assert client.get(url()).json() == {
        "extracted_at": None,
        "failures": 0,
        "failed_at": None,
        "will_retry": False,
        "requested": False,
        "requested_at": None,
    }

    fails()

    state = client.get(url()).json()
    assert state["failures"] == 1 and state["will_retry"] is True
    assert state["failed_at"] is not None
