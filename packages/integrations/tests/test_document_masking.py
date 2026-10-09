"""A document's text is masked before anything stores it (#817).

The detector is ``find_pii``; these tests are about what the masker does with
the spans it returns, and that its result passes the guard every store already
has. Every value below is made up.
"""

from __future__ import annotations

import pytest

from autune_core.errors import PrivacyViolationError
from autune_integrations import document_masking
from autune_integrations.document_masking import holds_mask, mask_document, screen_output
from autune_integrations.privacy import MASK_CHAR, assert_masked, find_unmasked

PHONE = "010-2345-6789"
EMAIL = "minsu.kim@example.com"
RRN = "900101-1234567"
CARD = "4111 1111 1111 1111"
ACCOUNT = "123456-01-234567"
SECRETS = (PHONE, EMAIL, RRN, CARD, ACCOUNT)

DOCUMENT = f"""3분기 플랫폼 로드맵

결제 모듈 이관은 9월 둘째 주까지 마칩니다. 이관 담당 연락처는 {PHONE} 이고 메일은 {EMAIL} 입니다.
검색 서비스는 색인 재구축을 끝낸 뒤 10월에 공개합니다. 예산은 4,500만 원입니다.
정산 계좌는 {ACCOUNT} 입니다.
주민번호 {RRN}, 카드 {CARD}
"""


def test_every_kind_of_personal_data_is_hidden_and_the_guard_agrees() -> None:
    masked = mask_document(DOCUMENT)

    assert not any(secret in masked.text for secret in SECRETS)
    assert find_unmasked(masked.text) == []
    assert_masked(masked.text, destination="a table")
    assert sum(masked.counts.values()) >= 5
    assert holds_mask(masked.text)


def test_what_is_not_personal_data_stays_readable() -> None:
    text = "예산은 4,500만 원, 2026-10-08 회의, 버전 1.2.3, 총 1,234,567원, 3분기 목표 120%"

    assert mask_document(text).text == text
    assert mask_document(text).counts == {}
    assert not holds_mask(mask_document(text).text)


def test_the_layout_of_a_hidden_value_stays_and_no_character_of_it_does() -> None:
    masked = mask_document(f"연락처 {PHONE}, 메일 {EMAIL}").text

    assert "***-****-****" in masked
    assert "@" in masked and "example" not in masked and "minsu" not in masked
    assert len(masked) == len(f"연락처 {PHONE}, 메일 {EMAIL}")


def test_the_text_around_a_hidden_value_is_untouched() -> None:
    masked = mask_document(f"이관 담당 연락처는 {PHONE} 이고 예산은 4,500만 원입니다.").text

    assert masked.startswith("이관 담당 연락처는 ")
    assert masked.endswith(" 이고 예산은 4,500만 원입니다.")


def test_masking_twice_changes_nothing() -> None:
    once = mask_document(DOCUMENT).text

    assert screen_output(once) == once
    assert mask_document(once).text == once


def test_values_run_together_are_hidden_across_both() -> None:
    """A phone number run into a card number: the union goes, not one of the two."""
    masked = mask_document("사무실 02 1234 5678 9012 3456 이요").text

    assert not any(char.isdigit() for char in masked)


def test_overlapping_spans_become_one_under_the_first() -> None:
    assert document_masking._merged([(4, 9, "card"), (0, 6, "phone"), (12, 15, "email")]) == [
        (0, 9, "phone"),
        (12, 15, "email"),
    ]


def test_a_span_inside_another_does_not_shorten_it() -> None:
    assert document_masking._merged([(0, 10, "card"), (2, 5, "phone")]) == [(0, 10, "card")]


def test_the_counts_say_how_many_of_each_kind_and_nothing_of_the_values() -> None:
    masked = mask_document(f"{PHONE} 그리고 {PHONE}, 메일 {EMAIL}")

    assert sum(masked.counts.values()) == 3
    assert all(isinstance(count, int) for count in masked.counts.values())
    assert not any(secret in str(masked.counts) for secret in SECRETS)


def test_text_the_masker_could_not_clear_is_refused_and_the_error_holds_no_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A detector that finds a span on the second look which the first missed:
    the masker raises rather than hand back text it knows is not clean."""
    monkeypatch.setattr(document_masking, "find_pii", lambda _text: [])

    with pytest.raises(PrivacyViolationError) as raised:
        mask_document(f"연락처 {PHONE}")

    assert PHONE not in str(raised.value)
    assert PHONE not in repr(raised.value.__dict__)


def test_an_excerpt_is_screened_on_the_way_out() -> None:
    """What was never masked -- a typed title -- is hidden when it is shown."""
    assert PHONE not in screen_output(f"담당 {PHONE} 문서")
    assert screen_output("3분기 로드맵") == "3분기 로드맵"


def test_a_mask_in_the_text_is_what_tells_the_reader_something_was_hidden() -> None:
    assert holds_mask(f"연락처 {MASK_CHAR * 3}")
    assert not holds_mask("연락처 없음")
