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

    @pytest.mark.parametrize(
        "line",
        [
            "010-1234-5678로 연락 주세요",
            "01012345678이에요",
            "주민번호 900101-1234567이고요",
            "계좌 110-123-456789로 보내주세요",
            "카드 1234-5678-9012-3456으로 결제했어요",
        ],
    )
    def test_a_particle_attached_to_the_value_does_not_hide_it(self, line: str) -> None:
        """`\\b` is a `\\w` edge and a Hangul syllable is `\\w`.

        So there is no word boundary between `5678` and `로`, and every one of
        these went through untouched — as did `find_unmasked`, which uses the
        same anchor. Two layers pierced by the same input. Korean attaches its
        particles directly and Whisper writes them that way; the patterns anchor
        on digit boundaries now.
        """
        assert mask(line).text != line
        assert find_unmasked(mask(line).text) == []

    @pytest.mark.parametrize(
        "number",
        [
            "070-1234-5678",
            "07012345678",
            "0800012345",
            "0505-123-4567",
            "+82-10-1234-5678",
            "82-10-1234-5678",
        ],
    )
    def test_a_number_outside_the_mobile_ranges_is_still_a_number(self, number: str) -> None:
        """An enumerated list of prefixes went stale before it shipped.

        070 is a common Korean VoIP range and it was passing through whole; the
        international form was worse, with the account pattern taking the first
        two groups and leaving the last eight digits standing. Partial masking
        is worse than none — the same argument the overlap rule makes.
        """
        masked = mask(number).text
        assert masked != number
        assert "1234" not in masked.replace("-1234-5678", "").replace("-****-5678", "")


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

    def test_a_partial_overlap_is_merged_rather_than_dropped(self) -> None:
        """Keeping the first span and skipping the next *shrank* the cover.

        A name at (0, 3) and an address at (2, 23) left everything from 3 to 23
        in the clear. Every other judgement in this file covers more when the
        answer is unclear; this was the one going the other way.
        """

        class NameThenAddress:
            def find(self, text: str) -> list[tuple[int, int, str]]:
                return [(0, 3, "name"), (2, 23, "address")]

        line = "김민경 서울시 강남구 테헤란로 123"
        result = mask(line, recogniser=NameThenAddress())
        assert result.text == "김" + "*" * (len(line) - 1)
        assert result.counts == {"address": 1}

    def test_a_span_of_text_and_digits_is_hidden_whole(self) -> None:
        """An address ends in a building number.

        Choosing the masking rule by "does this span contain digits" sent it
        down the numeric path, where the digits were starred and every Korean
        character passed through untouched.
        """

        class Address:
            def find(self, text: str) -> list[tuple[int, int, str]]:
                return [(0, 23, "address")]

        assert (
            "테헤란로" not in mask("김민경 서울시 강남구 테헤란로 123", recogniser=Address()).text
        )

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
    # Particles attach directly to the value. Korean is written this way and
    # Whisper writes it this way -- an earlier version of this corpus put a
    # space before every one of them, which is why it scored 1.000 while every
    # one of these lines went through untouched.
    ("연락처는 010-1234-5678입니다", "연락처는 010-****-5678입니다"),
    ("메일은 minkyoung@example.com로 부탁드립니다", "메일은 m***@example.com로 부탁드립니다"),
    ("주민번호 900101-1234567이고요", "주민번호 ******-1******이고요"),
    (
        "법인카드 1234-5678-9012-3456으로 결제했습니다",
        "법인카드 ****-****-****-3456으로 결제했습니다",
    ),
    ("계좌는 110-123-456789로 보내주세요", "계좌는 ***-***-**6789로 보내주세요"),
    ("준호님 번호 01098765432이에요", "준호님 번호 010****5432이에요"),
    ("사무실은 02-123-4567입니다", "사무실은 **-***-4567입니다"),
    (
        "hong.gil-dong+tag@sub.example.co.kr로 보냈어요",
        "h***@sub.example.co.kr로 보냈어요",
    ),
    ("대표번호는 070-1234-5678입니다", "대표번호는 ***-****-5678입니다"),
    ("해외에서는 +82-10-9876-5432로 걸어주세요", "해외에서는 +**-**-****-5432로 걸어주세요"),
    ("수신자부담 0800012345로 문의주세요", "수신자부담 ******2345로 문의주세요"),
    # A registered foreign national's number. The seventh digit is 5-8 for them
    # and the pattern accepted only 1-4, so it matched nothing at all.
    ("등록번호 900101-5123456이라고 하셨어요", "등록번호 ******-5******이라고 하셨어요"),
    # Run together, and a 6-2-6 layout. Both were missed by a pattern that
    # required literal hyphens and a short first group.
    ("계좌는 110234567890이에요", "계좌는 ********7890이에요"),
    ("KB계좌 123456-78-901234입니다", "KB계좌 ******-**-**1234입니다"),
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
