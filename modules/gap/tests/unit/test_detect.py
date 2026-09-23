"""Template comparison and risk scoring — steps 6 and 7.

Pure functions, so every number here is named rather than read out of the
environment. The thresholds fixture is the shipped defaults written down: a
change to `config` that moves a severity band should fail a test that says which
band moved, not silently re-score every meeting.
"""

from __future__ import annotations

import pytest

from autune_gap import detect
from autune_gap.config import GapSettings
from autune_gap.template import Template, TemplateItem


@pytest.fixture
def thresholds() -> detect.Thresholds:
    settings = GapSettings()
    return detect.Thresholds(
        high=settings.risk_threshold,
        medium=settings.medium_threshold,
        partial_centrality=settings.partial_centrality,
        partial_damping=settings.partial_damping,
        weight_template=settings.weight_template,
        weight_coverage=settings.weight_coverage,
        weight_participation=settings.weight_participation,
    )


def item(key: str = "success_criteria", weight: float = 0.9, **kwargs: object) -> TemplateItem:
    return TemplateItem(
        key=key,
        category=str(kwargs.get("category", "measurement")),
        item="성공 기준·측정 지표",
        weight=weight,
        keywords=tuple(kwargs.get("keywords", ("지표", "성공"))),  # type: ignore[arg-type]
        question="무엇으로 측정합니까?",
        question_about="{topic}의 성공 기준은 무엇으로 측정합니까?",
    )


SILENT: tuple[str, ...] = ()
"""A meeting where nothing was said, for the tests that are about the graph.

Written out at every call rather than defaulted in ``compare``: "no speech" is
a claim about a meeting, and an argument that fills itself in is one a caller
forgets to pass and then reads a coverage state computed from half the evidence.
"""


def one_item_template(*items: TemplateItem) -> Template:
    return Template(key="test", name="테스트", version="test.1", items=items)


def topic(
    topic_id: str = "topic_1",
    label: str = "핵심 지표",
    centrality: float = 1.0,
    silent_share: float | None = 0.0,
) -> detect.TopicView:
    return detect.TopicView(
        id=topic_id, label=label, centrality=centrality, silent_share=silent_share
    )


# --- what counts as covered -------------------------------------------------


def test_a_central_matched_topic_raises_nothing(thresholds: detect.Thresholds) -> None:
    findings = detect.compare(one_item_template(item()), [topic()], SILENT, thresholds)

    assert findings == []


def test_nothing_matching_is_missing(thresholds: detect.Thresholds) -> None:
    findings = detect.compare(
        one_item_template(item()),
        [topic(label="콜드스타트")],
        SILENT,
        thresholds,
    )

    assert [finding.coverage for finding in findings] == [detect.Coverage.MISSING]


def test_a_matched_topic_on_the_edge_of_the_graph_is_partial(
    thresholds: detect.Thresholds,
) -> None:
    """The meeting named it without settling it. A weaker claim than never
    having come up, and scored as one."""
    findings = detect.compare(
        one_item_template(item()), [topic(centrality=0.1)], SILENT, thresholds
    )

    assert [finding.coverage for finding in findings] == [detect.Coverage.PARTIAL]


def test_a_keyword_matches_inside_a_longer_label_and_the_other_way(
    thresholds: detect.Thresholds,
) -> None:
    """ "지표" inside "핵심 지표", and "에러" as a label inside the keyword
    "에러 처리". Containment both ways, over the graph's own normalisation."""
    assert detect.match(item(keywords=("지표",)), [topic(label="핵심 지표")])
    assert detect.match(item(keywords=("에러 처리",)), [topic(label="에러")])
    assert not detect.match(item(keywords=("지표",)), [topic(label="콜드스타트")])


def test_matches_come_most_central_first(thresholds: detect.Thresholds) -> None:
    """The topic the meeting leaned on is the one the finding is scored against
    and the first one a reader is shown."""
    matched = detect.match(
        item(keywords=("지표",)),
        [topic("topic_a", "보조 지표", centrality=0.2), topic("topic_b", "핵심 지표", 0.9)],
    )

    assert [found.id for found in matched] == ["topic_b", "topic_a"]


# --- what is deliberately not a rule ----------------------------------------


