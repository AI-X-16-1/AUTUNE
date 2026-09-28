"""``/dev/connect-notion``'s page-id parsing (#342).

Just the pure parser -- the route itself talks to the real Notion API to
create databases, which is what ``dev/page.py``'s manual walkthrough is for,
not a unit test.
"""

from __future__ import annotations

import pytest

from autune_extraction.dev.routes import _parse_page_id


def test_a_bare_dashed_id_is_kept_whole() -> None:
    """Live bug from review of #342 (lsh2217): the previous parser kept only
    the text after the id's own last dash, turning this into "1234567890ab"."""
    page_id = "8e2c9c50-4b0d-4bb0-b0b0-1234567890ab"

    assert _parse_page_id(page_id) == page_id


def test_a_bare_undashed_id_is_kept_whole() -> None:
    assert _parse_page_id("8e2c9c504b0d4bb0b0b01234567890ab") == "8e2c9c504b0d4bb0b0b01234567890ab"


def test_the_id_is_read_from_a_titled_url() -> None:
    url = "https://www.notion.so/myworkspace/Page-Title-8e2c9c50-4b0d-4bb0-b0b0-1234567890ab"

    assert _parse_page_id(url) == "8e2c9c50-4b0d-4bb0-b0b0-1234567890ab"


def test_a_query_string_is_dropped() -> None:
    url = "https://www.notion.so/myworkspace/8e2c9c50-4b0d-4bb0-b0b0-1234567890ab?pvs=4"

    assert _parse_page_id(url) == "8e2c9c50-4b0d-4bb0-b0b0-1234567890ab"


def test_surrounding_whitespace_is_stripped() -> None:
    assert (
        _parse_page_id("  8e2c9c50-4b0d-4bb0-b0b0-1234567890ab  ")
        == "8e2c9c50-4b0d-4bb0-b0b0-1234567890ab"
    )


def test_something_with_no_id_shaped_run_is_refused() -> None:
    with pytest.raises(ValueError, match="page id"):
        _parse_page_id("그냥 아무 텍스트")
