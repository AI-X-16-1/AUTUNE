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
    _AMBIGUOUS_ENDINGS,
    _STACK_TAILS,
    MASK_CHAR,
    MAX_TERM_TOKENS,
    PARTICLES,
    STOP_TERMS,
    Token,
    _fits,
    entity_text,
    is_bare_noun,
    is_plausible,
    masked_spans,
    noun_stem,
    noun_terms,
    person_name,
    quantity_text,
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


def test_a_particle_ends_the_run_and_stays_out_of_the_label() -> None:
    """The noun carrying the particle is the last word of its phrase: it joins
    the run as its stem, and the next noun starts a new one."""
    assert labels(("실시간", "ncpa+xsn"), ("개인화로", "ncn+jca"), ("합의", "ncpa")) == [
        "실시간 개인화",
        "합의",
    ]


def test_a_stopword_breaks_the_run_rather_than_joining_it() -> None:
    """ "이번 스프린트" is not a topic; "스프린트" might be. Without the break the
    node is called "검색 개인화 기능 이번"."""
    assert labels(("검색", "ncpa"), ("기능", "ncn"), ("이번", "ncn"), ("스프린트", "nq")) == [
        "검색 기능",
        "스프린트",
    ]


def test_a_hangul_verb_the_model_tags_foreign_is_not_a_topic() -> None:
    """``ko_core_news_lg`` gives 느려졌습니다 and 터졌습니다 as ``f``. #391."""
    assert labels(("결제", "ncpa"), ("페이지가", "ncn+jcs"), ("느려졌습니다", "f")) == [
        "결제 페이지"
    ]
    assert labels(("개인화", "ncn"), ("모델마저", "ncn+jxc"), ("느려졌습니다", "f")) == [
        "개인화 모델"
    ]
    assert labels(("캐시가", "nq+jcs"), ("터졌습니다", "f")) == ["캐시"]


@pytest.mark.parametrize(
    ("text", "tag", "stem"),
    [
        ("QA", "f", "QA"),
        ("GPU", "f", "GPU"),
        ("K8s", "f", "K8s"),
        ("API는", "f+jxt", "API"),
        ("Redis가", "f+jcs", "Redis"),
        ("스프린트에서", "f+jca", "스프린트"),
    ],
)
def test_a_term_tagged_foreign_is_still_a_noun(text: str, tag: str, stem: str) -> None:
    assert noun_stem(Token(text=text, tag=tag, start=0, end=len(text))) == stem


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

    assert [text for _, text in noun_terms(laid_out, [date])] == ["자료", "정리"]


