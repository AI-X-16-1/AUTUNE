"""What the graph keeps from spoken Korean, decided without loading a model.

`spoken` holds the two judgements `ko_core_news_lg` cannot make for us: which
noun runs are the topics a product meeting is about, and which of the entities
it *does* find are worth a node. Both are exercised here on hand-written
tokens, with the tags the real pipeline emits — `test_spacy_ner.py` is the same
rules against the actual weights, and it needs the optional extra.
"""

from __future__ import annotations

import pytest

from autune_gap.pipeline.spoken import (
    MASK_CHAR,
    MAX_TERM_TOKENS,
    STOP_TERMS,
    Token,
    is_bare_noun,
    is_plausible,
    masked_spans,
    noun_terms,
)


def tokens(*tagged: tuple[str, str]) -> list[Token]:
    """Tokens laid out as one space-separated sentence, offsets included."""
    built: list[Token] = []
    cursor = 0
    for text, tag in tagged:
        built.append(Token(text=text, tag=tag, start=cursor, end=cursor + len(text)))
        cursor += len(text) + 1
    return built


def labels(*tagged: tuple[str, str]) -> list[str]:
    return [text for _, text in noun_terms(tokens(*tagged))]


def sentence(*tagged: tuple[str, str]) -> str:
    """The text ``tokens`` laid those tokens out over, so character offsets from
    one line up with the other."""
    return " ".join(text for text, _ in tagged)


# --- which tokens are nouns at all ------------------------------------------


@pytest.mark.parametrize("tag", ["ncn", "ncpa", "nq", "ncpa+xsn", "ncn+ncn", "f"])
def test_a_content_noun_is_a_noun_and_its_noun_forming_suffix(tag: str) -> None:
    assert is_bare_noun(tag)


@pytest.mark.parametrize("tag", ["ncn+jca", "ncn+jxt", "pvg+etm", "mag", "sp", "ncpa+xsv+ep+ecs"])
def test_a_token_carrying_grammar_is_not_a_noun(tag: str) -> None:
    """A particle or an ending makes the token a phrase, and joining it into a
    label puts "개인화로" in a report where "개인화" belongs."""
    assert not is_bare_noun(tag)


@pytest.mark.parametrize("tag", ["npd", "npp", "nnc", "nno", "nbn"])
def test_a_pronoun_a_numeral_and_a_bound_noun_are_not_content(tag: str) -> None:
    """Matching here means *joining* a run, so these have to fail it.

    그거 is two characters and clears the length floor, and it is in most
    spoken utterances — it would be a node of nearly every meeting. Raised in
    review of #222.
    """
    assert not is_bare_noun(tag)


def test_a_demonstrative_does_not_join_the_compound_beside_it() -> None:
    """ "그거 검색 기능" as one run makes a second ``topic_key`` for the topic
    the meeting calls 검색 기능 everywhere else.

    Both spellings are covered on purpose. The tag rule stops the pronoun the
    tagset describes, and the stoplist stops the one this model actually
    emits — ``ko_core_news_lg`` tags 그거 ``ncn``, so the tag rule alone lets it
    through. Measured, not assumed.
    """
    assert labels(("그거", "npd"), ("검색", "ncpa"), ("기능", "ncn")) == ["검색 기능"]
    assert labels(("그거", "ncn"), ("검색", "ncpa"), ("기능", "ncn")) == ["검색 기능"]


def test_a_quantity_is_not_a_topic() -> None:
    assert labels(("두", "nnc"), ("가지", "nbn"), ("방법", "ncn")) == ["방법"]


# --- the runs that become topics --------------------------------------------


def test_a_run_of_nouns_is_the_compound_the_speaker_said() -> None:
    """The one thing a written-Korean NER cannot give us: a product meeting's
    subject arrives as ordinary nouns, not as an entity."""
    assert labels(("검색", "ncpa"), ("개인화", "ncn"), ("기능", "ncn")) == ["검색 개인화 기능"]


def test_a_particle_breaks_the_run_and_keeps_the_label_clean() -> None:
    assert labels(("실시간", "ncpa+xsn"), ("개인화로", "ncn+jca"), ("합의", "ncpa")) == [
        "실시간",
        "합의",
    ]


