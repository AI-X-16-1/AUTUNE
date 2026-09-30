"""The noun-ended form of what was said.

Every case is a sentence in the register the team meetings are written in. The
rules are checked against what a reader would write in the minutes; where no rule
applies the sentence comes back as it was said, and that is a case too.
"""

from __future__ import annotations

import pytest

from autune_extraction.noun_form import tidy


@pytest.mark.parametrize(
    ("said", "tidied"),
    [
        # A person will do something.
        ("그럼 제가 다음 주 화요일까지 볼게요", "다음 주 화요일까지 볼 예정"),
        ("제가 금요일까지 자료 정리하겠습니다", "금요일까지 자료 정리 예정"),
        ("자료 정리해서 공유하겠습니다", "자료 정리해서 공유 예정"),
        ("보고서는 제가 금요일까지 작성하겠습니다", "보고서는 금요일까지 작성 예정"),
        ("확인해 보겠습니다", "확인 예정"),
        ("검토해 볼게요", "검토 예정"),
        ("제가 서버에 배포할게요", "서버에 배포 예정"),
        ("내일까지 만들게요", "내일까지 만들 예정"),
        ("파일을 보낼게요", "파일을 보낼 예정"),
        ("그건 제가 가져오겠습니다", "그건 가져올 예정"),
        ("금요일에 공유할 거예요", "금요일에 공유 예정"),
        ("다음 주에 먹을 거예요", "다음 주에 먹을 예정"),
        # Something was settled.
        ("이번 주 안으로 배포하기로 했습니다", "이번 주 안으로 배포하기로 함"),
        ("다음 회의부터 격주로 하기로 하죠", "다음 회의부터 격주로 하기로 함"),
        ("A안으로 진행합시다", "A안으로 진행함"),
        ("일정이 확정됐습니다", "일정이 확정됨"),
        ("그 방향으로 가시죠", "그 방향으로 진행"),
        ("B안으로 하겠습니다", "B안으로 결정"),
    ],
)
def test_a_known_ending_becomes_a_noun_ending(said: str, tidied: str) -> None:
    assert tidy(said) == tidied


def test_the_opening_filler_and_the_speaker_go() -> None:
    assert tidy("네, 그러면 제가 확인하겠습니다") == "확인 예정"


def test_each_sentence_is_tidied_and_acknowledgement_is_dropped() -> None:
    assert tidy("네 알겠습니다. 다음 주까지 정리하겠습니다.") == "다음 주까지 정리 예정"


@pytest.mark.parametrize(
    "said",
    [
        "네 알겠습니다",  # nothing but acknowledgement: keep the original
        "안 볼게요",  # a negation is not a plan
        "이건 못 하겠습니다",
        "언제까지 정리하면 될까요?",  # a question is not a commitment
        "그렇게 하죠",  # no noun in front of the verb
        "저는 반대예요",  # an ending this list does not know
        "이건 다시 얘기해 봐야 할 것 같아요",
        "",
    ],
)
def test_no_rule_leaves_the_sentence_as_it_was_said(said: str) -> None:
    assert tidy(said) == said


def test_a_word_that_contains_a_filler_is_not_a_filler() -> None:
    """ "그 방향으로" is content; only a standalone marker at the start is dropped."""
    assert tidy("그 방향으로 진행하겠습니다") == "그 방향으로 진행 예정"


def test_tidying_is_stable() -> None:
    """What a person confirmed is not rewritten by a second pass."""
    once = tidy("제가 자료 정리해서 공유하겠습니다")
    assert tidy(once) == once


@pytest.mark.parametrize(
    ("said", "tidied"),
    [
        # ㄷ, ㅂ and ㅅ irregular verbs change before the ㄹ.
        ("오늘 저녁에 듣겠습니다", "오늘 저녁에 들을 예정"),
        ("같이 걷겠습니다", "같이 걸을 예정"),
        ("제가 옆에서 돕겠습니다", "옆에서 도울 예정"),
        ("이름은 제가 짓겠습니다", "이름은 지을 예정"),
        # Regular verbs with the same final consonants are not touched.
        ("제가 웃겠습니다", "웃을 예정"),
        ("파일을 씻겠습니다", "파일을 씻을 예정"),
        ("먹겠습니다", "먹을 예정"),
        # An intention.
        ("제가 화면 녹화된 거 몇 개 보려고요", "화면 녹화된 거 몇 개 볼 예정"),
        ("메일은 제가 두려고요", "메일은 둘 예정"),
        ("점심은 먹으려고요", "점심은 먹을 예정"),
        ("서버 확인해 보려고 해요", "서버 확인 예정"),
        ("표는 제가 만들려고요", "표는 만들 예정"),
        # An agreement that was made.
        (
            "그거 제가 이번 주까지 표 정리해 드리기로 했었죠",
            "그거 이번 주까지 표 정리해 드리기로 함",
        ),
    ],
)
def test_irregular_verbs_and_intentions(said: str, tidied: str) -> None:
    assert tidy(said) == tidied


def test_a_verb_whose_form_the_spelling_does_not_settle_is_left_alone() -> None:
    """묻다 is "ask" (물을) and "bury" (묻을): either could be wrong."""
    said = "제가 그 건은 묻겠습니다"
    assert tidy(said) == said


@pytest.mark.parametrize(
    ("said", "tidied"),
    [
        # "말하다" is a verb of its own: the noun 말 alone says nothing.
        ("그건 제가 센터장님께 말해 둘게요", "그건 센터장님께 말해 둘 예정"),
        ("그렇게 말할게요", "그렇게 말할 예정"),
        ("제가 일하겠습니다", "일할 예정"),
        # "-야겠다" is "has to", not "will".
        ("그건 보고서 앞쪽에 넣어야겠다", "그건 보고서 앞쪽에 넣어야 함"),
        ("제가 다시 확인해야겠습니다", "다시 확인해야 함"),
        # 하다 after a noun of two syllables or more is the noun.
        ("이건 제가 요청해 볼게요", "이건 요청 예정"),
    ],
)
def test_a_verb_of_its_own_and_a_must(said: str, tidied: str) -> None:
    assert tidy(said) == tidied


@pytest.mark.parametrize("said", ["네 그럴게요", "할게요", "제가 하겠습니다"])
def test_an_answer_that_points_at_something_said_before_is_left_alone(said: str) -> None:
    """ "그럴 예정" would say nothing; the person reads the line before it."""
    assert tidy(said) == said
