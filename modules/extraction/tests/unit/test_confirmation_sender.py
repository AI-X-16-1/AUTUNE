"""Step 6: the confirmation DM goes out (#70, WBS 8.3).

SQLite in memory, ``FakeSlack`` behind the team's Slack connection. What is
under test: which ambiguous agreements are asked about, that the DM goes to
the speaker alone and starts the clock once, and that a failed send leaves the
question to be asked again.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_core import Base, Meeting, Participant, PrivacyViolationError, User, Utterance
from autune_extraction import tasks
from autune_extraction.models import ExtConfirmation
from autune_integrations.errors import SlackRecipientNotLinkedError
from autune_integrations.fakes import FakeSlack

MEETING = "mtg_1"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    shared = {m.__tablename__ for m in (Meeting, User, Participant, Utterance)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id="team_1", title="주간 회의"))
        s.add(User(id="user_kim", email="kim@example.com", display_name="김민경"))
        s.add_all(
            [
                Participant(
                    id="par_kim",
                    meeting_id=MEETING,
                    speaker_label="김민경",
                    consented=True,
                    user_id="user_kim",
                ),
                Participant(id="par_anon", meeting_id=MEETING, speaker_label="S2", consented=True),
                Participant(
                    id="par_no",
                    meeting_id=MEETING,
                    speaker_label="S3",
                    consented=False,
                    user_id="user_kim",
                ),
            ]
        )
        s.flush()
        yield s


@dataclass
class _Config:
    secret: str

    def require_secret(self) -> str:
        return self.secret


@pytest.fixture
def slack(session: Session, monkeypatch: pytest.MonkeyPatch) -> FakeSlack:
    """The task with this session, a connected team and a fake Slack."""

    @contextmanager
    def scope() -> Iterator[Session]:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise

    fake = FakeSlack()
    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "load_integration", lambda s, team, service: _Config("xoxb"))
    monkeypatch.setattr(tasks, "SlackClient", lambda secret: fake)
    return fake


def agreement(
    session: Session,
    utterance_id: str,
    participant_id: str | None = "par_kim",
    *,
    text: str = "그럼 제가 한번 볼게요",
    recorded: datetime | None = None,
) -> None:
    session.add(
        Utterance(
            id=utterance_id,
            meeting_id=MEETING,
            participant_id=participant_id,
            speaker_label="김민경",
            start_sec=0.0,
            end_sec=2.0,
            text=text,
        )
    )
    row = ExtConfirmation(utterance_id=utterance_id, meeting_id=MEETING, reason="weak_assent")
    if recorded is not None:
        row.created_at = recorded
    session.add(row)
    session.commit()


def sent_at(session: Session, utterance_id: str) -> datetime | None:
    row = session.get(ExtConfirmation, utterance_id, populate_existing=True)
    assert row is not None
    return row.sent_at


def test_the_speaker_is_asked_once_by_direct_message(session: Session, slack: FakeSlack) -> None:
    agreement(session, "utt_1")

    assert tasks.ask_confirmations() == ["utt_1"]
    assert tasks.ask_confirmations() == [], "the clock is running; no second DM"

    (message,) = slack.sent
    # The quotation travels in the blocks, which the guard reads; the fallback
    # text names no words at all.
    assert message.is_dm and message.channel == "user_kim"
    assert sent_at(session, "utt_1") is not None


@pytest.mark.parametrize(
    "participant_id", ["par_anon", "par_no", None], ids=["unidentified", "no-consent", "nobody"]
)
def test_nobody_but_an_identified_consenting_speaker_is_asked(
    session: Session, slack: FakeSlack, participant_id: str | None
) -> None:
    agreement(session, "utt_1", participant_id)

    assert tasks.ask_confirmations() == []
    assert slack.sent == []


def test_a_question_older_than_its_window_is_not_put(session: Session, slack: FakeSlack) -> None:
    agreement(session, "utt_1", recorded=datetime.now(UTC) - timedelta(hours=25))

    assert tasks.ask_confirmations() == []
    assert slack.sent == []


def test_a_team_without_slack_is_skipped_and_asked_once_it_connects(
    session: Session, slack: FakeSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    agreement(session, "utt_1")
    monkeypatch.setattr(tasks, "load_integration", lambda s, team, service: None)

    assert tasks.ask_confirmations() == []
    assert sent_at(session, "utt_1") is None

    monkeypatch.setattr(tasks, "load_integration", lambda s, team, service: _Config("xoxb"))
    assert tasks.ask_confirmations() == ["utt_1"]


def test_a_failed_send_takes_the_claim_back(
    session: Session, slack: FakeSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The speaker has not linked Slack: the clock must not start on a question
    nobody received."""
    agreement(session, "utt_1")

    def not_linked(user_id: str, text: str, blocks: list[dict] | None = None) -> str:
        raise SlackRecipientNotLinkedError("not linked")

    monkeypatch.setattr(slack, "send_dm", not_linked)

    assert tasks.ask_confirmations() == []
    assert sent_at(session, "utt_1") is None


def test_a_privacy_violation_is_raised_after_the_others_are_asked(
    session: Session, slack: FakeSlack
) -> None:
    agreement(session, "utt_1", text="제 번호 010-1234-5678로 연락 주시면 볼게요")
    agreement(session, "utt_2")

    with pytest.raises(PrivacyViolationError, match="utt_1") as raised:
        tasks.ask_confirmations()

    assert "010" not in str(raised.value), "ids only"
    assert [m.channel for m in slack.sent] == ["user_kim"]
    assert sent_at(session, "utt_1") is None
    assert sent_at(session, "utt_2") is not None