def test_a_term_knows_where_it_was_said() -> None:
    """The caller interleaves terms with the model's entities, and the order the
    meeting said things in decides the label a topic keeps."""
    laid_out = tokens(("검색", "ncpa"), ("정렬은", "ncn+jxt"), ("인기순", "ncn"))

    assert noun_terms(laid_out) == [(laid_out[0].start, "검색 정렬"), (laid_out[2].start, "인기순")]


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
    is the expensive direction — so the list stays short and single words.
    The bound moved from 50 to 55 with 의미 (#315), the first entry measured
    as the most central node of a meeting it said nothing about."""
    assert len(STOP_TERMS) < 55
    assert all(" " not in term for term in STOP_TERMS)


# --- a noun and the particle attached to it (#278) --------------------------
#
# The tags below are the ones ``ko_core_news_lg`` 3.8.0 gives these words in
# the sentences #278 measured; ``test_spacy_ner.py`` runs the same sentences
# against the weights.


@pytest.mark.parametrize(
    ("text", "tag", "stem"),
    [
        ("리스크는", "ncn+jxt", "리스크"),
        ("인덱스가", "nq+jcs", "인덱스"),
        ("서버에서는", "ncn+jca+jxt", "서버"),
        ("기능이랑", "ncn+ncn+jca+jxc", "기능"),
        ("화요일까지", "ncn+ncn+jcj", "화요일"),
        ("개인화로", "ncn+jca", "개인화"),
        ("API는", "f+jxt", "API"),
        ("콜드스타트입니다", "ncn+ncpa+jxc", "콜드스타트"),
    ],
)
def test_a_noun_is_read_through_its_particle(text: str, tag: str, stem: str) -> None:
    """Cut on the surface, not along ``lemma_``: the lemma gives 개인화로 as
    개인 + 화로 and 콜드스타트입니다 as 콜드 + 스타트입니다."""
    assert noun_stem(Token(text=text, tag=tag, start=0, end=len(text))) == stem


@pytest.mark.parametrize(
    ("text", "tag"),
    [
        ("붙입니다", "ncn+jp+etm"),
        ("실패인데", "ncn+jp+ecs"),
        ("끝나야", "pvg+ecs"),
        ("그건", "npd+jxt"),
        ("번까지", "nbu+jxc"),
    ],
)
def test_a_copula_an_ending_or_a_non_content_head_is_not_read_through(text: str, tag: str) -> None:
    """붙입니다 is a verb the model tagged as a noun and the copula. Reading the
    copula off would give a topic called 붙, so the copula is left out
    whole — a topic lost, rather than one invented."""
    assert noun_stem(Token(text=text, tag=tag, start=0, end=len(text))) is None


def test_an_ending_nobody_listed_is_not_guessed_at() -> None:
    """The tag says a particle is there; the list says which. When the list does
    not know, the token is refused as it was before, not cut somewhere."""
    assert noun_stem(Token(text="리스크ㅋ", tag="ncn+jxt", start=0, end=4)) is None


@pytest.mark.parametrize(
    ("text", "tag", "stem"),
    [
        ("서버에서부터", "ncn+jca+jxc", "서버"),
        ("모듈까지도", "ncn+jxc", "모듈"),
        ("학교에서만", "ncn+jca+jxc", "학교"),
        ("고객에게도", "ncn+jca+jxc", "고객"),
        ("고객한테는", "ncpa+jca+jxt", "고객"),
        ("캐시에만", "ncn+jca+jxc", "캐시"),
        ("데이터에서의", "ncn+jca+jcm", "데이터"),
        ("인덱스밖에", "ncn+ncn+jca", "인덱스"),
        ("모델마저", "ncn+jxc", "모델"),
        ("일정조차", "ncpa+ncn+jxc", "일정"),
        ("우크라이나에서는", "nq+jca+jxt", "우크라이나"),
        ("경로로", "ncn+jca", "경로"),
        # Added when the cut learned to count (#345).
        ("서버에서조차", "ncn+jca+jxc", "서버"),
        ("서버로는", "ncn+jca+jxt", "서버"),
        ("결과까지도", "ncn+jxc+jxc", "결과"),
        ("캐시처럼은", "ncn+jxt", "캐시"),
        ("고객만큼은", "ncn+jxt", "고객"),
        ("모델이랑은", "ncn+jxt", "모델"),
        ("서버조차도", "ncn+jxc", "서버"),
        ("계획이라도", "ncn+jxc", "계획"),
        ("모델이야말로", "ncn+jxc", "모델"),
        ("카메라야말로", "ncn+jxc", "카메라"),
        ("프로세스로부터", "ncn+jca+jxc", "프로세스"),
    ],
)
def test_a_stacked_particle_is_cut_whole(text: str, tag: str, stem: str) -> None:
    """Cut at the longest listed particle alone, 모듈까지도 was 모듈까지 and
    서버에서부터 was 서버에서 — the rest of the particle left on the topic.
    Raised in review of #345; the tags are the model's for those sentences.

    The second half reads the tag's count: 서버로는 and 프로세스로부터 carry a
    ``j`` for each particle, so the 로 is a particle too; 모델이야말로 and
    카메라야말로 are settled by which spelling can follow the noun."""
    assert noun_stem(Token(text=text, tag=tag, start=0, end=len(text))) == stem


@pytest.mark.parametrize(
    ("text", "tag"),
    [
        ("개인화로는", "ncn+jxt"),
        ("도로도", "ncn+jxc"),
        ("서버로만", "ncn+jca"),
        ("캐시라도", "ncn+jxc"),
        ("계획대로", "ncn+ncpa+jca"),
        ("무대로", "ncn+jca"),
        ("도로부터", "ncn+jxc"),
    ],
)
def test_an_ending_a_noun_could_also_end_in_is_not_cut(text: str, tag: str) -> None:
    """경로는 and 개인화로는 carry the same tag; cutting 로는 gives 경, cutting
    는 gives 개인화로. Either is a topic nobody said, so neither is cut — 경로
    itself is kept by name, in ``_NOUNS_ENDING_LIKE_A_PARTICLE``."""
    assert noun_stem(Token(text=text, tag=tag, start=0, end=len(text))) is None


MEASURED_345: list[tuple[str, str, str | None]] = [
    # Both review rounds of #345. Every tag is what ``ko_core_news_lg`` 3.8.0
    # gave the token in a sentence; the stem is the whole particle off, or
    # nothing.
    ("인덱스밖에", "ncn+ncn+jca", "인덱스"),
    ("계획대로", "ncn+ncpa+jca", None),
    ("캐시라도", "ncn+jxc", None),
    ("서버에서부터", "ncn+jca+jxc", "서버"),
    ("모듈까지도", "ncn+jxc", "모듈"),
    ("모델이야말로", "ncn+jxc", "모델"),
    ("서버와는", "ncn+jct+jxt", "서버"),
    ("고객과의", "ncn+jct+jcm", "고객"),
    ("서버와의", "nq+jct+jcm", "서버"),
    ("고객과도", "ncn+jct+jxc", "고객"),
    ("디자인팀과도", "ncn+jxc", None),
    ("서버와도", "ncn+jxc", None),
    ("고객과는", "ncn+xsn+jxt", None),
    ("서버보다는", "ncn+jca+jxt", "서버"),
    ("일정보다는", "ncn+xsn+jca+jxt", "일정"),
    ("영업팀처럼은", "nq+ncn+jxt", "영업팀"),
    ("사용자마다의", "ncn+ncn+jcm", "사용자"),
    ("지난번만큼은", "ncn+jxt", "지난번"),
    ("디자인팀이랑은", "ncn+jxc+jxt", "디자인팀"),
    ("고객이랑도", "ncn+xsn+jxc", "고객"),
    ("캐시만은", "nq+jxt", None),
    ("배포만은", "ncn+jxt", None),
    ("기능만이", "ncn+jcs", None),
    ("결과는", "ncpa+jxt", "결과"),
    ("효과는", "ncn+jxt", "효과"),
    ("성과도", "ncn+jxc", "성과"),
    ("결과와의", "ncpa+jct+jcm", "결과"),
    ("불만은", "ncps+jxt", "불만"),
    ("불만이", "ncn+jcs", "불만"),
    ("미만은", "ncn+jxt", "미만"),
    ("경로는", "ncn+jxt", "경로"),
    ("회로는", "ncn+jxt", "회로"),
    ("인프라도", "ncn+jxc", "인프라"),
    ("사랑은", "ncpa+jxt", None),
]


@pytest.mark.parametrize(("text", "tag", "stem"), MEASURED_345)
def test_a_measured_token_loses_its_whole_particle_or_names_nothing(
    text: str, tag: str, stem: str | None
) -> None:
    """Never a stem with part of a particle left on it — 서버와, 고객과,
    서버보다, 캐시만 — and never one cut into the noun — 결, 경, 인프."""
    assert noun_stem(Token(text=text, tag=tag, start=0, end=len(text))) == stem


def test_a_case_particle_left_on_the_stem_refuses_the_token() -> None:
    """에서나마 is not listed, and a single ``j`` allows one cut: 나마 would
    leave 서버에서."""
    assert noun_stem(Token(text="서버에서나마", tag="ncn+jxc", start=0, end=6)) is None


def test_the_rows_345_measured_give_no_topic_carrying_a_particle() -> None:
    assert labels(("결제", "ncpa"), ("모듈까지도", "ncn+jxc"), ("영향이", "ncn+jcs")) == [
        "결제 모듈",
        "영향",
    ]
    assert labels(("서버에서부터", "ncn+jca+jxc"), ("로그를", "ncn+jco")) == ["서버", "로그"]
    assert labels(("정렬", "ncn"), ("로직은", "ncn+jxt"), ("계획대로", "ncn+ncpa+jca")) == [
        "정렬 로직"
    ]
    assert labels(("검색", "ncpa"), ("기능은", "ncn+jxt"), ("캐시라도", "ncn+jxc")) == ["검색 기능"]
    assert labels(("추천", "ncpa"), ("모델이야말로", "ncn+jxc")) == ["추천 모델"]


@pytest.mark.parametrize(
    ("text", "tag", "stem"),
    [
        ("결과도", "ncn+jxc", "결과"),
        ("결과로는", "ncn+jca+jxt", "결과"),
        ("효과도", "ncn+jxc", "효과"),
        ("합의는", "ncpa+jxt", "합의"),
        ("경로로", "ncn+jca", "경로"),
        ("속도도", "ncn+jxc", "속도"),
        ("불만도", "ncpa+jxc", "불만"),
    ],
)
def test_a_noun_ending_in_a_particle_syllable_keeps_it(text: str, tag: str, stem: str) -> None:
    """Cutting until the list stops matching would give 결, 합, 경 and 불 — and
    불 is contained in 불가 and 불안. The tag's ``j`` count is what stops it."""
    assert noun_stem(Token(text=text, tag=tag, start=0, end=len(text))) == stem


def test_a_particle_is_cut_in_the_spelling_that_follows_the_noun() -> None:
    """차이랑 ends in the letters of 이랑, but 이랑 only follows a consonant."""
    assert noun_stem(Token(text="차이랑", tag="ncn+jct", start=0, end=3)) == "차이"
    assert noun_stem(Token(text="모델로", tag="ncn+jca", start=0, end=3)) == "모델"
    assert noun_stem(Token(text="폴백으로", tag="nq+jca", start=0, end=4)) == "폴백"


def test_a_one_syllable_headed_stack_the_model_tagged_as_one_is_refused() -> None:
    """결과만은 is ``jxt``, the tag 불만은 has: one cut, which could be 결과만 +
    은 or 결과 + 만은. Either reading is a guess, so neither is taken."""
    assert noun_stem(Token(text="결과만은", tag="ncn+jxt", start=0, end=4)) is None
    assert noun_stem(Token(text="결과만은", tag="ncn+jxc+jxt", start=0, end=4)) == "결과"


_PLAIN_NOUNS = ("서버", "모듈", "인덱스", "고객", "로직", "캐시", "폴백", "리스크", "모델", "계획")
_SINGLE_PARTICLES = [particle for particle, count in PARTICLES.items() if count == 1]
_SECOND_PARTICLES = sorted({particle for pair in _STACK_TAILS for particle in pair})


def _joined(noun: str, *particles: str) -> str | None:
    """``noun`` with ``particles`` attached, or ``None`` if one of them is the
    spelling that cannot follow what is before it (서버이, 모델는)."""
    text = noun
    for particle in particles:
        if not _fits(particle, text[-1]):
            return None
        text += particle
    return text


def test_a_stem_never_ends_in_a_listed_particle() -> None:
    """The property the first version broke: where the tag says a particle is
    attached, what is left does not end in one. It is the noun, or it is
    nothing — and nothing only where ``_AMBIGUOUS_ENDINGS`` says the cut could
    be read two ways (서버라도 is refused because 인프라도 has to be).

    Every particle on the list, and every particle followed by one that stacks
    onto another (는, 도, 만, 의, 까지 ...), on nouns that do not end in a
    particle syllable themselves (a noun that does — 결과 — is the case above,
    where this property is false by design). One ``j`` per particle, and the
    listed stacks also as the single ``j`` the model sometimes gives them.
    """
    assert not any(noun.endswith(tuple(PARTICLES)) for noun in _PLAIN_NOUNS)
    cases: list[tuple[str, str, str]] = []
    for noun in _PLAIN_NOUNS:
        for ending, count in PARTICLES.items():
            text = _joined(noun, ending)
            if text is not None:
                cases.append((noun, text, "ncn" + "+jxc" * count))
                cases.append((noun, text, "ncn+jxc"))
        for first in _SINGLE_PARTICLES:
            for second in _SECOND_PARTICLES:
                text = _joined(noun, first, second)
                if text is not None:
                    cases.append((noun, text, "ncn+jca+jxc"))

    wrong = [
        (text, tag, stem)
        for noun, text, tag in cases
        if (stem := noun_stem(Token(text=text, tag=tag, start=0, end=len(text)))) != noun
        and not (stem is None and any(ending in text for ending in _AMBIGUOUS_ENDINGS))
    ]
    assert wrong == []
    assert not any(
        stem.endswith(tuple(PARTICLES))
        for _, text, tag in cases
        if (stem := noun_stem(Token(text=text, tag=tag, start=0, end=len(text)))) is not None
    )


def test_a_lookalike_carrying_the_copula_is_not_read_through() -> None:
    """The 재시도 shortcut is taken only once the tag has passed the same check
    every other token does."""
    assert noun_stem(Token(text="재시도", tag="ncpa+jp", start=0, end=3)) is None


@pytest.mark.parametrize(("text", "tag"), [("재시도", "ncpa+jxc"), ("난이도", "ncn+jcs")])
def test_a_noun_the_model_splits_before_its_last_syllable_is_kept_whole(
    text: str, tag: str
) -> None:
    """Read through, 재시도 is 재시 — and containment matching would then call
    a template's 재시도 keyword covered by it."""
    assert noun_stem(Token(text=text, tag=tag, start=0, end=len(text))) == text
    assert noun_stem(Token(text=f"{text}도", tag=tag, start=0, end=len(text) + 1)) == text


def test_the_regression_table_278_agreed_on() -> None:
    """The six rows written down before this was built. Rows 2-4 failed before
    it; row 6 is the one that tells a token-level stoplist from a span-level
    one, and a span-level list would let 오늘 into a label. The issue wrote
    that row as 오늘 회의; 회의 is a stopword now, so 배포 stands in for it."""
    assert labels(("오늘은", "ncn+jxt")) == []
    assert labels(("리스크는", "ncn+jxt")) == ["리스크"]
    assert labels(
        ("가장", "mag"),
        ("큰", "paa+etm"),
        ("리스크는", "ncn+jxt"),
        ("콜드스타트입니다", "ncn+ncpa+jxc"),
    ) == [
        "리스크",
        "콜드스타트",
    ]
    assert labels(("정렬", "ncn"), ("로직은", "ncn+jxt"), ("끝냅니다", "pvg+ef")) == ["정렬 로직"]
    assert labels(("오늘", "ncn"), ("배포는", "ncpa+jxt")) == ["배포"]


def test_a_bound_noun_the_model_calls_common_does_not_join_a_compound() -> None:
    """중 and 쪽 are tagged ``ncn``; with the particle read through, 서버 쪽
    would be a second topic beside 서버."""
    assert labels(("서버", "ncn"), ("쪽에서", "ncn+jca")) == ["서버"]
    assert labels(("두", "nnc"), ("가지", "nbu"), ("방법", "ncn"), ("중에", "ncn+jca")) == ["방법"]


def test_a_stopword_is_refused_by_its_stem() -> None:
    """이야기부터 and 사람에게 are discourse nouns with a particle on; the
    particle used to be what refused them, and now the stoplist has to."""
    assert labels(("실시간", "ncpa+xsn"), ("개인화", "ncn"), ("이야기부터", "ncn+jxc")) == [
        "실시간 개인화"
    ]
    assert labels(("사람에게", "ncn+jca"), ("폴백으로", "nq+jca")) == ["폴백"]


def test_an_entity_loses_the_particle_on_its_last_word_only() -> None:
    """다음 주 화요일까지 is a deadline and survives as one; its particle goes."""
    last = Token(text="화요일까지", tag="ncn+ncn+jcj", start=5, end=10)
    assert entity_text("다음 주 화요일까지", last) == "다음 주 화요일"
    assert entity_text("오늘은", Token(text="오늘은", tag="ncn+jxt", start=0, end=3)) == "오늘"
    assert entity_text("오후 3시", Token(text="3시", tag="nnc+nbu", start=3, end=5)) == "오후 3시"


def test_a_stopword_is_not_a_topic_on_the_entity_path_either() -> None:
    """#230: 오늘 was refused as a term and taken as a date. The whole span is
    compared, so a date that merely starts with 다음 is still a date."""
    assert not is_plausible("date", "오늘")
    assert is_plausible("date", "다음 주 화요일")
    assert is_plausible("date", "다음 주")


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


def test_a_masked_value_glued_to_a_word_does_not_claim_the_word() -> None:
    """Korean runs words together, and the chunk used to be bounded by nothing
    but whitespace.

    ``번호010-****-5678이에요`` came back as one span covering 번호 — a real
    noun claimed away and lost beside a masked value, which is #250 again with
    a missing space in place of a missing particle. Reproduced in review by
    @lsh2217.

    What bounds it now is what could have been *part of* the masked value: the
    masker writes digits, its separators and an address's domain, and hides a
    two-script span whole, so no Hangul survives inside one.
    """
    text = "번호010-****-5678이에요"

    assert [text[start:end] for start, end in masked_spans(text)] == ["010-****-5678"]

    tagged = (("번호", "ncn"), ("010-****-5678", "ncn"), ("확인", "ncpa"))
    laid_out = tokens(*tagged)
    assert [t for _, t in noun_terms(laid_out, masked_spans(sentence(*tagged)))] == [
        "번호",
        "확인",
    ]


def test_a_masked_name_keeps_one_syllable_and_the_run_keeps_the_rest() -> None:
    """A name, a place or an address is hidden as ``value[:1]`` plus masks —
    김민경 becomes 김** — so exactly one Hangul syllable may stand inside a
    masked value, immediately before the mask. One, and no more: ``고객김**``
    claims 김** and leaves 고객 to be a topic."""
    assert [t[a:b] for t in ["김** 님께 전달"] for a, b in masked_spans(t)] == ["김**"]
    assert [t[a:b] for t in ["고객김** 확인"] for a, b in masked_spans(t)] == ["김**"]


def test_an_address_is_claimed_with_the_domain_the_masker_left() -> None:
    """``k***@example.com`` keeps its domain by design, and the whole of it is
    the masked value — leaving the domain unclaimed would let a noun run take
    ``example.com`` as a topic."""
    text = "k***@example.com"

    assert [text[start:end] for start, end in masked_spans(text)] == [text]


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


@pytest.mark.parametrize(
    ("span", "name"),
    [
        ("이건우님이", "이건우"),  # one token, npp+jcs: what S20 showed
        ("민구님", "민구"),
        ("서연님은", "서연"),
        ("이건우님께서", "이건우"),
        ("재경 씨가", "재경"),
        ("이건우", "이건우"),
    ],
)
def test_a_person_is_the_name_without_the_honorific(span: str, name: str) -> None:
    """One colleague was as many topics as the ways the meeting addressed
    them."""
    assert person_name(span) == name


def test_an_honorific_followed_by_more_than_a_particle_is_left_alone() -> None:
    """Only a name the honorific closes is cut; 님 elsewhere in a span is not
    this rule's to judge."""
    assert person_name("님비 현상") == "님비 현상"
    assert person_name("김씨네 가게") == "김씨네 가게"


def test_a_surname_with_an_honorific_is_refused_as_one_letter() -> None:
    assert not is_plausible("person", person_name("김씨"))


def test_a_metric_without_a_number_is_not_a_metric() -> None:
    """``QT`` on spoken Korean fires on 한번, 네, 좀. What makes a quantity
    worth graphing is the quantity."""
    assert not is_plausible("metric", "한번")
    assert not is_plausible("metric", "네,")
    assert is_plausible("metric", "응답 3초")


@pytest.mark.parametrize(
    ("label", "text"), [("metric", "15%"), ("metric", "0건"), ("date", "30초"), ("date", "90일")]
)
def test_a_bare_quantity_is_not_a_topic(label: str, text: str) -> None:
    """#315: a number and a unit name no thing. The noun beside it is the topic."""
    assert not is_plausible(label, text)


@pytest.mark.parametrize("text", ["다음 주 금요일", "10월 1일", "응답 3초"])
def test_a_quantity_with_a_word_in_it_stays(text: str) -> None:
    assert is_plausible("date", text) or is_plausible("metric", text)


@pytest.mark.parametrize(
    ("span", "text"),
    [
        ("0건입니다", "0건"),
        ("90일이고", "90일"),
        ("다음 주 금요일까지", "다음 주 금요일"),
        ("다음 주에", "다음 주"),
        ("10월 1일부터", "10월 1일"),
        ("다음 주 화요일", "다음 주 화요일"),
        ("보관 기간까지", "보관 기간까지"),  # not a quantity: left whole
    ],
)
def test_a_quantity_loses_the_copula_and_particle_on_its_end(span: str, text: str) -> None:
    assert quantity_text(span) == text


@pytest.mark.parametrize("label", ["feature", "system", "date", "term"])
def test_every_other_label_is_taken_as_the_model_gave_it(label: str) -> None:
    """Two rules, from one measurement. A third would be a guess about a label
    nobody has looked at yet."""
    assert is_plausible(label, "다음 주 화요일까지")
    assert not is_plausible(label, "   ")
