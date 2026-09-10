"""Masking is judged on recall, so these tests are mostly about what gets missed.

`docs/architecture/privacy.md` section 2: a wrongly masked word is an annoyance,
a leaked national ID number is an incident. Every case here that looks
over-cautious is over-cautious on purpose.
"""

from __future__ import annotations

import pytest

from autune_audio.eval.metrics import masking_recall
from autune_audio.masking import Masked, mask
from autune_integrations.privacy import find_unmasked


class TestTheDocumentedFormat:
    def test_a_mobile_number_keeps_the_shape_privacy_md_writes_down(self) -> None:
        assert mask("제 번호는 010-1234-5678 입니다").text == "제 번호는 010-****-5678 입니다"

    def test_an_email_keeps_its_first_letter_and_domain(self) -> None:
        """Enough to tell two people apart, not enough to write to either."""
        assert mask("minkyoung@example.com").text == "m***@example.com"

    def test_an_area_code_does_not_survive(self) -> None:
        """02 and 031 say where somebody is, and privacy.md's example is a mobile.

        Where the doc is silent this file takes the safer reading.
        """
        assert mask("02-123-4567").text == "**-***-4567"
        assert mask("031 234 5678").text == "*** *** 5678"

    def test_a_national_id_loses_its_birth_date(self) -> None:
        """Only the century-and-sex digit stays. The date half identifies too."""
        assert mask("900101-1234567").text == "******-1******"

    def test_a_card_keeps_its_last_four(self) -> None:
        assert mask("1234-5678-9012-3456").text == "****-****-****-3456"


class TestSpeechNotWriting:
    """A transcript is what somebody said, punctuated by whichever model heard it.

    The same number arrives spaced, hyphenated or run together depending on the
    sentence around it, and the shape we do not accept is the one that leaks.
    """

    @pytest.mark.parametrize(
        "spoken",
        ["010-1234-5678", "010 1234 5678", "01012345678", "010.1234.5678"],
    )
    def test_a_phone_number_is_caught_however_it_was_written(self, spoken: str) -> None:
        assert find_unmasked(mask(spoken).text) == []

    @pytest.mark.parametrize("spoken", ["900101-1234567", "900101 1234567", "9001011234567"])
    def test_a_national_id_is_caught_however_it_was_written(self, spoken: str) -> None:
        assert find_unmasked(mask(spoken).text) == []


class TestWhatIsReported:
    def test_counts_are_by_category(self) -> None:
        result = mask("010-1234-5678 과 a@b.com 과 010-9999-8888")
        assert result.counts == {"phone": 2, "email": 1}

    def test_the_repr_carries_no_transcript_and_no_spans(self) -> None:
        """This object is one line away from a log call.

        `aud_masking_events` stores exactly what is in here — categories and
        counts. A record of *what* was masked would recreate the unmasked column
        privacy.md forbids.
        """
        text = repr(mask("제 번호는 010-1234-5678 입니다"))
        assert "010" not in text
        assert "5678" not in text
        assert "spans=1" in text

    def test_clean_text_is_returned_unchanged(self) -> None:
        clean = "다음 회의는 금요일 오후 3시입니다"
        assert mask(clean) == Masked(text=clean)


class TestOverlaps:
    """Two detectors finding the same number is the normal case, not an error."""

    def test_the_longer_span_wins(self) -> None:
        """A national ID also looks like two number groups.

        Masking only part of one is worse than useless — the rest is still
        enough to identify somebody.
        """
        assert mask("900101-1234567").text == "******-1******"

    def test_a_second_detector_agreeing_does_not_double_mask(self) -> None:
        class Duplicate:
            def find(self, text: str) -> list[tuple[int, int, str]]:
                return [(0, 13, "phone")]

        result = mask("010-1234-5678", recogniser=Duplicate())
        assert result.text == "010-****-5678"
        assert result.spans == 1


class TestTheSecondDetector:
    """A Protocol, so this module works before a model is chosen.

    Whatever backs it runs in our own process or on our own inference server.
    Handing a transcript to somebody else's NER service is what this file exists
    to prevent.
    """

    def test_a_span_the_patterns_cannot_describe_is_still_masked(self) -> None:
        class NameFinder:
            def find(self, text: str) -> list[tuple[int, int, str]]:
                start = text.index("김민경")
                return [(start, start + 3, "name")]

        result = mask("담당자는 김민경 님입니다", recogniser=NameFinder())
        assert result.text == "담당자는 김** 님입니다"
        assert result.counts["name"] == 1

    def test_no_recogniser_is_a_supported_configuration(self) -> None:
        assert mask("010-1234-5678").text == "010-****-5678"


