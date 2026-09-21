# Evaluation 02 — `speaker-diarization-3.1` on the same recording

**Date:** 2026-09-10 · **Owner:** 김민경 · **Module:** A, Audio Pipeline

Evaluation 01 left one of module A's three targets unmeasured: DER. This fills
it in, on the same 13-minute recording, and checks the join that turns turns and
words into utterances.

---

## 1. Result

| | Result | Target |
| --- | --- | --- |
| Speakers found | **4**, correct | 4 |
| **DER** (S1) | **0.141** | ≤ 0.15 |
| Words preserved through the join | 945 → 945 | all |
| Diarization time, 11m37s on CPU | 377 s | — |

`pyannote/speaker-diarization-3.1`, CPU, on the waveform in memory.

**Which track the numbers come from.** pyannote returns two: `speaker_diarization`,
which contains overlapping turns, and `exclusive_speaker_diarization`, which does
not. DER is scored against the first, because that is what the metric is defined
over. The join consumes the second, because a word can only belong to one
speaker and reading overlapping turns hands an interruption to whoever started
talking first.

On this recording the choice makes almost no difference — 155 turns against 147,
the same four speakers, the same 49 utterances, the same DER — and that is the
point rather than a reassurance. **S1 to S3 are one person at a time, so this
recording cannot measure the thing the two tracks disagree about.**

## 2. What the reference is, and what it is not

There are no per-speaker tracks, so the reference is written by hand — which is
only honest for the sections where one known person speaks at a time. That is
S1: four people reading the same calibration sentence in a known order.

Two things go into it, and **neither comes from the diarizer**:

- **Block boundaries** come from the transcript's content — the second each
  speaker says their own name.
- **Speech spans inside a block** are Whisper's segment times, so the silence
  between readings is not counted as speech.

The second one matters more than it sounds. A first attempt treated each block
as continuous speech from one name to the next, and scored **0.209** — the
diarizer was being charged for correctly finding the silences that a coarse
reference had called speech. The number below is against a reference that only
claims speech where there was speech.

**S4 and stress drill 03 are not scored here.** They are where people overlap
and interrupt, which is where DER means the most and where a hand-written
reference stops being honest. Those need the per-speaker tracks the cue sheet
asks for.

## 3. The separation itself

Speaker time is close to even, which is what the cue sheet asked for:

```
SPEAKER_03  153 s      SPEAKER_00  150 s
SPEAKER_02  124 s      SPEAKER_01  121 s
```

And the S1 blocks land where the cue sheet puts them:

```
  1.5 –  39.7   SPEAKER_00   …인텔리전스를 맡은 김서연입니다
 40.0 –  73.7   SPEAKER_01   …오디오 파이프라인을 맡은 박준우입니다
 74.0 – 108.6   SPEAKER_03   …구조와 추출을 맡은 이지훈입니다
108.8 – 149.0   SPEAKER_02   …갭 맥락을 맡은 최윤아입니다
149.0 – 181.7   SPEAKER_00   PM이면서 인텔리전스 대시보드를 담당합니다
```

The last line is the check worth making: S2 opens with the PM, and the diarizer
brings `SPEAKER_00` back for it without being told anyone had spoken before.

## 4. The join

123 Whisper segments and 155 turns become **49 utterances**, and all 945 words
survive.

The count drops because consecutive segments by one speaker merge — a diarizer's
turn is not a sentence and neither is an utterance. What matters is that no word
moved: attributing a segment to whoever holds most of it would put one person's
words in another's mouth, and module B keys commitments on who said them.

See `modules/audio/src/autune_audio/speakers.py` for the three judgements the
join makes and why.

## 5. What is still open

- **DER on overlapping speech.** Needs per-speaker tracks. Next recording.
  Until then nothing here measures the overlap path: the two diarization tracks
  produce the same answer on this audio, and a bug that only appears when two
  people talk at once would be invisible in every number above. One was found by
  reading the code (#136) rather than by measuring.
- **Identification.** Turns carry a local label — `SPEAKER_00` means "the same
  voice as the other turns with this label", nothing more. Matching a label to a
  person is the second half of #6.
- **377 s for 11m37s** is 0.54 real-time on CPU, on top of transcription's
  0.73: **RTF 0.73 + 0.54 = 1.27 for the two model stages.** (Not "886 s" —
  the transcription second count was never recorded, only its rounded RTF,
  and 0.725–0.735 × 697 spans 882–889.) `docs/modules/audio.md` sets two
  targets, ≤ 1.5× at six weeks and ≤ 1× at three months, and an earlier draft
  of this line read them as one and called the pass a failure (#186).
  Against the target as the denominator: **1.27 leaves 15% of the six-week
  budget and exceeds the three-month one by 27%.** Two things that pass
  leaves out. **The target is end to end** and 1.27 is not: decode,
  repetition check, speaker join, masking (patterns and a recogniser) and the
  database write are outside it, and whether they fit in the remaining
  0.23× — about 160 s on this file — has not been measured. And **this is one
  11-minute file**: the 2m45s file already sits at RTF 1.19 for transcription
  alone (01, section 2), because model loading is a fixed cost, so a short
  meeting does not pass. The GPU is for the three-month target and for the
  0.3 transcription RTF that 01-baseline's section 2 table and section 5
  (row 6) ask for, not for the MVP. The margin is thin: a 45-minute meeting
  at 1.27× is 57 minutes of model time before anything else runs.
