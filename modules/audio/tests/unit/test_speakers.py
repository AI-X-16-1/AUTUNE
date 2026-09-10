"""Two models disagree about where things are, and the join has to pick.

Whisper cuts at what sounds like a sentence; a diarizer cuts where the voice
changes. A segment routinely spans a turn, and attributing it whole puts one
person's words in another's mouth — which for module B is a commitment nobody
made.
"""

from __future__ import annotations

from autune_audio.schemas import Segment, Transcription, Turn, Word
from autune_audio.speakers import UNIDENTIFIED, assign_speakers, speaker_at


def word(text: str, start: float, end: float, probability: float = 0.9) -> Word:
    return Word(start=start, end=end, text=text, probability=probability)


def transcript(*segments: Segment) -> Transcription:
    return Transcription(
        segments=segments,
        language="ko",
        language_probability=1.0,
        duration=max((s.end for s in segments), default=0.0),
    )


def segment(*words: Word) -> Segment:
    return Segment(
        start=words[0].start,
        end=words[-1].end,
        text=" ".join(w.text for w in words),
        words=words,
    )


class TestWhereAWordBelongs:
    def test_a_word_belongs_to_the_turn_holding_its_middle(self) -> None:
        turns = (Turn(0.0, 1.0, "A"), Turn(1.0, 2.0, "B"))
        assert speaker_at(turns, word("네", 0.2, 0.4)) == "A"
        assert speaker_at(turns, word("네", 1.2, 1.4)) == "B"

    def test_a_word_starting_just_before_its_turn_still_belongs_to_it(self) -> None:
        """The first word of a turn is the one most likely to sit on the seam.

        Keying on the start would hand it to the previous speaker for the sake
        of a few milliseconds of disagreement between two models.
        """
        turns = (Turn(0.0, 1.0, "A"), Turn(1.0, 2.0, "B"))
        assert speaker_at(turns, word("좋습니다", 0.95, 1.45)) == "B"

    def test_a_word_in_a_gap_has_no_turn(self) -> None:
        """An honest answer. Where it goes is assign_speakers' decision."""
        turns = (Turn(0.0, 1.0, "A"), Turn(2.0, 3.0, "B"))
        assert speaker_at(turns, word("음", 1.4, 1.6)) is None


class TestCuttingASegmentAtATurnChange:
    """The case the word timings exist for."""

    def test_a_segment_spanning_a_turn_becomes_two_utterances(self) -> None:
        spoken = segment(
            word("그러면", 0.0, 0.4),
            word("그렇게", 0.4, 0.8),
            word("하시죠", 0.8, 1.0),
            word("네", 1.2, 1.4),
            word("좋습니다", 1.4, 1.9),
        )
        turns = (Turn(0.0, 1.1, "SPEAKER_00"), Turn(1.1, 2.0, "SPEAKER_01"))

        utterances = assign_speakers(transcript(spoken), turns)

        assert [u.speaker for u in utterances] == ["SPEAKER_00", "SPEAKER_01"]
        assert utterances[0].text == "그러면 그렇게 하시죠"
        assert utterances[1].text == "네 좋습니다"

    def test_consecutive_segments_by_one_speaker_become_one_utterance(self) -> None:
        """A diarizer's turn is not a sentence, and neither is an utterance."""
        first = segment(word("먼저", 0.0, 0.4), word("정리하면", 0.4, 0.9))
        second = segment(word("이렇게", 1.0, 1.4), word("됩니다", 1.4, 1.8))
        turns = (Turn(0.0, 2.0, "SPEAKER_00"),)

        utterances = assign_speakers(transcript(first, second), turns)

        assert len(utterances) == 1
        assert utterances[0].text == "먼저 정리하면 이렇게 됩니다"

    def test_a_speaker_returning_starts_a_new_utterance(self) -> None:
        """A B A is three utterances, not two — the third is a separate thing said."""
        spoken = segment(
            word("어떻게", 0.0, 0.4),
            word("할까요", 0.4, 0.8),
            word("저는", 1.2, 1.5),
            word("찬성", 1.5, 1.8),
            word("좋아요", 2.2, 2.6),
        )
        turns = (Turn(0.0, 1.0, "A"), Turn(1.0, 2.0, "B"), Turn(2.0, 3.0, "A"))

        assert [u.speaker for u in assign_speakers(transcript(spoken), turns)] == ["A", "B", "A"]