# A page of meeting talk with personal data planted in it, and the same text
# with those spans already hidden. Recall is measured by position, so the two
# have to tokenise the same way -- see eval.metrics.masking_recall.
CORPUS: list[tuple[str, str]] = [
    ("연락처는 010-1234-5678 입니다", "연락처는 010-****-5678 입니다"),
    (
        "메일 주소는 minkyoung@example.com 로 부탁드립니다",
        "메일 주소는 m***@example.com 로 부탁드립니다",
    ),
    ("주민번호 900101-1234567 확인 부탁드려요", "주민번호 ******-1****** 확인 부탁드려요"),
    (
        "법인카드 1234-5678-9012-3456 로 결제했습니다",
        "법인카드 ****-****-****-3456 로 결제했습니다",
    ),
    ("계좌는 110-123-456789 입니다", "계좌는 ***-***-**6789 입니다"),
    ("준호님 번호 01098765432 로 전화드릴게요", "준호님 번호 010****5432 로 전화드릴게요"),
    ("사무실은 02-123-4567 입니다", "사무실은 **-***-4567 입니다"),
    ("hong.gil-dong+tag@sub.example.co.kr 로 보냈어요", "h***@sub.example.co.kr 로 보냈어요"),
    ("다음 회의는 9월 18일 오후 3시 반입니다", "다음 회의는 9월 18일 오후 3시 반입니다"),
    (
        "A100 40기가 인스턴스는 시간당 4달러 90센트입니다",
        "A100 40기가 인스턴스는 시간당 4달러 90센트입니다",
    ),
]


def test_recall_on_the_planted_corpus_clears_the_target() -> None:
    """The 0.95 target in docs/modules/audio.md, measured rather than asserted.

    The last two rows carry no personal data and exist to catch the detector
    that scores well by masking everything — over-masking cannot raise recall,
    so they would show up as a wrong answer here rather than a good one.
    """
    scores = []
    for raw, expected in CORPUS:
        ours = mask(raw).text
        if "*" not in expected:
            assert ours == raw, "clean text was altered"
            continue
        scores.append(masking_recall(expected, ours).recall)

    assert sum(scores) / len(scores) >= 0.95


# Numbers a real meeting is full of, taken from the evaluation recording. The
# masker saw all 123 of its segments and changed none of them; these are the
# shapes that would have been the near misses.
NOT_PERSONAL = [
    "다음 회의는 9월 18일 금요일 오후 3시 반, 405호입니다",
    "A100 40기가 인스턴스로 돌리면 시간당 4달러 90센트고, 하루 6시간씩 5일이면 147달러입니다",
    "학습 데이터는 총 1,240건인데 그중 라벨링이 끝난 게 870건이에요",
    "지연은 평균 0.35초, 최대 1.2초까지 봤습니다",
    "MRR 2,475달러입니다. TAM은 180억 달러, SAM은 15억 달러입니다",
    "large-v3가 3.9, turbo가 4.4 나왔고요. RTF는 large가 0.42, turbo가 0.15예요",
    "오디오는 16킬로헤르츠 모노 WAV로 통일하고, 청크는 30초에 오버랩 2초로 잡았습니다",
]


@pytest.mark.parametrize("line", NOT_PERSONAL)
def test_a_meeting_full_of_numbers_is_left_alone(line: str) -> None:
    """Over-masking is allowed by policy but still has a cost.

    A transcript where every figure is starred is one people ask for the
    original of, and the original is the thing that does not exist. Run over the
    whole evaluation recording, the masker changed none of its 123 segments.
    """
    assert mask(line).text == line


def test_nothing_personal_survives_the_corpus() -> None:
    """The other direction: ask the outbound guard whether it would let this out.

    `find_unmasked` is what `packages/integrations` runs before a request
    leaves. If it finds something here, module A handed the rest of the system
    text it will refuse to deliver.
    """
    for raw, _ in CORPUS:
        assert find_unmasked(mask(raw).text) == [], "masked text still trips the outbound guard"
