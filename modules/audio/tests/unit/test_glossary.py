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
    def test_the_most_important_term_is_emitted_last(self) -> None:
        """``initial_prompt`` is truncated from the front, so last is safest.

        ``previous_tokens[-(max_length // 2 - 1):]`` keeps the tail. Anything
        that has to survive an over-long prompt goes at the end.
        """
        prompt = build_prompt(participants=["박준호"])
        assert prompt.rstrip(".").endswith("박준호")

    def test_participants_outrank_the_project_stack(self) -> None:
        """A wrong name does not look wrong, and B maps assignees by name."""
        prompt = build_prompt(participants=["최유나"])
        assert prompt.index("pyannote") < prompt.index("최유나")

    def test_corrections_outrank_the_stack_and_yield_to_participants(self) -> None:
        """Somebody fixing a term by hand is better evidence than our guess."""
        prompt = build_prompt(participants=["김서연"], corrections=["KURE-v1"])
        assert prompt.index("pyannote") < prompt.index("KURE-v1") < prompt.index("김서연")

    def test_pyannote_is_last_of_the_stack_terms(self) -> None:
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

    def test_the_estimate_leans_high(self) -> None:
        """Overrunning the window drops terms silently, which is the failure
        this module exists to prevent. A high estimate wastes budget; a low one
        loses a term we believed we had sent."""
        assert estimate_tokens("pyannote") >= 4
        assert estimate_tokens("박준호") >= 3


class TestAssembly:
    def test_nothing_to_say_produces_an_empty_string(self) -> None:
        """Whisper treats "" and None alike, so the caller needs no special case."""
        assert build_prompt(terms=[]) == ""

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
