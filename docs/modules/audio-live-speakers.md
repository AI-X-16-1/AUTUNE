# Live speaker labels — design

**Date:** 2026-09-22 · **Owner:** 김민경 · **Module:** A · **Status:** Built (`audio/live-speakers`); threshold evaluation pending
(issue #306; branch `audio/live-speakers`, stacked on `audio/live-transcription`)

A live row on S13 gets a speaker label — `화자 1`, `화자 2` — from the voice
in the utterance, while the meeting is happening. The label is a cluster, not
a person: `speaker_id` stays null until the enrolled voice profiles of #6
exist. This is the follow-up the live-transcription design
(`audio-live-transcription.md`, section 1, "Speaker during recording: None")
owes to the S13 design, which shows "화자 3 미확인" above the transcript.

---

## 1. What is decided, and why

| Decision | Choice | Reason |
| --- | --- | --- |
| Unit of labelling | **One utterance, one embedding, one label.** The `Segmenter` already cuts the stream at silence; each cut gets a speaker embedding and joins a cluster | No second segmentation pass, no change to the row model: a row is still final once sent |
| Embedding model | **`pyannote/wespeaker-voxceleb-resnet34-LM`**, through `pyannote.audio.Inference` | It is the embedding model inside `pyannote/speaker-diarization-3.1`, the stored path's diarizer, so it is already downloaded (27 MB) and its vectors live in the same space the stored path will use for identification (#6). No new dependency |
| Clustering | **Nearest centroid, one threshold.** A vector joins the closest cluster when the cosine similarity clears the threshold, otherwise it opens a new one | State is one vector and a count per cluster, the decision is `O(clusters)`, and the whole thing is testable with numpy and no model. See section 3.2 for the alternatives and why not |
| Label stability | **A label never changes once shown.** No merges, no relabel message | S13 is built on rows that do not move (live design, section 1). A merge would need a new message type, a `useLiveSession` change and a "label changed under you" interaction the design does not have. The stored path's whole-file pyannote pass corrects an over-split after the upload |
| Speaker-count hint | **The live tracker reads the same hint the stored path reads** — `diarization_num_speakers`, else `diarization_max_speakers` — as a cap on how many clusters may open | One knob for both paths (#325). A demo laptop that knows the room has one person gets one label |
| Label text | **`화자 N`, on both paths.** `speakers.UNIDENTIFIED` becomes `"화자"`, and the stored path maps pyannote's `SPEAKER_00` to `화자 1` by order of first appearance | The same meeting is seen live and then stored; the two screens must agree. Today the stored screen shows `SPEAKER_02` raw. S13 spells it `화자 N`, and no consumer keys on the prefix: module B's `assignee_of` reads `speaker_id is None` only |
| Failure | **Degrade one row at a time; give up only after three in a row.** An embedder that cannot load logs `live_speaker_unavailable reason=<type> kind=<original type, or None for the first failure in a process>` once and the session goes on labelling every row `?`. A single failed embed or label costs only that row (`live_speaker_failed error=<type> streak=<n>`, label `?`); three consecutive failures log `live_speaker_unavailable reason=repeated_failures` once and switch labelling off for the rest of the session. The socket stays open throughout | Live rows are display; the stored path is the record. Closing the channel over a speaker label would lose the transcript to save the label |
| Threshold | **Chosen by evaluation** (section 6) on the 13-minute four-speaker recording of evaluation 02; a setting overrides it | A cosine threshold on a voice embedding is a measured number, not a guess. The provisional value until the evaluation runs is 0.55 |
| Storage | **Nothing.** Centroids live in the `LiveSession` and die with the socket | Same rule as every other live-path value. An embedding is biometric data; keeping it is #6's decision, with consent, not a side effect of this |

## 2. Shape and data flow

```
frame ─▶ Segmenter ─segment─▶ Transcriber.run(waveform) ──▶ text, confidence
                                        │
                                        ▼
                                 mask(text) ──▶ masked text (the unmasked
                                        │        string dies here)
                                        ▼
                    empty after mask, or confidence < min ──▶ dropped, not a row
                                        │
                                        ▼
                    Embedder.embed(segment.waveform) ──▶ vector
                                        │
                                        ▼
                    SpeakerTracker.label(vector, seconds) ─▶ "화자 2"
                                        │
                                        ▼
        Utterance(speaker="화자 2", speaker_id=None, text=masked)
```

Order inside `LiveSession._row`: transcribe → mask → drop (empty after
masking, or confidence below `live_min_confidence`) → embed + label → row. An
utterance that masks to nothing, or that Whisper was only guessing at, must
not open a cluster or move a centroid, so it never reaches the embedder — the
embedding is computed from `segment.waveform`, never from the text, so
dropping on the masked string does not reorder what the embedder sees. The
unmasked string is still a local that dies before the row exists (masking
boundary of the live design, section 3.5).

The row that crosses the socket is unchanged in shape. `row.utterance.speaker`
now carries `화자 N` instead of `?`; `speaker_id` is still `null`. The browser
already does the right thing with that: `TranscriptRow` prints
`utterance.speaker` in the attention colour when `speaker_id` is null, and
`LiveTranscript` collects the distinct labels of such rows into the
"화자 N · 누구인지 확인이 필요합니다" prompt. **No frontend change.**

The embedder runs on a worker thread under its own lock (section 3.1), taken
after the transcriber's lock is released, not shared with it: two models
fighting over one lock would serialise them for no reason, since the CPU cost
is what actually competes. The embedding costs under 60 ms per utterance on
CPU (section 3.1), noise next to the live lag budget (about 1.7 s from
utterance end to row with mlx; `HISTORY.md` section 2).

## 3. Server — `modules/audio/src/autune_audio/live/`

### 3.1 `Embedder` (`live/embedder.py`)

```python
class Embedder:
    lock: threading.Lock  # own lock, not shared with the transcriber

    def __init__(
        self, checkpoint: str = "pyannote/wespeaker-voxceleb-resnet34-LM", token: str = ""
    ) -> None: ...
    def warm_up(
        self,
    ) -> None: ...  # raises the load error first; later calls raise EmbedderUnavailable(kind)
    def embed(self, waveform: Waveform) -> np.ndarray: ...  # float32, shape (256,), unit length


class EmbedderUnavailable(RuntimeError):
    kind: str  # the original exception's type name
```

- `pyannote.audio` is imported inside the method that needs it, the way
  `diarization.PyannoteDiarizer` does, so `apps/api` does not load torch to
  answer a health check and unit tests need no model.
- `Inference(model, window="whole")` on the utterance's samples; `unit()`
  (`live/speakers.py`) normalises the result so that cosine similarity is a
  dot product — the same function the tracker uses on its own vectors, so a
  zero or non-finite embedding is refused the same way in both places. An
  utterance shorter than the model's receptive field is padded with zeros to
  0.5 s before inference — the tracker, not the embedder, decides what a
  short utterance may do (section 3.3).
- `self.lock` is the embedder's own `threading.Lock`, not shared with the
  transcriber: a slow embedding must not hold up another meeting's decode.
  `live/transcriber.off_loop(fn, *, lock=...)` takes the lock to hold as a
  keyword, defaulting to the transcriber's own.
- A failed `warm_up()` is remembered on the instance (`self._unavailable`, the
  failing exception's type name). Every later `warm_up()` call raises
  `EmbedderUnavailable(kind)` immediately — `kind` is that remembered type
  name — rather than retrying a download or an import that will not succeed
  on this machine this process. `embed()` calls `warm_up()` first, so it
  inherits the same short-circuit.
- Device: CPU. A probe on the reference laptop (M4 Pro) loaded the model in
  0.4 s and embedded 0.5 s / 1 s / 3 s / 10 s of audio in 12 / 12 / 19 / 55 ms
  — two orders of magnitude under the Whisper decode it follows. No GPU path
  is needed or built.
- The Hugging Face token is `AudioSettings.hf_token`, the same one the
  diarizer uses. The wespeaker checkpoint is public, so an empty token works
  for a cached model; the setting is passed through so a fresh machine can
  download it.

### 3.2 `SpeakerTracker` (`live/speakers.py`)

Pure numpy. Knows nothing about audio or models.

```python
@dataclass
class Cluster:
    total: np.ndarray  # float32 sum of every member vector, not normalised
    count: int

    @property
    def centroid(
        self,
    ) -> np.ndarray: ...  # total / ‖total‖, or the zero vector when ‖total‖ <= 1e-6
    def similarity(self, v: np.ndarray) -> float: ...  # centroid . v; 0.0 if cancelled
    def add(self, v: np.ndarray) -> None: ...  # total += v; count += 1


def unit(vector: np.ndarray) -> np.ndarray: ...  # raises ValueError: zero or non-finite norm


class SpeakerTracker:
    def __init__(
        self, *, threshold: float, min_seconds: float = 1.0, max_speakers: int | None = None
    ) -> None: ...
    def label(self, vector: np.ndarray, seconds: float) -> str: ...
    @property
    def clusters(self) -> int: ...
```

`unit()` is the one place an *incoming* vector is normalised: a unit-length float32 copy,
raising `ValueError` when there is no direction to keep — a zero norm, or a
NaN/inf that would poison every later score (`norm == 0` alone is `False` for
NaN). `label` calls it on its incoming vector; `Embedder.embed` (section 3.1)
calls it on its outgoing one. One function, so the two call sites cannot
disagree about what counts as a usable vector.

`label` applies these rules, in this order:

1. **First utterance** opens `화자 1` regardless of length — the row has to go
   somewhere, and there is nothing to compare against.
2. **Score.** `s = max(centroid · vector)` over the clusters, and `best` is
   that cluster.
3. **Short utterance** (`seconds < min_seconds`): assigned to `best` without
   updating its sum. A sub-second embedding is unreliable, and without this
   rule every "네" and "음" opens a new speaker. It does not move the
   centroid because the same unreliability would drag it.
4. **Join** when `s >= threshold`: `cluster.add(vector)` — the vector joins
   the running sum and `count` increments. The centroid is read off that sum
   fresh each time (`total / ‖total‖`), so it is the exact mean of every member
   vector, not a repeatedly renormalised running centroid that drifts toward
   the earliest ones.
5. **Cap** when the cluster count equals `max_speakers`: assigned to `best`
   with the same `add` as rule 4, even below the threshold. A room that
   knows it has N people does not get an (N+1)th label.
6. **Open** otherwise: a new cluster with `total = vector.copy()`, `count = 1`,
   label `화자 {n+1}`.

A cluster's members can in principle cancel to a (near-)zero sum
(`‖total‖ <= 1e-6`, only through a capped forced join of an opposite voice);
`centroid` then returns the zero vector and `similarity` reports `0.0` rather
than raising.

Labels are `f"{UNIDENTIFIED} {n}"` with `UNIDENTIFIED` imported from
`autune_audio.speakers`, so the two paths cannot drift apart.

Logs: `live_speaker_labelled cluster=<n> similarity=<s> opened=<bool>` — a
number and a boolean, no audio, no text. The vector is never logged.

**Alternatives considered.** Keeping every embedding and scoring a new
utterance against the k nearest in each cluster is more robust to a voice
drifting (distance from the microphone, tone), but the label-stability rule
means the extra accuracy would arrive too late to change what is on screen,
and the state grows with the meeting. It is the candidate if the evaluation
shows the centroid over-splitting badly. `diart` (online pyannote) was
rejected in #306: a new dependency, a second resident model, and not
credible in real time on the CPU next to Whisper.

### 3.3 `LiveSession` changes

- Constructor takes `embedder: Embedder | None` and `tracker: SpeakerTracker`.
  `None` is the degraded mode: every row is labelled `?`.
- `warm_up()` warms the transcriber and then the embedder. An embedder that
  fails to load logs `live_speaker_unavailable reason=<exception type>
  kind=<original type or None>` once and sets `self._embedder = None`; the
  session goes on. This is not the transcriber's 4503: a channel with no
  speaker labels is still a live transcript, a channel with no transcriber is
  not.
- `_row`'s order is transcribe → `mask()` → drop the row if the masked text
  is empty → drop it if confidence is below `live_min_confidence` → embed and
  label (`self._label(segment)`) → build the row. Masking runs before either
  drop check, and both drop checks run before labelling: an utterance that
  masks to nothing, or that Whisper was only guessing at, must not open a
  cluster or move a centroid.
- `_label` returns `NO_SPEAKER` (`"?"`) when the embedder is `None`. A single
  failed embed or tracker call costs only that row: it increments
  `self._label_failures`, logs `live_speaker_failed error=<type>
  streak=<n>`, and returns `NO_SPEAKER` — the embedder is not switched off
  yet. Once `self._label_failures` reaches `MAX_CONSECUTIVE_LABEL_FAILURES`
  (3), that call also logs `live_speaker_unavailable reason=repeated_failures`
  once and sets `self._embedder = None`, switching labelling off for the rest
  of the session. Any successful label resets the streak to 0.
- `UNIDENTIFIED = "?"` stays in `session.py` as the degraded label, renamed
  `NO_SPEAKER` to say what it now means.

### 3.4 `routes.build_session()`

Assembles `SpeakerTracker(threshold=settings.live_speaker_threshold,
min_seconds=settings.live_speaker_min_s, max_speakers=speaker_cap(settings))`
and `Embedder(token=settings.hf_token)`. `speaker_cap` (`live/speakers.py`)
reads `AudioSettings.speaker_bounds()` — the single source of head-count
precedence, also read by `diarization.py` for the stored path: an exact
`diarization_num_speakers` wins, else the `diarization_max_speakers` upper
bound, else no cap. All three `diarization_*_speakers` fields are
`Field(default=None, ge=1)`, so a value below 1 stops the API at startup and
cannot reach `build_session()` — see section 5 for the `ValueError` clause in
the hello handler, which is the belt for anything `SpeakerTracker`/`Segmenter`
construction refuses instead. One embedder instance per process, shared
across sessions the way `_model_for` shares Whisper — the model is read-only
once loaded, and the lock serialises calls anyway.

### 3.5 Settings (`config.py`)

| Setting | Env | Default | Meaning |
| --- | --- | --- | --- |
| `live_speaker_threshold` | `AUTUNE_AUDIO_LIVE_SPEAKER_THRESHOLD` | the value section 6 picks (provisionally 0.55) | cosine similarity at or above which an utterance joins a cluster |
| `live_speaker_min_s` | `AUTUNE_AUDIO_LIVE_SPEAKER_MIN_S` | 1.0 | utterances shorter than this may not open a cluster or move a centroid |

The speaker-count hint reuses `diarization_num_speakers` /
`diarization_max_speakers`; no new variable.

## 4. Stored path — `speakers.py`

Two changes, so the stored screen and the live screen say the same thing:

- `UNIDENTIFIED = "화자"`. The no-diarization fallback becomes `화자 1`.
- `assign_speakers` renames turn labels by order of first appearance in time:
  the first turn's speaker is `화자 1`, the next new one `화자 2`, and so on.
  Today `SPEAKER_00` reaches the utterance unchanged, which is what the stored
  screen showed on 2026-09-22 (`SPEAKER_02` on a one-person recording).
  Renaming happens in `assign_speakers`, on the `Turn` labels, before the
  word-level join — the join's logic does not change. The mapping is applied
  to every code path that reads a turn's speaker, including the untimed
  segment fallback.

The contract is not touched. `Utterance.speaker` is `str`; the docstring's
example (`'Speaker 2' when unidentified`) and `docs/architecture/contracts.md`
line 74 are prose. Issue #327 asks the team to update the example; this branch
does not edit `packages/`.

## 5. Errors, limits, privacy

- **Embedder cannot load** (extra missing, no network on a fresh machine):
  degraded mode, section 3.3. `ready` is still sent. The failure is
  remembered on the `Embedder` instance (`EmbedderUnavailable`, section 3.1),
  so a second session on the same process does not retry the same doomed
  load.
- **One utterance's embedding or labelling raises**: that row is `?`; a
  `live_speaker_failed` warning names the exception type and the current
  streak. Labelling keeps trying on later rows — a single bad vector is that
  utterance's problem — until three failures land in a row, at which point
  `live_speaker_unavailable reason=repeated_failures` logs once and the rest
  of the session is `?`. Exception types only, never messages — pyannote
  errors can quote file paths.
- **A bad head-count override cannot reach `build_session()`**: `Field(ge=1)`
  refuses `diarization_num_speakers`/`_min_speakers`/`_max_speakers` below 1
  when `AudioSettings` loads, and `autune_audio.router` imports `live.routes`,
  whose module level calls `get_settings()` to build the process-wide
  `Embedder` — so a value below 1 stops the API at startup; it cannot reach
  `build_session()`. The hello handler's 4503 branch catching `ValueError`
  next to `ConfigurationError` is the belt for anything `SpeakerTracker`'s own
  `max_speakers < 1` guard (or any other construction refusal) raises
  instead — a session that cannot be built is a 4503 with the status flip
  rolled back, never a meeting stranded in `recording`.
- **More clusters than people**: bounded by the hint when there is one,
  otherwise by the threshold. The evaluation reports how many clusters a
  four-person recording produces at each threshold.
- **A speaker change inside one utterance** (no pause) gets the label of
  whichever voice dominates the embedding. Live rows are display; `utt_live_`
  ids never claim to be the stored `utt_` rows, which pyannote cuts at the
  turn.
- **Privacy.** An embedding is biometric data. It exists as a local in
  `_row`, each cluster's summed vector is process memory only, nothing is
  written anywhere, and no log line carries a vector. The session's clusters
  are gone when the socket closes. This matches the live design's section 3.5
  and `privacy.md`. Enrolment and persistence are #6, behind consent.

## 6. Evaluation

`modules/audio/scripts/evaluate_live_speakers.py`, run by the module owner
against the 13-minute four-speaker recording of evaluation 02 (not in the
repository; it lives with the owner). It:

1. cuts the recording with the live `Segmenter` at the live settings, and
   embeds each utterance once, caching the vectors and utterance bounds to a
   local `.npy`/`.json` — never the audio;
2. sweeps the threshold from 0.40 to 0.80 in steps of 0.05, with and without
   the speaker-count hint (4), and for each reports
   - clusters opened (target: 4),
   - **purity** — for each cluster, the share of its utterances that belong
     to its majority reference speaker, averaged over utterances,
   - **completeness** — for each reference speaker, the share of their
     utterances that landed in their largest cluster, averaged over
     speakers;
   the reference is the hand-written S1 section of evaluation 02, the part
   where one known person speaks at a time;
3. times `Embedder.embed` per utterance on CPU;
4. writes `docs/modules/audio-evaluations/04-live-speakers.md` and the
   `HISTORY.md` entry that records the chosen default threshold and why.

The threshold that becomes the default is the one with the fewest clusters
at purity ≥ 0.9 without the hint; ties go to the lower threshold.

## 7. Tests

All run on CI without a model. The pattern for faking `pyannote.audio` is the
one `test_live_backends.py` uses for `mlx_whisper` and
`test_diarization_hints.py` uses for `torch`.

| File | What it proves |
| --- | --- |
| `test_live_speakers.py` | first vector → `화자 1`; a similar vector joins and moves the centroid; a dissimilar one opens `화자 2`; labels never change once given; the cap forces assignment below the threshold; a short utterance neither opens nor moves; a short first utterance still opens `화자 1`; labels use `speakers.UNIDENTIFIED` |
| `test_live_embedder.py` | the output is unit length and 256 wide; the checkpoint and token reach `Inference`; a load failure raises from `warm_up` and does not import torch at module scope |
| `test_live_session.py` | a row carries the tracker's label; a dropped row (empty, low confidence) does not reach the tracker; an embedder that raises gives `?` on that and later rows with exactly one warning; a `None` embedder gives `?`; the embedder receives the waveform, never the text (`capsys`/`caplog` privacy check extended) |
| `test_live_routes.py` | `build_session()` passes `diarization_num_speakers` as the cap, falls back to `diarization_max_speakers`, and `None` when neither |
| `test_speakers.py` | existing expectations updated to `화자 N`; `SPEAKER_00`/`SPEAKER_01` become `화자 1`/`화자 2` by first appearance; the untimed-segment fallback uses the renamed label |

## 8. Documentation touched

- `docs/modules/audio-live-transcription.md`: section 1 row "Speaker during
  recording", section 2 `row` description, section 7 "Show a speaker … on a
  live row" — each updated to point here.
- `docs/modules/audio.md`: one paragraph on live speaker labels.
- `docs/engineering/environments.md`: the two settings.
- `modules/audio/HISTORY.md`: the evaluation and the chosen threshold.

## 9. What this does not do

- Identify anyone. `speaker_id` is null on every live row until #6.
- Change a label once shown, merge clusters, or send any new socket message.
- Store an embedding, a centroid, or a label.
- Change the contract or the frontend.
- Run diarization inside an utterance.

## 10. Dependencies on other work

| | |
| --- | --- |
| #307 | The live channel. This branch is stacked on it and rebases onto `main` when it merges |
| #325 | The speaker-count hint this reuses |
| #6 | Identification and enrolment — consumes the same embedding space; not depended on |
| #259 · #283 | Unchanged |

## 11. What differed when it was built

Review on PR #328 changed the following from the plan above; the rest of this
document describes what shipped, not the delta.

- **Exact-mean centroid via summed vectors.** `Cluster` keeps `total` (the
  raw float32 sum) and `count`; `centroid` reads the mean off `total` fresh
  each call instead of renormalising a running centroid, which drifts toward
  the earliest vectors.
- **`unit()` shared and NaN-aware.** One function in `live/speakers.py`
  normalises for both the tracker and `Embedder.embed()`; it raises
  `ValueError` on a zero or non-finite norm rather than letting a NaN through.
- **Head count validated once.** `AudioSettings.speaker_bounds()` is the
  single source of the exact-count-else-bounds precedence, read by both
  `diarization.py` and `live.speakers.speaker_cap`; the three
  `diarization_*_speakers` fields are `Field(ge=1)`, refused at settings load.
- **Mask before drop before label.** `_row`'s order is transcribe → mask →
  drop (empty text, then low confidence) → embed and label, so a masked-empty
  or hallucinated utterance never reaches the tracker.
- **Per-row failure policy with a three-strike switch-off.** One bad embed or
  label costs only that row (`?`, one `live_speaker_failed` warning with the
  current streak); three consecutive failures switch labelling off for the
  rest of the session (`live_speaker_unavailable reason=repeated_failures`).
- **Embedder's own lock and remembered load failure.** `Embedder.lock` is
  separate from the transcriber's lock; after one failed `warm_up()`, every
  later call raises `EmbedderUnavailable(kind)` immediately instead of
  retrying a hopeless load.
- **`ValueError` also refuses 4503.** The hello handler's 4503 branch catches
  `(ConfigurationError, ValueError)` from `build_session()`, so a
  `SpeakerTracker`/`Segmenter` construction error closes the socket the same
  way a transcriber that cannot load does.
- **Eval replays `min_seconds`.** `simulate`/`sweep` take `min_seconds` as a
  required keyword instead of the tracker's own default, and
  `evaluate_live_speakers.py` defaults `--min-seconds` to
  `get_settings().live_speaker_min_s` and prints the value it used.
