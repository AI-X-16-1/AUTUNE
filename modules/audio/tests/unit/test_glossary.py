"""The glossary spends a fixed budget on the terms worth the most.

Every number here comes from evaluation 01 rather than from taste: the model
mangled these terms on a real recording, and the ordering rule follows from how
faster-whisper truncates a prompt.
"""

from __future__ import annotations

import pytest

from autune_audio.glossary import (
    PROJECT_TERMS,
    build_prompt,
    estimate_tokens,
)


class TestOrdering:
    """The two channels truncate from opposite ends, so the order follows the mode.

    Emitting one fixed order would put the participant names exactly where the
    default channel cuts.
    """

    def test_hotwords_puts_the_most_important_first(self) -> None:
        """``hotwords_tokens[: 223]`` keeps the head and drops the rest."""
        prompt = build_prompt(participants=["박준호"], mode="hotwords")
        assert prompt.startswith("박준호")

    def test_prompt_puts_the_most_important_last(self) -> None:
        """``previous_tokens[-223:]`` keeps the tail."""
        prompt = build_prompt(participants=["박준호"], mode="prompt")
        assert prompt.rstrip(".").endswith("박준호")

    def test_both_follows_hotwords(self) -> None:
        """One of the two has to be wrong; hotwords is the one doing the work."""
        assert build_prompt(participants=["박준호"], mode="both").startswith("박준호")

    def test_participants_outrank_the_project_stack(self) -> None:
        """A wrong name does not look wrong, and B maps assignees by name."""
        prompt = build_prompt(participants=["최유나"])
        assert prompt.index("최유나") < prompt.index("pyannote")

    def test_corrections_outrank_the_stack_and_yield_to_participants(self) -> None:
        """Somebody fixing a term by hand is better evidence than our guess."""
        prompt = build_prompt(participants=["김서연"], corrections=["KURE-v1"])
        assert prompt.index("김서연") < prompt.index("KURE-v1") < prompt.index("pyannote")

    def test_pyannote_is_the_most_important_stack_term(self) -> None:
        """It was mangled three ways, and C and D both key on it."""
        assert PROJECT_TERMS[-1] == "pyannote"


class TestBudget:
    def test_a_long_list_is_trimmed_from_the_least_important_end(self) -> None:
        prompt = build_prompt(participants=["박준호"], budget=40)
        assert "박준호" in prompt
        assert "Tailwind" not in prompt

    def test_the_estimate_stays_within_the_budget(self) -> None:
        prompt = build_prompt(participants=["김서연", "박준호", "이지훈", "최유나"])
        assert estimate_tokens(prompt) <= 200

    @pytest.mark.parametrize(
        ("term", "actual"),
        [
            ("large-v3-turbo", 7),
            ("ECAPA-TDNN", 8),
            ("faster-whisper", 5),
            ("silero-VAD", 5),
            ("Sentence-BERT", 4),
            ("cross-encoder", 4),
            ("Bolt for Python", 3),
            ("pyannote", 3),
            ("김민경", 3),
        ],
    )
    def test_the_estimate_is_an_upper_bound(self, term: str, actual: int) -> None:
        """``actual`` is what large-v3's own tokeniser produced for the term.

        Pinned here so the estimate can be checked without loading a model. An
        earlier version counted whitespace-separated words and came out 28-56%
        under — ECAPA-TDNN is eight tokens, not four — which let a glossary that
        looked to be inside the budget overrun the window and lose every
        participant name off the end.
        """
        assert estimate_tokens(term) >= actual


class TestAssembly:
    def test_nothing_to_say_produces_an_empty_string(self) -> None:
        """Whisper treats "" and None alike, so the caller needs no special case."""
        assert build_prompt(terms=[]) == ""

    def test_only_the_prompt_channel_gets_a_framing_sentence(self) -> None:
        """``hotwords`` is a list of words to boost, not a sentence.

        A prefix there boosts 회의 and 녹취록 and spends budget doing it.
        """
        assert build_prompt(mode="prompt").startswith("회의 녹취록")
        assert not build_prompt(mode="hotwords").startswith("회의")

    def test_a_name_in_two_sources_is_not_paid_for_twice(self) -> None:
        prompt = build_prompt(participants=["박준호"], corrections=["박준호"])
        assert prompt.count("박준호") == 1

    def test_a_repeat_keeps_the_higher_priority_spelling(self) -> None:
        """A user who corrected a term by hand spells it better than we guessed."""
        prompt = build_prompt(terms=["Pyannote"], corrections=["pyannote"])
        assert prompt.count("yannote") == 1
        assert "pyannote" in prompt

    def test_blank_entries_are_dropped(self) -> None:
        assert build_prompt(terms=["  ", ""], participants=["박준호"]).count(",") == 0


class TestTheTermsThatFailed:
    """Every term evaluation 01 caught the model mangling is in the list.

    Left as an explicit check because the list is the deliverable: a term that
    quietly falls out of it comes back as 파이노트 in a transcript module B keys
    action items on.
    """

    @pytest.mark.parametrize(
        "term",
        [
            "pyannote",  # 파이노트 · 파이어노트 · 하이에노트
            "faster-whisper",  # 페이스터 위시퍼 · 위스포
            "CTranslate2",  # 시트랜슬레이트 투 · CE Translator 2
            "ECAPA-TDNN",  # EC-KAPA-TDN · 이스카파 TDNN
            "silero-VAD",  # 슬로우 VAD · 실로 VED
            "DeBERTa",  # 데벨타
            "spaCy",  # 스페이시
            "NER",  # 넬로
            "Sentence-BERT",  # 센트베트
            "SetFit",  # Cepid
            "FastAPI",  # PEST API · 패스트 API
        ],
    )
    def test_the_term_is_in_the_default_prompt(self, term: str) -> None:
        assert term in build_prompt()