class TestGaps:
    def test_a_word_in_a_gap_stays_with_the_voice_it_was_part_of(self) -> None:
        """Dropping it loses transcript; a new speaker per pause shreds the meeting.

        The diarizer's uncertainty is about the boundary, not about whether
        anybody spoke.
        """
        spoken = segment(
            word("그러니까", 0.0, 0.4),
            word("음", 1.3, 1.5),  # in the gap
            word("이렇게요", 2.1, 2.6),
        )
        turns = (Turn(0.0, 1.0, "A"), Turn(2.0, 3.0, "A"))

        utterances = assign_speakers(transcript(spoken), turns)

        assert len(utterances) == 1
        assert utterances[0].text == "그러니까 음 이렇게요"

    def test_a_word_before_any_turn_takes_the_first_speaker(self) -> None:
        spoken = segment(word("안녕하세요", 0.0, 0.5), word("네", 1.2, 1.4))
        turns = (Turn(1.0, 2.0, "A"),)
        assert [u.speaker for u in assign_speakers(transcript(spoken), turns)] == ["A"]


class TestSegmentsWhisperDidNotTime:
    """Cutting one is impossible; dropping it loses transcript.

    And it would lose it only when diarization *succeeded* — the no-diarization
    path keeps such a segment. `pipeline` reads `s.words or ()`, which is the
    same admission that faster-whisper can return one.
    """

    def test_an_untimed_segment_survives_in_time_order(self) -> None:
        timed_first = segment(word("첫", 0.0, 0.5), word("문장입니다", 0.5, 1.0))
        untimed = Segment(start=1.5, end=2.5, text="단어 타이밍이 없는 세그먼트", words=())
        timed_last = segment(word("마지막", 3.5, 4.0), word("문장", 4.0, 4.5))

        utterances = assign_speakers(
            transcript(timed_first, untimed, timed_last),
            (Turn(0.0, 3.0, "A"), Turn(3.0, 6.0, "B")),
        )

        assert [u.text for u in utterances] == [
            "첫 문장입니다",
            "단어 타이밍이 없는 세그먼트",
            "마지막 문장",
        ]

    def test_it_is_attributed_by_its_own_midpoint(self) -> None:
        """Whole-segment attribution is wrong at boundaries, which is why it is
        the fallback rather than the rule."""
        untimed = Segment(start=2.5, end=3.5, text="경계에 걸친 문장", words=())
        utterances = assign_speakers(
            transcript(untimed), (Turn(0.0, 3.0, "A"), Turn(3.0, 6.0, "B"))
        )
        assert [u.speaker for u in utterances] == ["B"]


class TestNoDiarization:
    """A one-person recording is a real case, not an error.

    A voice memo, or a meeting where diarization failed. A transcript with one
    speaker is more useful than none, and the label says what is known.
    """

    def test_the_whole_recording_becomes_one_labelled_speaker(self) -> None:
        spoken = segment(word("혼자", 0.0, 0.4), word("녹음합니다", 0.4, 1.0))
        utterances = assign_speakers(transcript(spoken), ())
        assert [u.speaker for u in utterances] == [f"{UNIDENTIFIED} 1"]
        assert utterances[0].text == "혼자 녹음합니다"


class TestConfidence:
    def test_confidence_is_the_mean_not_the_minimum(self) -> None:
        """A paragraph is not as uncertain as its shakiest syllable."""
        spoken = segment(
            word("확실한", 0.0, 0.4, probability=1.0),
            word("말", 0.4, 0.6, probability=0.2),
            word("입니다", 0.6, 1.0, probability=0.9),
        )
        utterance = assign_speakers(transcript(spoken), (Turn(0.0, 1.0, "A"),))[0]
        assert utterance.confidence == (1.0 + 0.2 + 0.9) / 3


class TestTheJoinPreservesTheTranscript:
    def test_no_word_is_lost_and_none_is_duplicated(self) -> None:
        """Whatever the boundaries do, the words are all still there once."""
        spoken = segment(*(word(f"단어{i}", i * 0.5, i * 0.5 + 0.4) for i in range(12)))
        turns = (Turn(0.0, 1.7, "A"), Turn(1.7, 4.0, "B"), Turn(4.0, 6.5, "A"))

        utterances = assign_speakers(transcript(spoken), turns)
        rejoined = [w.text for u in utterances for w in u.words]

        assert rejoined == [f"단어{i}" for i in range(12)]
