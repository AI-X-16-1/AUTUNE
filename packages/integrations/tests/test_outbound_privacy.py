"""The outbound boundary is where data leaves our infrastructure.

These tests exist because a leak here is not a bug — it is an incident.
See docs/architecture/privacy.md.
"""

from __future__ import annotations

import pytest

from autune_core.errors import PrivacyViolationError
from autune_integrations import find_unmasked
from autune_integrations.base import HttpClient
from autune_integrations.errors import PermanentIntegrationError
from autune_integrations.fakes import FakeJira, FakeNotion, FakeSlack
from autune_integrations.privacy import (
    MAX_OUTBOUND_CHARS,
    assert_masked,
    assert_personal_delivery,
    check_outbound,
    strings_in,
)

UNMASKED = [
    ("phone", "연락은 010-1234-5678 로 주세요"),
    ("rrn", "주민번호 900101-1234567 입니다"),
    ("card", "카드 1234-5678-9012-3456 로 결제"),
    ("email", "메일은 hong@example.com 입니다"),
]

MASKED = [
    "연락은 010-****-5678 로 주세요",
    "주민번호 900101-1****** 입니다",
    "카드 ****-****-****-3456 로 결제",
    "메일은 h***@example.com 입니다",
]


@pytest.mark.parametrize(("category", "text"), UNMASKED)
def test_unmasked_data_is_refused(category: str, text: str) -> None:
    assert category in find_unmasked(text)
    with pytest.raises(PrivacyViolationError):
        assert_masked(text, destination="slack")


@pytest.mark.parametrize("text", MASKED)
def test_masked_data_passes(text: str) -> None:
    """Masking preserves shape, so the guard must not fire on masked values."""
    assert find_unmasked(text) == []
    assert_masked(text, destination="slack")


def test_the_exception_names_categories_not_values() -> None:
    """An exception message reaches error tracking, itself a third party."""
    with pytest.raises(PrivacyViolationError) as caught:
        assert_masked("hong@example.com", destination="slack")
    assert "hong@example.com" not in str(caught.value)
    assert caught.value.details["categories"] == ["email"]


def test_a_whole_transcript_is_refused() -> None:
    """Send what the feature needs, never the whole meeting."""
    with pytest.raises(PrivacyViolationError, match="exceeds"):
        check_outbound("가" * (MAX_OUTBOUND_CHARS + 1), destination="notion")


def test_personal_data_cannot_go_to_a_channel() -> None:
    with pytest.raises(PrivacyViolationError, match="direct message"):
        assert_personal_delivery(subject_id="user_1", recipient_id="user_1", is_direct=False)


def test_personal_data_cannot_go_to_someone_else() -> None:
    """Not a teammate, not a manager, not an administrator."""
    with pytest.raises(PrivacyViolationError, match="the person it describes"):
        assert_personal_delivery(subject_id="user_1", recipient_id="user_2", is_direct=True)


def test_speaking_ratio_reaches_only_its_subject() -> None:
    slack = FakeSlack()
    slack.send_personal(subject_id="user_1", recipient_id="user_1", text="발언 비중 12%")
    assert len(slack.sent) == 1
    assert slack.sent[0].is_dm
    assert slack.channel_messages == []

    with pytest.raises(PrivacyViolationError):
        slack.send_personal(subject_id="user_1", recipient_id="user_2", text="발언 비중 12%")


def test_fakes_enforce_the_same_guards_as_real_clients() -> None:
    """A test that would have leaked must fail in tests too."""
    with pytest.raises(PrivacyViolationError):
        FakeSlack().post_message("#general", "전화번호 010-1234-5678")
    with pytest.raises(PrivacyViolationError):
        FakeJira().create_issue("AUT", "Task", "요약", "담당자 메일 hong@example.com")


# A rich message carries its content in a nested structure and leaves a bland
# summary at the top. The guard used to read only the summary.


def test_slack_blocks_are_checked_not_just_the_fallback_text() -> None:
    """Block Kit puts the message in `blocks`; `text` is the notification preview.

    A guard reading only `text` checks the least important field, and every rich
    message walks straight past it.
    """
    with pytest.raises(PrivacyViolationError):
        FakeSlack().post_message(
            "#squad",
            "액션아이템이 준비되었습니다",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "담당자 연락처 010-1234-5678"},
                }
            ],
        )


