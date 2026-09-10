"""Who said which words, and where one person's speech stops being theirs.

Two models disagree about where things are. Whisper cuts at what sounds like a
sentence; a diarizer cuts where the voice changes. Neither boundary respects the
other, so a segment routinely spans a turn:

    segment   |-------- 그러면 그렇게 하시죠 네 좋습니다 --------|
    turns     |---- SPEAKER_00 ----||------ SPEAKER_01 ------|

Attributing that segment to whoever holds the most of it puts one speaker's
words in another's mouth, and a transcript that does this is worse than one with
no speakers at all: module B keys commitments on who said them, and "네
좋습니다" attributed to the wrong person is a promise nobody made.

So the join is at word level. Whisper is asked for word timings from the start
for exactly this reason — see ``pipeline.transcribe``.

Nothing here loads a model. Diarization is a Protocol in ``diarization``; this
takes its output as values, which is what lets the hard part be tested without
a GPU or a Hugging Face token.
"""

from __future__ import annotations

from dataclasses import dataclass

from .schemas import Segment, Transcription, Turn, Word

UNIDENTIFIED = "Speaker"
"""Prefix for a voice we separated but did not put a name to.

The contract spells the display label this way — ``Speaker 2`` — and types
``speaker_id`` as ``str | None`` so consumers have to handle it. Identification
fills the id in later; it never renames this.
"""


@dataclass(frozen=True)
class Utterance:
    """One continuous stretch of speech by one speaker, ready to persist.

    Not the contract type: this has no id and no masked text yet, because
    masking runs after this and ids are assigned at the write. It is the shape
    the join produces, and ``service`` turns it into ``autune_contracts``'s.
    """

    speaker: str
    start: float
    end: float
    text: str
    words: tuple[Word, ...]
    confidence: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def speaker_at(turns: tuple[Turn, ...], word: Word) -> str | None:
    """Which turn owns ``word``, by where its middle falls.

    The midpoint rather than the start: a word that begins a few milliseconds
    before the diarizer thinks the turn does would otherwise go to the previous
    speaker, and the first word of a turn is the one most likely to sit on the
    seam.

    ``None`` when the word falls in a gap between turns. Diarizers leave gaps —
    they are where the model was not confident anyone was speaking — and a word
    there still has to go somewhere. ``assign_speakers`` decides where; this
    only reports the honest answer.
    """
    middle = (word.start + word.end) / 2
    for turn in turns:
        if turn.start <= middle < turn.end:
            return turn.speaker
    return None


def assign_speakers(transcription: Transcription, turns: tuple[Turn, ...]) -> tuple[Utterance, ...]:
    """Cut the transcript at turn changes and hand each piece to its speaker.

    Words are grouped into runs of the same speaker, in time order, and each run
    becomes one utterance. A Whisper segment that spans a turn change comes out
    as two utterances; several segments by one speaker in a row come out as one.

    **A word in a gap takes the speaker of the word before it.** Dropping it
    would lose transcript, and starting a new unidentified speaker for every
    pause would shred the meeting into fragments. The speech is real — the
    diarizer's uncertainty is about the boundary, not about whether anybody
    spoke — so it stays with the voice it was already part of.

    Segments whose words Whisper did not time are attributed as a whole, by the
    midpoint of the segment. That is the old behaviour and it is wrong at
    boundaries; it applies only when word timings are missing, which for this
    pipeline means never.
    """
    if not turns:
        return _one_utterance_per_segment(transcription.segments, speaker=f"{UNIDENTIFIED} 1")

    runs: list[list[Word]] = []
    speakers: list[str] = []
    current: str | None = None

    for word in _timed_words(transcription):
        speaker = speaker_at(turns, word) or current or turns[0].speaker
        if speaker != current or not runs:
            runs.append([])
            speakers.append(speaker)
            current = speaker
        runs[-1].append(word)

    return tuple(
        _utterance(speaker, tuple(words))
        for speaker, words in zip(speakers, runs, strict=True)
        if words
    )


def _timed_words(transcription: Transcription) -> list[Word]:
    """Every word that has a time, in order. Words without one cannot be cut on."""
    words = [w for s in transcription.segments for w in s.words]
    return sorted(words, key=lambda w: w.start)


def _utterance(speaker: str, words: tuple[Word, ...]) -> Utterance:
    """One run of words by one speaker.

    ``confidence`` is the mean of the word probabilities. The minimum would make
    a long utterance's score the score of its worst word, and a paragraph is not
    as uncertain as its shakiest syllable.
    """
    return Utterance(
        speaker=speaker,
        start=words[0].start,
        end=words[-1].end,
        text=" ".join(w.text for w in words),
        words=words,
        confidence=sum(w.probability for w in words) / len(words),
    )


def _one_utterance_per_segment(
    segments: tuple[Segment, ...], *, speaker: str
) -> tuple[Utterance, ...]:
    """No diarization: the whole recording is one voice as far as we know.

    Not an error. A one-person recording is a real case — a voice memo, or a
    meeting where diarization failed — and a transcript with one speaker is more
    useful than none. The label says what we know.
    """
    return tuple(
        Utterance(
            speaker=speaker,
            start=segment.start,
            end=segment.end,
            text=segment.text,
            words=segment.words,
            confidence=(
                sum(w.probability for w in segment.words) / len(segment.words)
                if segment.words
                else 0.0
            ),
        )
        for segment in segments
        if segment.text
    )