def test_no_rule_reads_which_job_roles_spoke(thresholds: detect.Thresholds) -> None:
    """#14's headline signal — "a topic no engineer spoke on is riskier" — is
    not implemented, and this pins that it is not half-implemented either.

    Two things are missing, not one. `participants.role` is written by no
    production code (#22), *and* a topic every consenting participant was
    silent on cannot occur: the graph builds topics from entities found in
    consenting speech, so whoever said the utterance a topic came from is
    recorded as having spoken on it. A rule that looked implemented would send
    somebody looking for the bug in the wrong half.
    """
    everybody_silent = [topic(silent_share=1.0)]

    assert detect.compare(one_item_template(item()), everybody_silent, SILENT, thresholds) == []
    assert detect.classify(everybody_silent, False, thresholds) is detect.Coverage.COVERED


# --- scoring ----------------------------------------------------------------


def test_a_missing_item_scores_exactly_its_template_weight(
    thresholds: detect.Thresholds,
) -> None:
    """There is no topic to read a centrality or a silence off, so the item's
    own weight is the only measured input and the score is it.

    Charging a missing item a full 1.0 for "no coverage" instead added the same
    constant to every one of them and pushed a whole checklist into ``high`` —
    a number that looks measured and is not.
    """
    scored = detect.score(item(weight=0.6), [], detect.Coverage.MISSING, thresholds)

    assert scored == pytest.approx(0.6)


def test_a_partial_finding_is_damped(thresholds: detect.Thresholds) -> None:
    """ "Named but thin" is a weaker claim than "never came up"."""
    matched = [topic(centrality=0.2, silent_share=0.5)]

    partial = detect.score(item(), matched, detect.Coverage.PARTIAL, thresholds)
    undamped = detect.score(item(), matched, detect.Coverage.MISSING, thresholds)

    assert partial == pytest.approx(undamped * thresholds.partial_damping)


def test_unmeasurable_participation_is_dropped_not_zeroed(
    thresholds: detect.Thresholds,
) -> None:
    """A topic nobody was considered for carries ``None``.

    Read as a zero it would say the whole room spoke on it, which is the
    opposite of what an absent measurement means; renormalising over the two
    signals that remain reports only what was measured.
    """
    unknown = detect.score(
        item(), [topic(centrality=0.2, silent_share=None)], detect.Coverage.MISSING, thresholds
    )

    expected = (thresholds.weight_template * 0.9 + thresholds.weight_coverage * 0.8) / (
        thresholds.weight_template + thresholds.weight_coverage
    )

    assert unknown == pytest.approx(expected)


def test_a_score_landing_on_the_threshold_is_high(thresholds: detect.Thresholds) -> None:
    """A missing item weighted 0.7 comes out of the weighted mean as
    0.6999999999999998. Without rounding it reads as ``medium`` — an item the
    template author put exactly at the threshold, demoted by float residue."""
    scored = detect.score(item(weight=thresholds.high), [], detect.Coverage.MISSING, thresholds)

    assert detect.severity_of(scored, thresholds) == "high"


def test_severity_bands(thresholds: detect.Thresholds) -> None:
    assert detect.severity_of(thresholds.high, thresholds) == "high"
    assert detect.severity_of(thresholds.medium, thresholds) == "medium"
    assert detect.severity_of(thresholds.medium - 0.01, thresholds) == "low"


def test_weights_that_sum_to_zero_are_refused(thresholds: detect.Thresholds) -> None:
    """A misconfigured deployment would otherwise divide by zero halfway
    through a meeting's pipeline."""
    broken = detect.Thresholds(
        high=0.7,
        medium=0.5,
        partial_centrality=0.4,
        partial_damping=0.7,
        weight_template=0,
        weight_coverage=0,
        weight_participation=0,
    )

    with pytest.raises(ValueError, match="weights sum to zero"):
        detect.score(item(), [], detect.Coverage.MISSING, broken)


# --- what a whole comparison produces ---------------------------------------


def test_an_empty_graph_raises_no_gaps(thresholds: detect.Thresholds) -> None:
    """Every item would be missing, and the report would be a whole checklist
    about a meeting the pipeline failed to read. Extraction finding nothing is
    not the meeting having discussed nothing."""
    findings = detect.compare(
        one_item_template(item(), item(key="ownership")), [], SILENT, thresholds
    )

    assert findings == []