def test_a_stopword_breaks_the_run_rather_than_joining_it() -> None:
    """ "이번 스프린트" is not a topic; "스프린트" might be. Without the break the
    node is called "검색 개인화 기능 이번"."""
    assert labels(("검색", "ncpa"), ("기능", "ncn"), ("이번", "ncn"), ("스프린트", "nq")) == [
        "검색 기능",
        "스프린트",
    ]


def test_a_one_character_noun_alone_is_not_a_topic() -> None:
    """Counters and bound nouns — 주, 것, 수. A run keeps them, because a run
    that survived the stoplist has a real noun in it."""
    assert labels(("주", "ncn")) == []
    assert labels(("다음", "ncn"), ("주", "ncn")) == []
    assert labels(("응답", "ncpa"), ("시간", "ncn")) == ["응답 시간"]


def test_a_long_run_yields_its_first_compound_and_nothing_after_it() -> None:
    """A list read out loud is one unbroken noun run, and the tail is dropped.

    Cutting it into further chunks would put a node in the graph whose
    boundary is the fourth token and nowhere the speaker paused — a label no
    reader recognises, which is what a false gap gets raised on. Raised in
    review of #222.
    """
    spoken = [(f"명사{index}", "ncn") for index in range(MAX_TERM_TOKENS + 2)]

    assert labels(*spoken) == [" ".join(text for text, _ in spoken[:MAX_TERM_TOKENS])]


def test_a_run_of_counters_is_not_a_topic() -> None:
    """The length floor asks the run for one real noun, rather than assuming a
    multi-token run has one. 주 and 번 are counters and neither is in the
    stoplist. Raised in review of #222."""
    assert labels(("주", "ncn"), ("번", "ncn")) == []
    assert labels(("주", "ncn"), ("단위", "ncn")) == ["주 단위"]


def test_a_span_an_entity_already_claimed_is_not_also_a_term() -> None:
    """One character belongs to at most one thing — the rule ``FakeNer``
    follows. "다음 주 화요일까지" is a date; its tail is not also a noun run."""
    laid_out = tokens(("자료는", "ncn+jxt"), ("다음", "ncn"), ("주", "ncn"), ("정리", "ncpa"))
    date = laid_out[1].start, laid_out[2].end

    assert [text for _, text in noun_terms(laid_out, [date])] == ["정리"]


def test_a_term_knows_where_it_was_said() -> None:
    """The caller interleaves terms with the model's entities, and the order the
    meeting said things in decides the label a topic keeps."""
    laid_out = tokens(("검색", "ncpa"), ("정렬은", "ncn+jxt"), ("인기순", "ncn"))

    assert noun_terms(laid_out) == [(laid_out[0].start, "검색"), (laid_out[2].start, "인기순")]


def test_nothing_is_found_in_a_sentence_with_no_nouns() -> None:
    assert labels(("그건", "npd+jxt"), ("안", "mag"), ("됩니다", "pvg+ef")) == []


def test_a_marker_step_two_keys_on_is_not_a_topic() -> None:
    """A cue word the relation rules read — 대신, 필요, 이슈 — is tagged as an
    ordinary noun by this model, so a run welds it into a label: "인기순 정렬
    대신 실시간" became one topic, and "인덱스가 필요 없습니다" produced a topic
    called 필요. A marker inside a label is a junk node and an invisible
    relation at once. Raised in review of #222 and #249.
    """
    assert labels(("인기순", "ncn"), ("정렬", "ncn"), ("대신", "ncn"), ("실시간", "ncpa+xsn")) == [
        "인기순 정렬",
        "실시간",
    ]
    assert labels(("인덱스", "ncn"), ("필요", "ncpa")) == ["인덱스"]


def test_the_stoplist_holds_only_what_speech_repeats() -> None:
    """Every entry is a topic the graph can no longer raise a gap about, which
    is the expensive direction — so the list stays short and single words."""
    assert len(STOP_TERMS) < 50
    assert all(" " not in term for term in STOP_TERMS)


# --- what module A's masking left behind ------------------------------------


def test_a_masked_value_is_the_chunk_the_speaker_said() -> None:
    """One span, not three: the digits either side of the mask are what
    survived one number, and breaking between them would leave ``5678`` free to
    join the run as a topic of its own."""
    assert masked_spans(f"010-{MASK_CHAR * 4}-5678") == [(0, 13)]
    assert masked_spans("연락처는 010-****-5678 입니다") == [(5, 18)]


