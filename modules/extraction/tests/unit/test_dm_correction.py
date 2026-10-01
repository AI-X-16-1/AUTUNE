"""A corrected line reaches the confirmation DM that quoted it (#586, part 2).

A PII report masks a stored line again; the DM sent before still quotes the old
text. B keeps where each DM landed and a digest of what it quoted, notices on
the next run that the line changed, and replaces the DM in place through the
team's bot. Under test: the place is kept on send; a changed line is found, an
unchanged one is not; the task rebuilds and updates the DM and records the new
digest; it does nothing without a kept place or a Slack connection; a Slack
failure is logged; a privacy refusal is raised.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_core import Base, Meeting, Participant, PrivacyViolationError, User, Utterance
from autune_extraction import service, tasks
from autune_extraction.models import ExtConfirmation
from autune_integrations import PermanentIntegrationError
from autune_integrations.fakes import FakeSlack

MEETING = "mtg_1"
OLD = "그럼 제가 010-1234-5678로 한번 볼게요"
NEW = "그럼 제가 [전화번호]로 한번 볼게요"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Meeting, User, Participant, Utterance)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id="team_1", title="주간 회의"))
        s.add(User(id="user_kim", email="kim@example.com", display_name="김"))
        s.add(
            Participant(
                id="par_kim",
                meeting_id=MEETING,
                user_id="user_kim",
                speaker_label="김",
                consented=True,
            )
        )
        s.flush()
        s.add(
            Utterance(
                id="utt_1",
                meeting_id=MEETING,
                participant_id="par_kim",
                speaker_label="김",
                start_sec=0.0,
                end_sec=1.0,
                text=NEW,
            )
        )
        s.add(ExtConfirmation(utterance_id="utt_1", meeting_id=MEETING, reason="weak_assent"))
        s.commit()
        yield s


@dataclass
class _Config:
    secret: str

    def require_secret(self) -> str:
        return self.secret


@pytest.fixture
def slack(session: Session, monkeypatch: pytest.MonkeyPatch) -> FakeSlack:
    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    fake = FakeSlack()
    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "load_integration", lambda s, team, svc: _Config("xoxb"))
    monkeypatch.setattr(tasks, "SlackClient", lambda secret: fake)
    return fake


def sent_as(session: Session, text: str) -> ExtConfirmation:
    """The DM as it went out quoting ``text``, with its place kept."""
    row = session.get(ExtConfirmation, "utt_1")
    assert row is not None
    row.sent_at = datetime.now(UTC)
    row.dm_channel, row.dm_ts = "D-user_kim", "1.000000"
    row.dm_digest = service.source_digest([text])
    session.commit()
    return row


def test_sending_keeps_where_the_dm_landed_and_what_it_quoted(session: Session) -> None:
    row = service.ask_for_confirmation(
        session,
        FakeSlack(),
        meeting_id=MEETING,
        speaker_id="user_kim",
        recipient_id="user_kim",
        utterance_id="utt_1",
        quoted_text=NEW,
        answer_url="https://autune.example/meetings/mtg_1/actions",
    )

    assert row is not None
    assert (row.dm_channel, row.dm_ts) == ("D-user_kim", "1.000000")
    assert row.dm_digest == service.source_digest([NEW])


def test_a_dm_quoting_a_line_since_corrected_is_found(session: Session) -> None:
    sent_as(session, OLD)

    assert service.dms_to_correct(session, meeting_id=MEETING, spoken={"utt_1": NEW}) == ["utt_1"]
    assert service.dms_to_correct(session, meeting_id=MEETING, spoken={"utt_1": OLD}) == []


def test_a_dm_sent_before_places_were_kept_is_not_found(session: Session) -> None:
    row = sent_as(session, OLD)
    row.dm_ts = None
    session.commit()

    assert service.dms_to_correct(session, meeting_id=MEETING, spoken={"utt_1": NEW}) == []


def test_the_task_replaces_the_dm_with_the_line_as_stored_now(
    session: Session, slack: FakeSlack
) -> None:
    sent_as(session, OLD)

    assert tasks.update_confirmation_dm("utt_1") is True

    ((channel, ts, _text, blocks),) = slack.updates
    assert (channel, ts) == ("D-user_kim", "1.000000")
    assert NEW in str(blocks) and "010-1234-5678" not in str(blocks)
    row = session.get(ExtConfirmation, "utt_1", populate_existing=True)
    assert row is not None and row.dm_digest == service.source_digest([NEW])


def test_nothing_happens_without_a_kept_place_or_a_slack_connection(
    session: Session, slack: FakeSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = sent_as(session, OLD)
    row.dm_ts = None
    session.commit()
    assert tasks.update_confirmation_dm("utt_1") is False

    sent_as(session, OLD)
    monkeypatch.setattr(tasks, "load_integration", lambda s, team, svc: None)
    assert tasks.update_confirmation_dm("utt_1") is False
    assert slack.updates == []


def test_a_slack_failure_is_logged_and_left_for_the_next_run(
    session: Session, slack: FakeSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent_as(session, OLD)

    def refuse(*_: object, **__: object) -> None:
        raise PermanentIntegrationError("message_not_found")

    monkeypatch.setattr(slack, "update_message", refuse)

    assert tasks.update_confirmation_dm("utt_1") is False
    row = session.get(ExtConfirmation, "utt_1", populate_existing=True)
    assert row is not None and row.dm_digest == service.source_digest([OLD]), "found again"


def test_a_line_still_unmasked_is_refused_not_sent(session: Session, slack: FakeSlack) -> None:
    sent_as(session, NEW)
    said = session.get(Utterance, "utt_1")
    assert said is not None
    said.text = OLD  # should be impossible: stored text is masked
    session.commit()

    with pytest.raises(PrivacyViolationError):
        tasks.update_confirmation_dm("utt_1")
    assert slack.updates == []
