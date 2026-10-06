"""``service.drop_bare_acknowledgements`` -- the fixed rule before step 4.

An ``ambiguous`` turn that is nothing but an acknowledgement has no content to
ask its speaker about. No session and no model. See ``test_pipeline_classify.py``
for the rule wired into the task, where what it prevents is visible.
"""

from __future__ import annotations

import pytest

from autune_contracts.enums import UtteranceKind
from autune_extraction.decisions import ClassifiedUtterance
from autune_extraction.service import drop_bare_acknowledgements

K = UtteranceKind


def utterance(text: str, kind: UtteranceKind | None = K.AMBIGUOUS) -> ClassifiedUtterance:
    return ClassifiedUtterance(id="utt_1", kind=kind, confidence=0.9, text=text)


@pytest.mark.parametrize(
    "text",
    [
        "네 알겠습니다.",
        "네, 알겠습니다",
        "넵 알겠습니다!",
        "아 네 알겠습니다…",
        "네. 알겠습니다.",
        "알겠습니다",
        "네네 알겠어요~",
        "예",
    ],
)
def test_an_ambiguous_turn_of_acknowledgement_only_becomes_none(text: str) -> None:
    [result] = drop_bare_acknowledgements([utterance(text)])

    assert result.kind is None
    assert result.text == text, "the turn stays in the sequence, for the decision gap"
    assert result.confidence == 0.9


@pytest.mark.parametrize(
    "text",
    [
        "네 알겠습니다, 한번 볼게요",
        "알겠습니다 그건 다음 주에 다시 얘기하죠",
        "네 그렇게 하겠습니다",
        "네 좋습니다",
        "감사합니다",
        "한번 볼게요",
    ],
)
def test_an_ambiguous_turn_that_says_anything_more_is_kept(text: str) -> None:
    """Agreeing to something is what the confirmation asks about."""
    [result] = drop_bare_acknowledgements([utterance(text)])

    assert result.kind is K.AMBIGUOUS


@pytest.mark.parametrize("kind", [K.COMMITMENT, K.DECISION, K.OPEN_QUESTION, K.CONCERN])
def test_the_same_words_under_another_kind_are_left_alone(kind: UtteranceKind) -> None:
    """After a request addressed to the speaker the words are an acceptance, and
    the classifier's ``commitment`` stands."""
    u = utterance("네 알겠습니다.", kind)

    assert drop_bare_acknowledgements([u]) == [u]


def test_a_turn_with_no_text_is_not_an_acknowledgement() -> None:
    """Punctuation alone, or nothing: no word was matched, so nothing is decided."""
    kept = drop_bare_acknowledgements([utterance("..."), utterance("")])

    assert [u.kind for u in kept] == [K.AMBIGUOUS, K.AMBIGUOUS]


def test_every_utterance_comes_back_in_order() -> None:
    before = [
        ClassifiedUtterance(
            id="utt_1", kind=K.DECISION, confidence=0.8, text="A안으로 가기로 했습니다"
        ),
        ClassifiedUtterance(id="utt_2", kind=K.AMBIGUOUS, confidence=0.7, text="네 알겠습니다."),
        ClassifiedUtterance(id="utt_3", kind=None, confidence=0.9, text="네 좋아요"),
    ]

    after = drop_bare_acknowledgements(before)

    assert [u.id for u in after] == ["utt_1", "utt_2", "utt_3"]
    assert [u.kind for u in after] == [K.DECISION, None, None]