def test_an_unmasked_text_has_no_masked_spans() -> None:
    """The character is the only signal. Nothing here guesses at what personal
    data looks like — that is ``autune_integrations.privacy``'s job, on the
    other side of the masking."""
    assert masked_spans("검색 개인화 기능 응답 시간 줄이죠") == []
    assert masked_spans("") == []


def test_a_masked_value_breaks_the_run_instead_of_being_carried_by_it() -> None:
    """#250, and the reason this exists.

    The run joins the mask, the run is then dropped whole by
    ``graph.build_topics``, and 고객 연락처 — a real topic, said in the clear —
    goes with it. C reports on what is *absent* from the graph, so what the
    reader sees is "논의되지 않았다" about something the meeting discussed.
    """
    tagged = (
        ("고객", "ncn"),
        ("연락처", "ncn"),
        ("010-****-5678", "ncn"),
        ("확인", "ncpa"),
        ("부탁", "ncpa"),
    )
    laid_out = tokens(*tagged)

    assert [text for _, text in noun_terms(laid_out)] == ["고객 연락처 010-****-5678 확인"]
    assert [text for _, text in noun_terms(laid_out, masked_spans(sentence(*tagged)))] == [
        "고객 연락처",
        "확인 부탁",
    ]


def test_a_masked_value_a_particle_already_separated_is_not_a_term_either() -> None:
    """The other half of #250: where a particle happens to break the run, the
    topic beside it always survived — and the masked chunk became a one-token
    run of its own, a ``term`` that ``graph.build_topics`` then had to refuse.

    Claiming the span stops it being proposed at all. The graph check stays
    where it is; this only means it is no longer the thing doing the work.
    """
    tagged = (
        ("검색", "ncn"),
        ("개인화", "ncpa"),
        ("기능", "ncn"),
        ("담당자", "ncn"),
        ("연락처는", "ncn+jxt"),
        ("010-****-5678", "ncn"),
        ("입니다", "pvg+ef"),
    )
    laid_out = tokens(*tagged)

    assert [text for _, text in noun_terms(laid_out)] == [
        "검색 개인화 기능 담당자",
        "010-****-5678",
    ]
    assert [text for _, text in noun_terms(laid_out, masked_spans(sentence(*tagged)))] == [
        "검색 개인화 기능 담당자"
    ]


def test_nothing_carrying_the_mask_is_proposed_as_a_term_at_all() -> None:
    """Whatever sits around it — a bare noun, a noun wearing a particle, or
    nothing — the masked chunk is neither a term nor part of one.

    The bug was that this depended on the neighbour. It no longer does, which
    is the whole claim; what each sentence *keeps* still differs, because a
    token carrying a particle was never a candidate.
    """
    for tagged in (
        (("연락처", "ncn"), ("010-****-5678", "ncn"), ("확인", "ncpa")),
        (("연락처는", "ncn+jxt"), ("010-****-5678", "ncn"), ("확인", "ncpa")),
        (("010-****-5678", "ncn"),),
    ):
        found = noun_terms(tokens(*tagged), masked_spans(sentence(*tagged)))

        assert all(MASK_CHAR not in text for _, text in found)


# --- which of the model's entities are worth a node -------------------------


def test_a_one_letter_person_is_not_a_person() -> None:
    """ "A/B 결과" gives ``A`` and ``B`` as ``PS``, and they became the most
    connected nodes of the typical fixture's graph."""
    assert not is_plausible("person", "A")
    assert is_plausible("person", "민경")


def test_a_metric_without_a_number_is_not_a_metric() -> None:
    """``QT`` on spoken Korean fires on 한번, 네, 좀. What makes a quantity
    worth graphing is the quantity."""
    assert not is_plausible("metric", "한번")
    assert not is_plausible("metric", "네,")
    assert is_plausible("metric", "3초")


@pytest.mark.parametrize("label", ["feature", "system", "date", "term"])
def test_every_other_label_is_taken_as_the_model_gave_it(label: str) -> None:
    """Two rules, from one measurement. A third would be a guess about a label
    nobody has looked at yet."""
    assert is_plausible(label, "다음 주 화요일까지")
    assert not is_plausible(label, "   ")