def test_the_check_reaches_the_bottom_of_a_nested_payload() -> None:
    """Block Kit nests several levels deep; one pass over the top is not enough."""
    with pytest.raises(PrivacyViolationError):
        FakeSlack().send_dm(
            "U123",
            "확인 부탁드립니다",
            blocks=[{"elements": [{"elements": [{"text": "hong@example.com"}]}]}],
        )


def test_notion_properties_are_checked_through_their_nesting() -> None:
    """A Notion property wraps its value three objects deep."""
    with pytest.raises(PrivacyViolationError):
        FakeNotion().create_page(
            "db_1", {"제목": {"title": [{"text": {"content": "010-9999-8888"}}]}}
        )


def test_a_clean_rich_message_still_goes_out() -> None:
    """The guard must not reject every structured payload it is handed."""
    slack = FakeSlack()
    slack.post_message(
        "#squad",
        "갭 리포트가 준비되었습니다",
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "HIGH 2건"}}],
    )
    assert len(slack.sent) == 1


def test_size_is_measured_over_the_whole_payload() -> None:
    """Splitting a transcript across many blocks must not evade the size limit."""
    with pytest.raises(PrivacyViolationError, match="exceeds"):
        FakeSlack().post_message(
            "#squad",
            "회의 요약",
            blocks=[{"text": "가" * 500} for _ in range(20)],
        )


# The guard runs in HttpClient.request, so a client cannot skip it by forgetting
# an argument. Addressing keys are the one declared exception.


def test_an_address_the_feature_supplies_is_not_meeting_content() -> None:
    """A calendar attendee's email is where the invitation goes, not what it says.

    Checking it would refuse every invitation, so the client declares the key.
    """
    body = {
        "summary": "검색 개인화 후속",
        "attendees": [{"email": "hong@example.com"}, {"email": "kim@example.com"}],
    }
    check_outbound(body, destination="google_calendar", addressing=frozenset({"attendees"}))


def test_declaring_nothing_checks_everything() -> None:
    """Forgetting to declare an addressing key fails closed, not open."""
    body = {"attendees": [{"email": "hong@example.com"}]}
    with pytest.raises(PrivacyViolationError):
        check_outbound(body, destination="google_calendar")


def test_an_exemption_covers_only_the_key_it_names() -> None:
    """Exempting attendees must not exempt a phone number in the summary."""
    body = {
        "summary": "연락처 010-1234-5678",
        "attendees": [{"email": "hong@example.com"}],
    }
    with pytest.raises(PrivacyViolationError):
        check_outbound(body, destination="google_calendar", addressing=frozenset({"attendees"}))


def test_strings_in_skips_the_named_key_at_any_depth() -> None:
    assert strings_in(
        {"a": {"b": "keep"}, "skip": {"c": "drop"}}, addressing=frozenset({"skip"})
    ) == ["keep"]


def test_every_client_runs_the_guard_through_the_transport() -> None:
    """The check lives in HttpClient.request, so no client method can omit it."""
    import inspect

    from autune_integrations.base import HttpClient

    assert "check_outbound" in inspect.getsource(HttpClient.request)


# The guard reads the body httpx is given. httpx can be given a body five ways,
# and guarding one of them is the same bug one level up.


class _Unsent(HttpClient):
    """A client with no transport, used to run the guard and nothing else.

    Built without ``__init__`` so there is no httpx client behind it. A body the
    guard rejects raises; a body it allows runs on and fails reaching for the
    transport that is not there, which is how "allowed" is asserted below.
    """

    service = "probe"


def _guarded(**kwargs: object) -> None:
    HttpClient.request(_Unsent.__new__(_Unsent), "POST", "/anything", **kwargs)


@pytest.mark.parametrize("channel", ["json", "data", "params"])
def test_every_inspectable_channel_is_checked(channel: str) -> None:
    """Not just json. A body sent as form data leaves by the same wire."""
    with pytest.raises(PrivacyViolationError):
        _guarded(**{channel: {"note": "제 번호는 010-1234-5678 입니다"}})


@pytest.mark.parametrize("channel", ["content", "files"])
def test_channels_that_cannot_be_read_are_refused(channel: str) -> None:
    """Raw bytes cannot be shown to be free of personal data, so they do not go."""
    with pytest.raises(PermanentIntegrationError):
        _guarded(**{channel: b"anything at all"})


def test_a_clean_body_still_passes_the_guard() -> None:
    """The guard must not be the reason every request fails."""
    with pytest.raises(AttributeError):
        # Past the guard, reaching for the transport _Unsent does not have.
        _guarded(json={"note": "마스킹된 번호는 [전화번호] 입니다"})
