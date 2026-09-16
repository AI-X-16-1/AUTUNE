"""The real Korean pipeline over the shared fixtures.

Marked ``model``: it loads ``ko_core_news_lg`` from the ``local-models`` extra
and the default run excludes it (``-m 'not model'``), so CI stays green on a
checkout without a 220MB wheel. Run it with

    uv run --package autune-gap --extra local-models pytest -m model modules/gap

This is the pin under the measurement in ``pipeline/spoken.py`` and
``docs/modules/gap.md``. What the graph contains for a given fixture is the
whole product of module C's step 1, and a model upgrade that quietly empties it
should fail here rather than show up as a gap report about ``한번``.
"""

from __future__ import annotations

import pytest

from autune_contracts import TranscriptReady
from autune_contracts.fixtures import load
from autune_gap import graph
from autune_gap.pipeline.ner import SpacyNer

pytestmark = pytest.mark.model


@pytest.fixture(scope="module")
def ner() -> SpacyNer:
    return SpacyNer("ko_core_news_lg")


def topics_of(ner: SpacyNer, fixture: str) -> list[str]:
    """The labels the graph would hold for a fixture, in the order it met them."""
    transcript = TranscriptReady.model_validate(load(fixture))
    pairs = [(utterance.id, utterance.text) for utterance in transcript.utterances]
    entities = ner.extract(pairs)
    return [
        topic.label
        for topic in graph.build_topics(entities, [utterance_id for utterance_id, _ in pairs])
    ]


def test_the_typical_meeting_is_about_what_it_discussed(ner: SpacyNer) -> None:
    """#13's completion criterion, and the reason ``spoken`` exists.

    Before noun terms the five topics here were 실시간 → *nothing*, plus
    오늘은, 한번, A, B and 다음 주 화요일까지: a graph about a one-letter
    speaker and an adverb. The meeting is about real-time personalisation,
    popularity sort and cold start.
    """
    found = topics_of(ner, "transcript_ready.typical")

    assert "인기순 정렬" in found
    assert "콜드스타트" in found
    assert "실시간" in found


def test_the_noise_the_model_finds_does_not_become_a_topic(ner: SpacyNer) -> None:
    """``A``/``B`` from "A/B 결과" and ``한번`` as a quantity — see
    ``spoken.is_plausible``."""
    found = topics_of(ner, "transcript_ready.typical")

    assert "A" not in found
    assert "B" not in found
    assert "한번" not in found


def test_a_short_meeting_has_topics_at_all(ner: SpacyNer) -> None:
    """This fixture's only entity was ``네,``. Every noun in it — the feature
    being built and the metric nobody set — was invisible to the NER."""
    found = topics_of(ner, "transcript_ready.short")

    assert "검색 개인화 기능" in found
    assert "응답 시간" in found


def test_a_masked_span_is_never_a_topic(ner: SpacyNer) -> None:
    """``010-****-5678`` is read as a date by the model and keeps its last four
    digits by design. As a node it would carry them into a report the whole
    team reads (privacy.md section 2)."""
    found = topics_of(ner, "transcript_ready.typical")

    assert not any("*" in label for label in found)


def test_the_version_is_the_name_and_the_version(ner: SpacyNer) -> None:
    """Recorded on every row as ``gap_topics.extractor_version``; the name alone
    could not tell a graph built with 3.7 from one built with 3.8."""
    assert ner.model_version.startswith("ko_core_news_lg-3.")
