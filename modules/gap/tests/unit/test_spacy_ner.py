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

    Before noun terms the topics here were 오늘은, 한번, A, B and
    다음 주 화요일까지 — a graph about a one-letter speaker, an adverb and
    three dates, with no node for real-time personalisation, popularity sort or
    cold start. Those three are what this adds.
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


def test_a_date_is_still_a_topic_including_a_bare_one(ner: SpacyNer) -> None:
    """Stated rather than left to be discovered: this pass does not filter dates.

    오늘은 is a node, particle and all, while 오늘 is in ``STOP_TERMS`` and
    could never arrive as a term — the same word is refused on one path and
    taken on the other. That is not fixed here: a rule that drops 오늘은 while
    keeping 다음 주 화요일까지, which is a deadline the meeting set, is not the
    one-liner this PR could carry, and precision is worth an issue rather than
    a guess. Raised in review of #222; the issue is #230.
    """
    found = topics_of(ner, "transcript_ready.typical")

    assert "오늘은" in found
    assert "다음 주 화요일까지" in found


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


def test_a_span_the_module_refuses_is_not_re_emitted_as_a_term(ner: SpacyNer) -> None:
    """``LC`` and ``OG`` are dropped by design, so their characters are spoken
    for even though no entity comes back.

    Without that, 카카오 API 연동 puts the vendor the module just refused back in
    the graph as a term, and 강남 회의실 makes a node out of a meeting room.
    Raised in review of #222.
    """
    found = ner.extract(
        [
            ("utt_1", "카카오 API 연동 얘기는 다음에 하죠"),
            ("utt_2", "강남 회의실에서 검색 개인화 기능 리뷰합니다"),
        ]
    )
    labels = [entity.text for entity in found]

    assert labels == ["연동", "검색 개인화 기능"]


def test_spacing_does_not_split_a_compound(ner: SpacyNer) -> None:
    """ASR output and pasted text both carry double spaces, and a space token is
    not a noun — it would flush the run. Raised in review of #222."""
    spaced = ner.extract([("utt_1", "검색  개인화 기능 이번 스프린트에서 진행하겠습니다")])
    single = ner.extract([("utt_1", "검색 개인화 기능 이번 스프린트에서 진행하겠습니다")])

    assert [entity.text for entity in spaced] == [entity.text for entity in single]
    assert [entity.text for entity in single] == ["검색 개인화 기능"]


def test_a_demonstrative_this_model_tags_as_a_common_noun_is_still_broken(
    ner: SpacyNer,
) -> None:
    """``ko_core_news_lg`` tags 그거 ``ncn``, not ``npd``, so the tag rule alone
    would join it to the compound beside it and split 검색 기능 in two."""
    found = ner.extract([("utt_1", "그거 검색 기능 다시 볼게요")])

    assert [entity.text for entity in found] == ["검색 기능"]


def test_a_quantity_the_model_finds_does_not_also_become_a_term(ner: SpacyNer) -> None:
    """두 가지 is a ``QT`` span with no digit in it: dropped as a metric, and
    claimed so the noun run starts after it."""
    found = ner.extract([("utt_1", "두 가지 방법 중에 고르죠")])

    assert [(entity.label, entity.text) for entity in found] == [("term", "방법")]


def test_the_version_is_the_name_and_the_version(ner: SpacyNer) -> None:
    """Recorded on every row as ``gap_topics.extractor_version``; the name alone
    could not tell a graph built with 3.7 from one built with 3.8."""
    assert ner.model_version.startswith("ko_core_news_lg-3.")
