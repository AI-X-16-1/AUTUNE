"""Pick the live speaker threshold on a real recording.

Cuts the recording with the live segmenter, embeds every utterance once
(cached to ``--cache`` as vectors and bounds -- never audio), replays the
tracker across thresholds, and prints a table against a hand-written
reference. Design: ``docs/modules/audio-live-speakers.md`` section 6.

    uv run --package autune-audio python modules/audio/scripts/evaluate_live_speakers.py \\
        ~/recordings/s1-s4.wav \\
        --reference "1.5-39.7:A,40.0-73.7:B,74.0-108.6:C,108.8-149.0:D,149.0-181.7:A" \\
        --until 182 --speakers 4 --cache /tmp/live-speakers.npz

The recording is meeting audio and stays with the module owner (invariant
11); this script reads it, embeds it, and keeps only the vectors.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from autune_audio.config import get_settings
from autune_audio.decoding import decode
from autune_audio.eval.speakers import parse_reference, reference_speaker, sweep
from autune_audio.live.embedder import Embedder
from autune_audio.live.segmenter import Segmenter
from autune_audio.schemas import SAMPLE_RATE, Waveform

FRAME = SAMPLE_RATE // 5  # 200 ms, what the browser sends


def cut(waveform: Waveform, until: float | None) -> list[tuple[float, float, np.ndarray]]:
    samples = waveform.samples
    if until is not None:
        samples = samples[: int(until * SAMPLE_RATE)]
    segmenter = Segmenter(min_silence_ms=get_settings().live_min_silence_ms)
    pieces = []
    for i in range(0, len(samples), FRAME):
        for seg in segmenter.feed(samples[i : i + FRAME]):
            pieces.append((seg.start, seg.end, seg.waveform.samples))
    last = segmenter.flush()
    if last is not None:
        pieces.append((last.start, last.end, last.waveform.samples))
    return pieces


def embed_all(pieces: list[tuple[float, float, np.ndarray]]) -> tuple[np.ndarray, list[float]]:
    embedder = Embedder(token=get_settings().hf_token)
    embedder.warm_up()
    vectors, costs = [], []
    for _, _, samples in pieces:
        started = time.monotonic()
        vectors.append(embedder.embed(Waveform(samples=samples)))
        costs.append(time.monotonic() - started)
    return np.stack(vectors), costs


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("recording", type=Path)
    parser.add_argument("--reference", required=True, help='"start-end:SPEAKER,..." in seconds')
    parser.add_argument("--until", type=float, default=None, help="only the first N seconds")
    parser.add_argument("--speakers", type=int, default=None, help="head count for the capped run")
    parser.add_argument("--cache", type=Path, default=None, help=".npz of vectors and bounds")
    parser.add_argument("--thresholds", default="0.40,0.45,0.50,0.55,0.60,0.65,0.70,0.75,0.80")
    args = parser.parse_args()

    if args.cache is not None and args.cache.exists():
        data = np.load(args.cache)
        vectors: np.ndarray = data["vectors"]
        bounds: np.ndarray = data["bounds"]
        costs = list(data["costs"])
    else:
        pieces = cut(decode(args.recording), args.until)
        vectors, costs = embed_all(pieces)
        bounds = np.array([(s, e) for s, e, _ in pieces])
        if args.cache is not None:
            np.savez(args.cache, vectors=vectors, bounds=bounds, costs=np.array(costs))

    blocks = parse_reference(args.reference)
    seconds = [float(e - s) for s, e in bounds]
    truth = [reference_speaker(blocks, float(s), float(e)) for s, e in bounds]
    thresholds = [float(t) for t in args.thresholds.split(",")]

    print(
        f"utterances {len(seconds)}, scored {sum(t is not None for t in truth)}, "
        f"embed per utterance mean {np.mean(costs) * 1000:.0f} ms, "
        f"max {np.max(costs) * 1000:.0f} ms"
    )
    print()
    print("| threshold | capped | clusters | purity | completeness |")
    print("| --- | --- | --- | --- | --- |")
    for score in sweep(vectors, seconds, truth, thresholds=thresholds, max_speakers=args.speakers):
        print(
            f"| {score.threshold:.2f} | {'yes' if score.capped else 'no'} | {score.clusters} "
            f"| {score.purity:.3f} | {score.completeness:.3f} |"
        )
    print()
    print(json.dumps({"reference_speakers": sorted({b.speaker for b in blocks})}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
