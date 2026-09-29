"""The per-meeting summary report: stored once, posted once, gone with its meeting.

The Report subagent composes the body (agent-layer.md section 3.1); E stores it
and posts it through its own delivery path (section 8 rule 2). These tests cover
E's half only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core.errors import ConflictError, NotFoundError, PrivacyViolationError, ValidationError
from autune_integrations.fakes import FakeSlack
from autune_intelligence import service, tasks
from autune_intelligence.config import get_settings
from autune_intelligence.models import IntelMeetingReport

BODY = "*결제 기능 기획 회의*\n결정: 1차 출시는 카드 결제만 지원한다."


@dataclass
class BlockRecordingSlack(FakeSlack):
    """FakeSlack keeps text only; the button lives in the blocks."""

    blocks: list[list[dict] | None] = field(default_factory=list)

    def post_message(self, channel: str, text: str, blocks: list[dict] | None = None) -> str:
        self.blocks.append(blocks)
        return super().post_message(channel, text, blocks)


@pytest.fixture
def web_base_url(monkeypatch: pytest.MonkeyPatch) -> str:
    url = "https://autune.example.com"
    monkeypatch.setenv("AUTUNE_INTELLIGENCE_WEB_BASE_URL", url)
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


# --- save ---------------------------------------------------------------------


def test_save_stores_the_body_under_the_meetings_team(
    db_session: Session, team: str, meeting: str
) -> None:
    row = service.save_meeting_report(db_session, meeting, BODY)

    assert row.team_id == team
    assert row.body_markdown == BODY
    assert row.sent_at is None


def test_save_refuses_a_body_that_still_holds_personal_data(
    db_session: Session, meeting: str
) -> None:
    with pytest.raises(PrivacyViolationError):
        service.save_meeting_report(db_session, meeting, "담당자 연락처 010-1234-5678")

    assert db_session.get(IntelMeetingReport, meeting) is None


def test_save_refuses_a_body_longer_than_one_slack_section(
    db_session: Session, meeting: str
) -> None:
    with pytest.raises(ValidationError):
        service.save_meeting_report(
            db_session, meeting, "가" * (service.MEETING_REPORT_MAX_CHARS + 1)
        )


def test_save_is_not_found_for_an_unknown_meeting(db_session: Session) -> None:
    with pytest.raises(NotFoundError):
        service.save_meeting_report(db_session, "mtg_doesnotexist", BODY)


def test_save_replaces_a_body_that_has_not_been_sent(db_session: Session, meeting: str) -> None:
    service.save_meeting_report(db_session, meeting, BODY)

    row = service.save_meeting_report(db_session, meeting, "고친 본문")

    assert row.body_markdown == "고친 본문"


def test_save_refuses_to_change_a_report_people_have_already_seen(
    db_session: Session, meeting: str
) -> None:
    service.save_meeting_report(db_session, meeting, BODY)
    service.deliver_meeting_report(db_session, FakeSlack(), meeting, "C123")

    with pytest.raises(ConflictError):
        service.save_meeting_report(db_session, meeting, "고친 본문")


# --- deliver ------------------------------------------------------------------


def test_deliver_posts_once_with_a_details_button(
    db_session: Session, meeting: str, web_base_url: str
) -> None:
    service.save_meeting_report(db_session, meeting, BODY)
    slack = BlockRecordingSlack()

    assert service.deliver_meeting_report(db_session, slack, meeting, "C123") is True

    assert [(m.channel, m.text) for m in slack.sent] == [("C123", BODY)]
    buttons = [
        element
        for block in slack.blocks[0] or []
        if block["type"] == "actions"
        for element in block["elements"]
    ]
    assert [b["url"] for b in buttons] == [f"{web_base_url}/meetings/{meeting}"]
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None
    assert (row.slack_channel, row.slack_ts) == ("C123", "1.000000")
    assert row.sent_at is not None


def test_deliver_a_second_time_posts_nothing(db_session: Session, meeting: str) -> None:
    service.save_meeting_report(db_session, meeting, BODY)
    slack = FakeSlack()
    service.deliver_meeting_report(db_session, slack, meeting, "C123")

    assert service.deliver_meeting_report(db_session, slack, meeting, "C123") is False
    assert len(slack.sent) == 1


def test_deliver_without_a_web_url_posts_the_body_without_a_button(
    db_session: Session, meeting: str
) -> None:
    service.save_meeting_report(db_session, meeting, BODY)
    slack = BlockRecordingSlack()

    service.deliver_meeting_report(db_session, slack, meeting, "C123")

    assert all(block["type"] != "actions" for block in slack.blocks[0] or [])


def test_deliver_is_not_found_before_a_report_was_saved(db_session: Session, meeting: str) -> None:
    with pytest.raises(NotFoundError):
        service.deliver_meeting_report(db_session, FakeSlack(), meeting, "C123")


# --- deletion -----------------------------------------------------------------


def test_the_report_goes_when_its_meeting_is_deleted(db_session: Session, meeting: str) -> None:
    from autune_core import Meeting

    service.save_meeting_report(db_session, meeting, BODY)
    db_session.execute(sa.delete(Meeting).where(Meeting.id == meeting))
    db_session.expire_all()

    assert db_session.get(IntelMeetingReport, meeting) is None


# --- task: channel lookup, ids only in the payload ----------------------------


@pytest.fixture
def use_test_session(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    import contextlib
    from collections.abc import Iterator

    @contextlib.contextmanager
    def _scope() -> Iterator[Session]:
        yield db_session

    monkeypatch.setattr(tasks, "session_scope", _scope)


@pytest.fixture
def fake_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    from autune_core import crypto

    key = Fernet(Fernet.generate_key())
    monkeypatch.setattr(crypto, "_fernet", lambda: key)


def _connect_slack(db_session: Session, team_id: str, config: dict) -> None:
    from autune_core import TeamIntegration
    from autune_core.crypto import encrypt

    db_session.add(
        TeamIntegration(
            team_id=team_id, service="slack", secret=encrypt("xoxb-test"), config=config
        )
    )
    db_session.flush()


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_task_posts_to_the_teams_configured_channel(
    db_session: Session, team: str, meeting: str
) -> None:
    service.save_meeting_report(db_session, meeting, BODY)
    _connect_slack(db_session, team, config={"channel": "C123"})

    with patch.object(tasks, "SlackClient", return_value=FakeSlack()) as slack_client_cls:
        tasks.deliver_meeting_report(meeting)

    assert slack_client_cls.return_value.sent[0].channel == "C123"


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_task_keeps_the_report_unsent_without_a_configured_channel(
    db_session: Session, team: str, meeting: str
) -> None:
    service.save_meeting_report(db_session, meeting, BODY)
    _connect_slack(db_session, team, config={})

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        tasks.deliver_meeting_report(meeting)

    slack_client_cls.assert_not_called()
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.sent_at is None
