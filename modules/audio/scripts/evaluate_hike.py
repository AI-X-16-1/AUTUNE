"""Run the transcriber over HiKE and score it the way the HiKE paper does.

    # Sixty rows spread across the three CS levels, to check the harness
    uv run python modules/audio/scripts/evaluate_hike.py --limit 60 --seed 0 \\
        --predictions hike-large-v3.jsonl

    # The whole corpus; resume after an interruption with the same command
    uv run python modules/audio/scripts/evaluate_hike.py --predictions hike-large-v3.jsonl --resume

    # Score predictions made elsewhere (a GPU box, another model) without a model here
    uv run python modules/audio/scripts/evaluate_hike.py --score-only hike-qwen.jsonl

The model is the one the pipeline uses — ``AUTUNE_AUDIO_WHISPER_MODEL`` and
``AUTUNE_AUDIO_DEVICE`` pick it, exactly as in production — called through the
same ``transcribe()`` with **no glossary**, because HiKE has no meeting
vocabulary to build one from. The number therefore describes the model, not
our prompt; the prompt's effect is measured on the in-house recording.

Two language modes, and they answer different questions. ``--language ko``
(the default) is what production does, and on this corpus it makes Whisper
translate an English-matrix sentence into Korean rather than transcribe it.
``--language ''`` lets the model detect, which is how HiKE ran Whisper: only
that run can sit next to the paper's table.

The summary — means overall, by CS level and by category, plus the run's
settings — is written next to the predictions as ``<predictions>.summary.json``
and printed last on stdout. stdout also carries the pipeline's own log lines
(``autune_core`` logs there), so the file is the machine-readable copy.
Predictions go to the ``--predictions`` file, one line per utterance, flushed
as each finishes. Both files are outputs and are not committed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from autune_audio.eval.hike import (
    HikeScore,
    Prediction,
    download,
    labels,
    read_predictions,
    score,
    select_sample_ids,
    summarise,
    utterances,
    write_prediction,
)


def transcribe_corpus(
    corpus: Path,
    predictions: Path,
    *,
    limit: int | None,
    seed: int,
    resume: bool,
    language: str,
) -> dict[str, Any]:
    """Transcribe the selected rows into ``predictions``; return the run's settings."""
    # Imported here so --score-only never loads torch or the model.
    from autune_audio.config import get_settings
    from autune_audio.pipeline import _model, transcribe

    settings = get_settings()
    # Load the weights before the clock starts, or the first row's RTF is the
    # model load and not the model.
    _model()
    chosen = select_sample_ids(corpus, limit=limit, seed=seed)
    done = {p.sample_id for p in read_predictions(predictions)} if resume else set()
    todo = [sample_id for sample_id in chosen if sample_id not in done]
    run = {
        "model": settings.whisper_model,
        "device": settings.device,
        "language": language or "detect",
        "glossary": "",
        "limit": limit,
        "seed": seed,
        "selected": len(chosen),
        "already_done": len(chosen) - len(todo),
    }
    print(json.dumps(run), file=sys.stderr)

    with predictions.open("a" if resume else "w", encoding="utf-8") as fh:
        for n, utterance in enumerate(utterances(corpus, sample_ids=todo), start=1):
            started = time.perf_counter()
            transcription = transcribe(utterance.waveform, language=language or None, glossary="")
            elapsed = time.perf_counter() - started
            hypothesis = " ".join(segment.text.strip() for segment in transcription.segments)
            write_prediction(
                fh,
                Prediction(
                    sample_id=utterance.sample_id,
                    hypothesis=hypothesis.strip(),
                    seconds=round(utterance.waveform.duration, 3),
                    elapsed=round(elapsed, 3),
                ),
            )
            if n % 20 == 0 or n == len(todo):
                print(
                    json.dumps(
                        {
                            "done": n,
                            "of": len(todo),
                            "last_rtf": round(elapsed / max(utterance.waveform.duration, 1e-6), 2),
                        }
                    ),
                    file=sys.stderr,
                )
    return run


def score_predictions(corpus: Path, predictions: Path) -> tuple[list[HikeScore], int]:
    """Scores for every prediction that names a corpus row, and how many did not."""
    by_id = {p.sample_id: p for p in read_predictions(predictions)}
    if not by_id:
        raise SystemExit(f"no predictions in {predictions}")
    scores = [
        score(
            row,
            by_id[row.sample_id].hypothesis,
            seconds=by_id[row.sample_id].seconds,
            elapsed=by_id[row.sample_id].elapsed,
        )
        for row in labels(corpus, sample_ids=by_id.keys())
    ]
    return scores, len(by_id) - len(scores)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--predictions", type=Path, help="JSONL to write (and, with --resume, to continue)"
    )
    parser.add_argument(
        "--score-only", type=Path, metavar="PREDICTIONS", help="score this JSONL; load no model"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="rows to run, spread across CS levels"
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--resume", action="store_true", help="skip rows already in --predictions")
    parser.add_argument(
        "--overwrite", action="store_true", help="start --predictions again if it exists"
    )
    parser.add_argument(
        "--language",
        default="ko",
        help="Whisper language hint. 'ko' is the production setting; pass '' to let the "
        "model detect, which is how HiKE ran Whisper and the only run comparable to its table",
    )
    parser.add_argument(
        "--corpus", type=Path, default=None, help="local parquet; default downloads HiKE"
    )
    args = parser.parse_args(argv)

    corpus = args.corpus or download()
    run: dict[str, Any] | None = None
    if args.score_only:
        predictions = args.score_only
    else:
        if not args.predictions:
            parser.error("--predictions is required unless --score-only is given")
        predictions = args.predictions
        if (
            predictions.exists()
            and predictions.stat().st_size
            and not (args.resume or args.overwrite)
        ):
            parser.error(
                f"{predictions} already holds predictions; pass --resume to continue it "
                "or --overwrite to start again"
            )
        run = transcribe_corpus(
            corpus,
            predictions,
            limit=args.limit,
            seed=args.seed,
            resume=args.resume,
            language=args.language,
        )

    scores, unmatched = score_predictions(corpus, predictions)
    summary = summarise(scores)
    summary["predictions"] = str(predictions)
    summary["unmatched_predictions"] = unmatched
    if run is not None:
        summary["run"] = run
    summary_path = predictions.with_suffix(predictions.suffix + ".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
