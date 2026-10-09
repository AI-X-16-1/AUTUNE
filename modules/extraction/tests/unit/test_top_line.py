"""The top line of a row in a copy that leaves (module B's owner, 2026-10-09).

The writers are tested where they live (``test_jira_sync``, ``test_notion_sync``,
``test_calendar_sync``, the message files, ``test_project_send``); here is the
rule they share.
"""

from __future__ import annotations

from typing import get_args

import pytest

from autune_extraction import jira_sync, top_line

SENTENCE = "다음 주 수요일까지 결제 화면 오류 목록을 정리해서 디자인 팀에 공유하기"
TITLE = "결제 화면 오류 목록 공유"


def test_the_short_title_leads_where_the_row_has_one() -> None:
    assert top_line.top_line(TITLE, SENTENCE) == TITLE
    assert top_line.top_line(None, SENTENCE) == SENTENCE
    assert top_line.top_line("  ", SENTENCE) == SENTENCE, "an empty title is no title"
    assert top_line.top_line(f" {TITLE} ", SENTENCE) == TITLE


def test_a_title_stands_for_the_sentence_only_when_it_is_not_the_sentence() -> None:
    assert top_line.titled(TITLE, SENTENCE) is True
    assert top_line.titled(None, SENTENCE) is False
    assert top_line.titled("", SENTENCE) is False
    assert top_line.titled(SENTENCE, SENTENCE) is False


def test_the_copies_with_a_title_of_their_own_say_the_kind_and_a_line_does_not() -> None:
    """ "밖으로 나가는 제목 전부" for the mark; "붙이지 않기" for a line of a
    message or of minutes, which stands under a heading that says the kind."""
    assert {copy for copy, marked in top_line.MARKED.items() if marked} == {
        "jira",
        "notion",
        "calendar",
    }
    assert set(top_line.MARKED) == set(get_args(top_line.Copy)), "one switch a copy"


@pytest.mark.parametrize("copy", ["jira", "notion", "calendar"])
def test_the_mark_goes_before_the_title_and_outside_it(copy: top_line.Copy) -> None:
    assert top_line.outbound_line(copy, "item", TITLE, SENTENCE) == f"[할 일] {TITLE}"
    assert top_line.outbound_line(copy, "decision", TITLE, SENTENCE) == f"[결정] {TITLE}"
    # A row without a title goes out as it did, behind the mark.
    assert top_line.outbound_line(copy, "item", None, SENTENCE) == f"[할 일] {SENTENCE}"


@pytest.mark.parametrize("copy", ["reminder", "digest", "notice", "report", "minutes"])
def test_a_line_is_the_title_or_the_sentence_and_nothing_else(copy: top_line.Copy) -> None:
    assert top_line.outbound_line(copy, "item", TITLE, SENTENCE) == TITLE
    assert top_line.outbound_line(copy, "decision", None, SENTENCE) == SENTENCE


def test_a_reworded_decision_has_no_title() -> None:
    assert top_line.standing_title(TITLE, reworded=False) == TITLE
    assert top_line.standing_title(TITLE, reworded=True) is None
    assert top_line.standing_title(None, reworded=False) is None


def test_a_decisions_issue_keeps_the_mark_it_has_carried() -> None:
    assert jira_sync.DECISION_PREFIX == top_line.DECISION_MARK == "[결정] "
