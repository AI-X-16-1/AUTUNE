# modules/audio — Module A: Audio Pipeline

Read `/CLAUDE.md` first. This file holds only what is specific to this module.
Full detail: `/docs/modules/audio.md`.

| | |
| --- | --- |
| **Package** | `autune_audio` |
| **Owner** | 김민경 |
| **Frontend** | `apps/web/src/features/transcript/` |
| **Table prefix** | `aud_` — mandatory on every table this module creates |
| **API prefix** | `/api/audio` |
| **Alembic branch** | `audio` |

## What this module does

Recording → Whisper STT → Pyannote diarization → speaker identification → PII
masking → raw-audio deletion → `utterances` → publish `TranscriptReady`.

## Consumes

Uploaded recording files and live browser microphone audio — both are MVP.
Nothing from another module.

Both paths converge on one persistence-and-publish step, and `TranscriptReady`
is still published once, at the end. Incremental transcript updates go straight
to the frontend over a live channel; never put partial results in a contract or
an event, or B, C and D inherit a problem they do not have.

## Publishes

`TranscriptReady` on `autune.transcript.ready`, consumed by B, C, and D.
Contract: `/docs/architecture/contracts.md`.

## Owns

- Shared entities `meetings`, `participants`, `utterances` in `packages/core`.
  **A is the only module that writes them.**
- `aud_jobs`, `aud_speaker_embeddings`, `aud_masking_events`, `aud_corrections`.

## AI stack

Whisper (`large-v3`, whisper.cpp on CPU), silero-vad, Pyannote, speaker
embeddings, regex + NER PII detection. LLM only for interim summaries.

## Privacy — the highest-risk module in the repo

Read `/docs/architecture/privacy.md` in full before editing anything here.

- Delete the recording in a `finally` block. Success, exception, and
  cancellation all delete it. There is no debug flag that keeps it.
- Mask before the first database write. The unmasked string stays a local
  variable — never logged, never in an exception message, never cached, never
  sent anywhere.
- `aud_masking_events` stores categories and counts, never masked content.
- Set `privacy.original_audio_deleted = true` only after the file is gone.

## Do not do here

- Classify utterances (B), extract topics (C), or link meetings (D).
- Persist audio anywhere, for any reason.
- Build Chrome-extension capture — it was dropped. The desktop app is Phase 2.

## Metrics

Diarization DER ≤ 15%, PII masking recall 0.95+, processing time ≤ 1.5×
recording length.

```bash
# What a corpus can measure, before committing to downloading it
uv run python modules/audio/scripts/inspect_corpus.py <corpus-root>

# Code-switching: the pipeline's transcribe() over HiKE, scored as the paper scores it
uv run python modules/audio/scripts/evaluate_hike.py --limit 60 --predictions out.jsonl
uv run python modules/audio/scripts/evaluate_hike.py --score-only out.jsonl   # no model needed
```

Scoring lives in `autune_audio.eval` and takes structures, not a model, so it
runs without a GPU. Two corpora, two jobs: the in-house recording measures the
glossary prompt, DER and masking; HiKE (`thetaone-ai/HiKE`, 1,121 Korean-English
utterances) measures only how the model survives a language switch, with no
glossary, against a published table. Reports go in
`docs/modules/audio-evaluations/` and the thread between them in `HISTORY.md`
(which lands with #174).

```bash
# Score the masker against the corpus. Both directions, one second, no model.
uv run --package autune-audio python -m autune_audio.eval
uv run --package autune-audio python -m autune_audio.eval --no-recogniser --verbose
```

**Recall is the target; precision is what says what the recall cost.** Recall
cannot fall when the masker covers more, so every widening looks free until
somebody runs real text — which is how the account pattern came to eat every ISO
date. Run this before and after any change to a pattern.

The corpus is `src/autune_audio/eval/fixtures/masking.jsonl` — `fixtures/`
because `.gitignore` reserves `corpus/` for downloaded training data. A row whose `masked`
equals its `text` is an example of something to leave alone, and counts the
same. Growing it means running the corpus over a transcript, not inventing rows.

A is the critical path — B, C, and D cannot integrate until `TranscriptReady`
is real. Ship it first.
