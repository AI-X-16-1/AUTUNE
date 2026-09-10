"""What the read model says, and the line it does not draw yet.

No database. ``read_model`` is a pure function of one row, its source links, and
one setting, so all three are readable without Postgres — the same split as
``test_action_item_editing``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autune_extraction import service
from autune_extraction.config import ExtractionSettings, get_settings
from autune_extraction.models import ExtActionItem, ExtActionItemSource
from autune_extraction.schemas import ActionItemRead

MEETING = "mtg_1"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """``get_settings`` is ``lru_cache``d, so a test that sets an environment
    variable would otherwise be answered from another test's reading."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def item(*, confidence: float = 0.9, sources: tuple[str, ...] = ()) -> ExtActionItem:
    row = ExtActionItem(
        id="act_1",
        meeting_id=MEETING,
        description="배포 스크립트 정리",
        status="needs_confirmation",
        confidence=confidence,
        origin="model",
    )
    row.sources = [ExtActionItemSource(utterance_id=uid) for uid in sources]
    return row


# --- the field the card reads -----------------------------------------------


def test_the_read_model_carries_the_source_utterance_ids() -> None:
    """S17's card decides between "근거 발화 N건" and "직접 추가" from this list.

    It was missing from the response, and an absent list is an empty one, so
    every model-extracted item was labelled as one somebody typed — the
    distinction ADR 0006 and the edit-cost metric rest on, printed inverted with
    nothing failing. This is the test that would have caught it.
    """
    read = service.read_model(item(sources=("utt_1", "utt_2")))

    assert read.source_utterance_ids == ["utt_1", "utt_2"]


def test_a_hand_added_item_reports_no_sources_rather_than_omitting_the_field() -> None:
    """ "직접 추가" has to be a fact the response states, not one it fails to."""
    read = service.read_model(item(sources=()))

    assert read.source_utterance_ids == []


def test_the_response_carries_every_field_the_board_reads() -> None:
    """A field dropped from this schema does not fail anywhere — the client
    reads ``undefined`` and renders the falsy branch. Assert the whole set so a
    removal breaks here instead of on a screen."""
    assert set(ActionItemRead.model_fields) == {
        "id",
        "meeting_id",
        "description",
        "assignee_id",
        "assignee_label",
        "due_date",
        "status",
        "confidence",
        "origin",
        "source_utterance_ids",
        "is_candidate",
    }


# --- the threshold nobody has measured yet ----------------------------------


def test_nothing_is_a_candidate_while_the_threshold_is_unset() -> None:
    """The default, and the honest answer until #10 measures a number.

    A confidence of zero is still not a candidate, because "candidate" means
    "below the line" and there is no line.
    """
    assert get_settings().candidate_confidence is None

    assert service.read_model(item(confidence=0.0)).is_candidate is False


def test_the_threshold_is_read_from_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTUNE_EXTRACTION_CANDIDATE_CONFIDENCE", "0.5")
    get_settings.cache_clear()

    assert service.read_model(item(confidence=0.49)).is_candidate is True
    assert service.read_model(item(confidence=0.51)).is_candidate is False


def test_an_item_exactly_at_the_threshold_is_not_a_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The comparison is strict. "Below this confidence" is what the setting
    says, and a boundary that drifts is a boundary two people read differently.
    """
    monkeypatch.setenv("AUTUNE_EXTRACTION_CANDIDATE_CONFIDENCE", "0.5")
    get_settings.cache_clear()

    assert service.read_model(item(confidence=0.5)).is_candidate is False


def test_a_hand_added_item_cannot_land_in_the_candidate_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``create_action_item`` stores 1.0 for a typed item — a person entering it
    is the certainty — so no threshold in range can pull it into the band."""
    monkeypatch.setenv("AUTUNE_EXTRACTION_CANDIDATE_CONFIDENCE", "1.0")
    get_settings.cache_clear()

    assert service.read_model(item(confidence=1.0)).is_candidate is False


def test_the_setting_refuses_a_threshold_outside_the_confidence_range() -> None:
    """Confidence is a unit interval. A threshold of 5 would mark everything a
    candidate and read as a deliberate choice in whoever's .env it came from."""
    with pytest.raises(ValueError):
        ExtractionSettings(candidate_confidence=5.0)


# --- a blank in .env is a blank, not a crash ---------------------------------


def test_an_empty_environment_variable_reads_as_no_threshold() -> None:
    """What ``cp .env.example .env`` actually produces.

    ``candidate_confidence`` is the first non-``str`` setting in this repo whose
    example value is blank, and pydantic does not coerce "" to None on its own:
    without the validator this raises ``float_parsing`` on every
    ``get_settings()`` call, which ``read_model`` makes for every item read.
    """
    assert ExtractionSettings(_env_file=None, candidate_confidence="").candidate_confidence is None
    assert (
        ExtractionSettings(_env_file=None, candidate_confidence="   ").candidate_confidence is None
    )


def test_every_blank_extraction_variable_in_env_example_loads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole file, not just the one field this PR added.

    The bug was not that this setting is special; it is that ``.env.example``
    documents "unset" by writing the name with no value, and that only survives
    while every such field is a ``str``. The next non-``str`` one fails the same
    way, in the same place, for whoever copied the file. Asserting the file
    itself means the test finds that field instead of a teammate's first run.
    """
    example = Path(__file__).resolve().parents[4] / ".env.example"
    lines = example.read_text(encoding="utf-8").splitlines()

    declared = [
        line.split("=", 1)
        for line in lines
        if line.startswith("AUTUNE_EXTRACTION_") and "=" in line
    ]
    assert declared, "no AUTUNE_EXTRACTION_ variables found — did the prefix change?"

    for name, value in declared:
        monkeypatch.setenv(name, value)

    ExtractionSettings(_env_file=None)  # raises if any blank fails to parse
