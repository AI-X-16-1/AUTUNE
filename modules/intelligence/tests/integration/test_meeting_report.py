"""The per-meeting summary report: stored once, posted once, gone with its meeting.

The Report subagent composes the body (agent-layer.md section 3.1); E stores it
and posts it through its own delivery path (section 8 rule 2). These tests cover
E's half only.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
def no_web_base_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Unset even when a developer's .env sets it for a local sign-in."""
    monkeypatch.setenv("AUTUNE_INTELLIGENCE_WEB_BASE_URL", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def web_base_url(monkeypatch: pytest.MonkeyPatch) -> str:
    url = "https://autune.example.com"
    monkeypatch.setenv("AUTUNE_INTELLIGENCE_WEB_BASE_URL", url)
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


# --- save ---------------------------------------------------------------------


def test_the_footer_says_when_the_draft_was_written(db_session: Session, meeting: str) -> None:
    """A late approval posts the draft as it was; the time keeps it from reading as current."""
    from autune_core import Meeting

    row = db_session.get(Meeting, meeting)
    assert row is not None
    drafted = datetime(2026, 10, 1, 6, 2, tzinfo=UTC)  # 15:02 in Seoul

    document = service.meeting_report_document(row, BODY, now=drafted)

    assert document.endswith("\n\n자동 생성된 리포트입니다 · 10/1 15:02 기준.")


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
    service.claim_meeting_report(db_session, meeting)

    with pytest.raises(ConflictError):
        service.save_meeting_report(db_session, meeting, "고친 본문")


# --- claim: at most once --------------------------------------------------------


def test_claim_hands_out_the_report_once(db_session: Session, meeting: str) -> None:
    service.save_meeting_report(db_session, meeting, BODY)

    first = service.claim_meeting_report(db_session, meeting)
    second = service.claim_meeting_report(db_session, meeting)

    assert first is not None and first.body_markdown == BODY
    assert first.preview == "Test Meeting 회의 리포트"
    assert second is None
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.sent_at is not None


def test_claim_refuses_a_draft_replaced_since_the_post_was_approved(
    db_session: Session, meeting: str
) -> None:
    """The approver approved the draft named by its id; a newer draft is not that one."""
    service.save_meeting_report(db_session, meeting, BODY, draft_id="rdr_seen")
    service.save_meeting_report(db_session, meeting, BODY + "\n추가", draft_id="rdr_newer")

    with pytest.raises(ConflictError):
        service.claim_meeting_report(db_session, meeting, draft_id="rdr_seen")

    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.sent_at is None  # unclaimed: the newer draft can still go


def test_claim_hands_out_the_draft_it_was_pinned_to(db_session: Session, meeting: str) -> None:
    service.save_meeting_report(db_session, meeting, BODY, draft_id="rdr_seen")

    claimed = service.claim_meeting_report(db_session, meeting, draft_id="rdr_seen")

    assert claimed is not None and claimed.body_markdown == BODY


def test_claim_is_not_found_before_a_report_was_saved(db_session: Session, meeting: str) -> None:
    with pytest.raises(NotFoundError):
        service.claim_meeting_report(db_session, meeting)


# --- post -----------------------------------------------------------------------


def _claimed(db_session: Session, meeting: str, body: str = BODY) -> service.ClaimedReport:
    service.save_meeting_report(db_session, meeting, body)
    claimed = service.claim_meeting_report(db_session, meeting)
    assert claimed is not None
    return claimed


def test_post_carries_the_body_once_and_the_title_as_the_preview(
    db_session: Session, meeting: str, web_base_url: str
) -> None:
    slack = BlockRecordingSlack()

    service.post_meeting_report(slack, "C123", _claimed(db_session, meeting))

    # Block Kit's top-level text is the notification preview, not the message.
    assert [(m.channel, m.text) for m in slack.sent] == [("C123", "Test Meeting 회의 리포트")]
    sections = [b["text"]["text"] for b in slack.blocks[0] or [] if b["type"] == "section"]
    assert sections == [BODY]
    buttons = [
        element
        for block in slack.blocks[0] or []
        if block["type"] == "actions"
        for element in block["elements"]
    ]
    assert [b["url"] for b in buttons] == [f"{web_base_url}/meetings/{meeting}"]


def test_a_title_holding_personal_data_is_left_out_of_the_preview(
    db_session: Session, team: str
) -> None:
    """Checked before the claim commits: a refused post would lose the report for good."""
    from autune_core import Meeting

    row = Meeting(team_id=team, title="kim@example.com 1:1")
    db_session.add(row)
    db_session.flush()
    slack = FakeSlack()  # runs check_outbound on every string, the title included

    claimed = _claimed(db_session, row.id)
    service.post_meeting_report(slack, "C123", claimed)

    assert claimed.preview == "회의 리포트"
    assert [m.text for m in slack.sent] == ["회의 리포트"]


def test_a_report_at_the_length_cap_passes_the_outbound_size_check(
    db_session: Session, meeting: str, web_base_url: str
) -> None:
    """save accepts MEETING_REPORT_MAX_CHARS, so post must be able to send it."""
    body = "가" * service.MEETING_REPORT_MAX_CHARS
    slack = FakeSlack()  # runs check_outbound, size included

    service.post_meeting_report(slack, "C123", _claimed(db_session, meeting, body))

    assert len(slack.sent) == 1


@pytest.mark.usefixtures("no_web_base_url")
def test_slack_control_syntax_in_a_report_is_escaped(db_session: Session, meeting: str) -> None:
    """A person may now write the body; "<!channel>" or a disguised link must not
    go out under the bot's name as markup (#642 review)."""
    slack = BlockRecordingSlack()

    service.post_meeting_report(
        slack,
        "C123",
        _claimed(db_session, meeting, body="알림 <!channel> <https://x.io|상세보기> & 끝"),
    )

    text = slack.blocks[0][0]["text"]["text"]
    assert "<!channel>" not in text and "<https://x.io|" not in text
    assert "&lt;!channel&gt;" in text and "&amp; 끝" in text


def test_post_without_a_web_url_has_no_button(db_session: Session, meeting: str) -> None:
    slack = BlockRecordingSlack()

    service.post_meeting_report(slack, "C123", _claimed(db_session, meeting))

    assert all(block["type"] != "actions" for block in slack.blocks[0] or [])


def test_a_report_with_pending_items_gets_a_review_button(
    db_session: Session, meeting: str, web_base_url: str
) -> None:
    service.save_meeting_report(db_session, meeting, BODY, pending_review=True)
    claimed = service.claim_meeting_report(db_session, meeting)
    assert claimed is not None and claimed.pending_review is True
    slack = BlockRecordingSlack()

    service.post_meeting_report(slack, "C123", claimed)

    urls = [
        element["url"]
        for block in slack.blocks[0] or []
        if block["type"] == "actions"
        for element in block["elements"]
    ]
    assert urls == [
        f"{web_base_url}/meetings/{meeting}",
        f"{web_base_url}/meetings/{meeting}/actions",
    ]


def test_a_payload_refused_before_the_claim_leaves_the_report_unclaimed(
    db_session: Session, meeting: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pattern tightened between save and post must not lose the report."""
    service.save_meeting_report(db_session, meeting, BODY)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PrivacyViolationError("refusing", categories=["phone"])

    monkeypatch.setattr(service, "check_outbound", refuse)
    with pytest.raises(PrivacyViolationError):
        service.claim_meeting_report(db_session, meeting)

    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.sent_at is None


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
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None
    assert (row.slack_channel, row.slack_ts) == ("C123", "1.000000")


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_task_tells_c_where_the_report_went_once(
    db_session: Session, team: str, meeting: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#824: C's question cards reply in the report's thread. A second run posts
    nothing and so announces nothing."""
    from autune_contracts import INTELLIGENCE_MEETING_REPORT_POSTED, MeetingReportPosted

    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(tasks, "publish", lambda event, payload: published.append((event, payload)))
    service.save_meeting_report(db_session, meeting, BODY)
    _connect_slack(db_session, team, config={"channel": "C123"})

    with patch.object(tasks, "SlackClient", return_value=FakeSlack()):
        tasks.deliver_meeting_report(meeting)
        tasks.deliver_meeting_report(meeting)

    ((event, payload),) = published
    posted = MeetingReportPosted.model_validate(payload)
    assert event == INTELLIGENCE_MEETING_REPORT_POSTED
    assert (posted.meeting_id, posted.channel, posted.thread_ts) == (meeting, "C123", "1.000000")


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_task_posts_nothing_when_the_draft_was_replaced_after_approval(
    db_session: Session, team: str, meeting: str
) -> None:
    service.save_meeting_report(db_session, meeting, BODY, draft_id="rdr_newer")
    _connect_slack(db_session, team, config={"channel": "C123"})
    slack = FakeSlack()

    with patch.object(tasks, "SlackClient", return_value=slack):
        tasks.deliver_meeting_report(meeting, "rdr_seen")

    assert slack.sent == []
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.sent_at is None


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_task_rerun_after_a_failed_post_posts_nothing(
    db_session: Session, team: str, meeting: str
) -> None:
    """Claimed before posting: a failure costs one report, never a second copy."""
    service.save_meeting_report(db_session, meeting, BODY)
    _connect_slack(db_session, team, config={"channel": "C123"})
    failing = FakeSlack()

    with (
        patch.object(tasks, "SlackClient", return_value=failing),
        patch.object(failing, "post_message", side_effect=RuntimeError("slack down")),
        pytest.raises(RuntimeError),
    ):
        tasks.deliver_meeting_report(meeting)

    retry = FakeSlack()
    with patch.object(tasks, "SlackClient", return_value=retry):
        tasks.deliver_meeting_report(meeting)

    assert retry.sent == []


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