def test_findings_come_riskiest_first_and_ties_hold_template_order(
    thresholds: detect.Thresholds,
) -> None:
    """Topic ids are random and a re-run reassigns them; without a stable tie
    the same stored meeting would publish its gaps shuffled."""
    template_with_ties = one_item_template(
        item(key="first", weight=0.6, keywords=("없는말",)),
        item(key="heaviest", weight=0.9, keywords=("없는말",)),
        item(key="second", weight=0.6, keywords=("없는말",)),
    )

    findings = detect.compare(template_with_ties, [topic(label="콜드스타트")], SILENT, thresholds)

    assert [finding.item_key for finding in findings] == ["heaviest", "first", "second"]


def test_a_finding_carries_the_topics_it_was_inferred_from(
    thresholds: detect.Thresholds,
) -> None:
    """``gap_related_topics`` is how the report shows why a gap was raised."""
    findings = detect.compare(
        one_item_template(item(keywords=("지표",))),
        [topic("topic_a", "핵심 지표", centrality=0.1)],
        SILENT,
        thresholds,
    )

    assert findings[0].topic_ids == ("topic_a",)


def test_a_finding_carries_the_item_question_and_its_display_name(
    thresholds: detect.Thresholds,
) -> None:
    """S20 shows both, and ``gap_gaps`` stores both. The title is composed from
    the coverage state, so a partial gap and a missing one do not claim the same
    thing about the meeting."""
    missing = detect.compare(
        one_item_template(item()), [topic(label="콜드스타트")], SILENT, thresholds
    )[0]
    partial = detect.compare(
        one_item_template(item()), [topic(centrality=0.1)], SILENT, thresholds
    )[0]

    assert missing.template_item == "성공 기준·측정 지표"
    assert missing.question == "무엇으로 측정합니까?"
    assert missing.title != partial.title
    assert "논의되지" in missing.title


# --- the question a gap carries (#35) ---------------------------------------


def test_a_partial_gap_names_the_topic_it_was_inferred_from(
    thresholds: detect.Thresholds,
) -> None:
    """ "검색 개인화 기능의 성공 기준은…" can be answered. The generic wording has
    to be decoded first, and a reader opening the report a week later no longer
    knows which "일" it meant."""
    matched_but_thin = [topic(label="성공 기준", centrality=0.1)]

    findings = detect.compare(one_item_template(item()), matched_but_thin, SILENT, thresholds)

    assert findings[0].coverage is detect.Coverage.PARTIAL
    assert findings[0].question.startswith("성공 기준의")


def test_a_missing_gap_keeps_the_generic_question(thresholds: detect.Thresholds) -> None:
    """There is no topic to name, and naming the meeting's most central one
    instead would be a guess — with extraction where it is, as likely to be
    "다음 주" as the thing the meeting was about. Same rule as `score`: what was
    not measured is not substituted for."""
    findings = detect.compare(
        one_item_template(item()), [topic(label="콜드스타트")], SILENT, thresholds
    )

    assert findings[0].coverage is detect.Coverage.MISSING
    assert findings[0].question == "무엇으로 측정합니까?"


def test_the_topic_named_is_the_one_the_score_was_based_on(
    thresholds: detect.Thresholds,
) -> None:
    """`match` returns most central first and `score` reads `matched[0]`. The
    question has to point at the same topic, or the number and the sentence
    describe different things."""
    findings = detect.compare(
        one_item_template(item(keywords=("지표",))),
        [topic("topic_a", "보조 지표", centrality=0.1), topic("topic_b", "핵심 지표", 0.2)],
        SILENT,
        thresholds,
    )

    assert findings[0].question.startswith("핵심 지표")
    assert findings[0].topic_ids[0] == "topic_b"


def test_question_for_is_callable_on_its_own(thresholds: detect.Thresholds) -> None:
    """It is the half of this that a template author's copy reaches, so it is
    exercised directly rather than only through a whole comparison."""
    assert detect.question_for(item(), []) == "무엇으로 측정합니까?"
    assert (
        detect.question_for(item(), [topic(label="캐시")])
        == "캐시의 성공 기준은 무엇으로 측정합니까?"
    )


# --- what the meeting said, where the graph found nothing -------------------


def test_a_keyword_nobody_said_is_still_missing(thresholds: detect.Thresholds) -> None:
    findings = detect.compare(
        one_item_template(item(keywords=("지표",))),
        [topic(label="콜드스타트")],
        ["오늘은 배포 얘기만 하겠습니다"],
        thresholds,
    )

    assert findings[0].coverage is detect.Coverage.MISSING


def test_a_keyword_said_out_loud_is_partial_rather_than_missing(
    thresholds: detect.Thresholds,
) -> None:
    """The failure this fixes. A meeting settles an owner and a deadline in
    words no extractor turned into a topic; the item came back ``missing`` and
    put a full-weight gap on the screen about something the meeting did."""
    findings = detect.compare(
        one_item_template(item(keywords=("지표",))),
        [topic(label="콜드스타트")],
        ["핵심 지표는 클릭률로 보겠습니다"],
        thresholds,
    )

    assert findings[0].coverage is detect.Coverage.PARTIAL
    assert findings[0].topic_ids == ()


def test_speech_alone_never_covers_an_item(thresholds: detect.Thresholds) -> None:
    """A word in a sentence is not a settled item. Promoting it to covered would
    let one passing "다음에 얘기해요" close a gap the meeting never closed, and a
    covered item raises nothing at all."""
    findings = detect.compare(
        one_item_template(item(keywords=("지표",))),
        [topic(label="콜드스타트")],
        ["지표 얘기는 다음에 하죠"],
        thresholds,
    )

    assert findings != []
    assert findings[0].coverage is detect.Coverage.PARTIAL


def test_a_spoken_item_scores_below_the_same_item_missing(
    thresholds: detect.Thresholds,
) -> None:
    """Precision, in a number. The same item is a weaker claim when the meeting
    did raise it, so it is damped and lands in a lower band."""
    said = detect.compare(
        one_item_template(item(keywords=("지표",))),
        [topic(label="콜드스타트")],
        ["핵심 지표는 클릭률로 보겠습니다"],
        thresholds,
    )[0]
    silent = detect.compare(
        one_item_template(item(keywords=("지표",))),
        [topic(label="콜드스타트")],
        SILENT,
        thresholds,
    )[0]

    assert said.risk_score < silent.risk_score
    assert silent.severity == "high"
    assert said.severity != "high"


def test_a_topic_match_outranks_what_was_merely_said(thresholds: detect.Thresholds) -> None:
    """Both sources agree the item came up; the graph is the one with a
    centrality to read, so it decides."""
    findings = detect.compare(
        one_item_template(item(keywords=("지표",))),
        [topic(label="핵심 지표", centrality=1.0)],
        ["핵심 지표는 클릭률로 보겠습니다"],
        thresholds,
    )

    assert findings == []


def test_speech_is_matched_case_insensitively(thresholds: detect.Thresholds) -> None:
    """``KPI`` in a template file and ``kpi`` in a transcript are one word;
    keywords are casefolded at load and the speech is casefolded here."""
    findings = detect.compare(
        one_item_template(item(keywords=("kpi",))),
        [topic(label="콜드스타트")],
        ["이번 분기 KPI는 그대로 갑니다"],
        thresholds,
    )

    assert findings[0].coverage is detect.Coverage.PARTIAL


def test_an_empty_graph_still_raises_nothing_however_much_was_said(
    thresholds: detect.Thresholds,
) -> None:
    """An empty graph says extraction found nothing. Keyword hits over a
    transcript nothing was extracted from are a worse guess, not a better one."""
    assert (
        detect.compare(
            one_item_template(item(keywords=("지표",))),
            [],
            ["핵심 지표는 클릭률로 보겠습니다"],
            thresholds,
        )
        == []
    )


def test_mentioned_reads_the_items_own_keywords() -> None:
    assert detect.mentioned(item(keywords=("지표",)), ["핵심 지표는 클릭률"])
    assert not detect.mentioned(item(keywords=("지표",)), ["핵심 목표는 클릭률"])
