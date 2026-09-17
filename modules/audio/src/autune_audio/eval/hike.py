"""The HiKE corpus, read into the shapes the pipeline and the scorers take.

HiKE (``thetaone-ai/HiKE`` on Hugging Face, Apache-2.0) is 1,121 Korean-English
code-switched utterances, 2.2 hours, one parquet file with the audio inline as
16 kHz mono WAV. It has a single ``test`` split: it is for measuring, never for
training.

What it can and cannot measure for module A. It has no meeting glossary, so it
scores the model and not our prompt; it is one speaker per utterance, so there
is no DER; it carries no personal data, so there is no masking recall. Those
stay with the in-house recording. HiKE answers one question — how well the
model survives a language switch — against a published table.

The file is cached where the model weights are, never under ``TEMP_DIR``: that
directory is for meeting audio that gets deleted, and a benchmark copy sitting
there would be one ``rglob`` away from being treated as such.
"""

from __future__ import annotations

import io
import json
import random
import warnings
from collections.abc import Collection, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TextIO

import numpy as np
import pyarrow.parquet as pq
import soundfile as sf
from huggingface_hub import hf_hub_download

from autune_audio.eval.codeswitch import (
    MixedErrorRate,
    PointOfInterestErrorRate,
    mixed_error_rate,
    point_of_interest_error_rate,
)
from autune_audio.eval.korean import CharacterErrorRate, character_error_rate, normalise
from autune_audio.schemas import SAMPLE_RATE, Waveform

DATASET = "thetaone-ai/HiKE"
PARQUET = "data/test-00000-of-00001.parquet"
CS_LEVELS = ("word", "phrase", "sentence")

_LABELS = (
    "sample_id",
    "text",
    "text_normalized",
    "text_pier_labeled",
    "cs_level",
    "category",
    "loanwords",
)


@dataclass(frozen=True)
class HikeLabels:
    sample_id: str
    reference: str
    """``text_normalized``: what MER, PIER and normalised CER are scored against."""
    reference_raw: str
    """``text``: case and punctuation intact, for raw CER as evaluation 01 computed it."""
    reference_labeled: str
    """``text_pier_labeled``: the reference with ``<tag …>`` around each switch."""
    cs_level: str
    category: str
    loanwords: tuple[tuple[str, str], ...]
    """(Korean, English) spellings the annotators marked as the same word."""


@dataclass(frozen=True)
class HikeUtterance(HikeLabels):
    waveform: Waveform


def download(cache_dir: Path | None = None) -> Path:
    """Fetch the parquet once, into the Hugging Face cache (or ``cache_dir``)."""
    return Path(
        hf_hub_download(
            DATASET,
            PARQUET,
            repo_type="dataset",
            cache_dir=str(cache_dir) if cache_dir else None,
        )
    )


def select_sample_ids(path: Path, *, limit: int | None, seed: int) -> list[str]:
    """Which rows to run: all of them, or ``limit`` spread across CS levels.

    Sentence-level switching is 5% of the corpus, so a plain random subset of
    sixty rows may hold none. Every level gets at least one row and the rest
    of ``limit`` in proportion, so a short run still says something about
    every level.
    """
    if limit is not None and limit < 1:
        raise ValueError(f"limit must be at least 1, got {limit}")
    table = pq.read_table(path, columns=["sample_id", "cs_level"])
    ids_by_level: dict[str, list[str]] = {level: [] for level in CS_LEVELS}
    for sample_id, level in zip(
        table.column("sample_id").to_pylist(), table.column("cs_level").to_pylist(), strict=True
    ):
        ids_by_level.setdefault(level, []).append(sample_id)

    total = table.num_rows
    if limit is None or limit >= total:
        return [sample_id for ids in ids_by_level.values() for sample_id in ids]

    rng = random.Random(seed)
    chosen: list[str] = []
    for ids, share in zip(ids_by_level.values(), _shares(ids_by_level, limit), strict=True):
        chosen.extend(rng.sample(sorted(ids), share))
    return chosen


def _shares(ids_by_level: dict[str, list[str]], limit: int) -> list[int]:
    """One row per non-empty level first, the rest by largest remainder.

    Hamilton's method, so the shares sum to ``limit`` and the smallest level
    is not rounded away.
    """
    sizes = [len(ids) for ids in ids_by_level.values()]
    shares = [min(1, size) for size in sizes]
    if sum(shares) > limit:
        # Fewer rows asked for than there are levels: the largest levels get them.
        largest = sorted(range(len(sizes)), key=lambda i: sizes[i], reverse=True)[:limit]
        return [1 if i in largest else 0 for i in range(len(sizes))]
    remaining = limit - sum(shares)
    if remaining <= 0:
        return shares
    total = sum(sizes)
    quotas = [remaining * size / total for size in sizes]
    for i, quota in enumerate(quotas):
        shares[i] = min(sizes[i], shares[i] + int(quota))
    by_remainder = sorted(range(len(sizes)), key=lambda i: quotas[i] - int(quotas[i]), reverse=True)
    for i in by_remainder:
        if sum(shares) >= limit:
            break
        if shares[i] < sizes[i]:
            shares[i] += 1
    return shares


def labels(path: Path, *, sample_ids: Collection[str] | None = None) -> Iterator[HikeLabels]:
    """The text columns only — enough to score predictions made on another machine."""
    wanted = set(sample_ids) if sample_ids is not None else None
    for row in pq.read_table(path, columns=list(_LABELS)).to_pylist():
        if wanted is None or row["sample_id"] in wanted:
            yield _labels(row)


def utterances(
    path: Path, *, sample_ids: Collection[str] | None = None, batch_size: int = 32
) -> Iterator[HikeUtterance]:
    """Yield rows one at a time, decoding audio only for rows that are wanted."""
    wanted = set(sample_ids) if sample_ids is not None else None
    reader = pq.ParquetFile(path)
    for batch in reader.iter_batches(batch_size=batch_size, columns=[*_LABELS, "audio"]):
        for row in batch.to_pylist():
            if wanted is not None and row["sample_id"] not in wanted:
                continue
            yield HikeUtterance(
                **vars(_labels(row)),
                waveform=_waveform(row["audio"]["bytes"], row["sample_id"]),
            )


def _labels(row: dict[str, Any]) -> HikeLabels:
    return HikeLabels(
        sample_id=row["sample_id"],
        reference=row["text_normalized"],
        reference_raw=row["text"],
        reference_labeled=row["text_pier_labeled"],
        cs_level=row["cs_level"],
        category=row["category"],
        loanwords=tuple(
            (entry["Korean"], entry["English"]) for entry in json.loads(row["loanwords"])
        ),
    )


def _waveform(wav: bytes, sample_id: str) -> Waveform:
    samples, sample_rate = sf.read(io.BytesIO(wav), dtype="float32", always_2d=False)
    if sample_rate != SAMPLE_RATE:
        # No resampling: the pipeline's Waveform is 16 kHz by contract, and a
        # corpus that stops being 16 kHz is something to notice, not absorb.
        raise ValueError(f"HiKE row {sample_id} is {sample_rate} Hz, expected {SAMPLE_RATE}")
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    return Waveform(samples=np.ascontiguousarray(samples, dtype=np.float32))


@dataclass(frozen=True)
class HikeScore:
    """One utterance, scored four ways, with the time it took."""

    sample_id: str
    cs_level: str
    category: str
    seconds: float
    """Audio length."""
    elapsed: float
    """Wall-clock seconds the transcription took; ``elapsed / seconds`` is RTF."""
    mer: MixedErrorRate
    pier: PointOfInterestErrorRate
    cer_raw: CharacterErrorRate
    cer_normalised: CharacterErrorRate


def score(row: HikeLabels, hypothesis: str, *, seconds: float, elapsed: float) -> HikeScore:
    """HiKE's two metrics with its loanword rule, and our CER without it.

    CER is kept as evaluation 01 computed it — raw against the raw text, and
    normalised through ``korean.normalise`` on both sides — so the two corpora
    can be read side by side; MER and PIER are computed as the HiKE paper
    computed them so the number can sit in its table.
    """
    return HikeScore(
        sample_id=row.sample_id,
        cs_level=row.cs_level,
        category=row.category,
        seconds=seconds,
        elapsed=elapsed,
        mer=mixed_error_rate(row.reference, hypothesis, loanwords=row.loanwords),
        pier=point_of_interest_error_rate(
            row.reference_labeled, hypothesis, loanwords=row.loanwords
        ),
        cer_raw=character_error_rate(row.reference_raw, hypothesis),
        cer_normalised=character_error_rate(normalise(row.reference), normalise(hypothesis)),
    )


def summarise(scores: Sequence[HikeScore]) -> dict[str, Any]:
    """Means of per-utterance scores, overall and by CS level and category.

    Per-utterance means rather than corpus-level pooling, because that is how
    HiKE's ``result.json`` is computed and the paper's table is read from it.
    """
    if not scores:
        raise ValueError("no scores to summarise")

    def group(rows: Sequence[HikeScore]) -> dict[str, float | int]:
        seconds = sum(r.seconds for r in rows)
        return {
            "n": len(rows),
            "seconds": round(seconds, 1),
            "rtf": round(sum(r.elapsed for r in rows) / seconds, 3) if seconds else 0.0,
            "mer": sum(r.mer.mer for r in rows) / len(rows),
            "pier": sum(r.pier.pier for r in rows) / len(rows),
            "cer_raw": sum(r.cer_raw.cer for r in rows) / len(rows),
            "cer_normalised": sum(r.cer_normalised.cer for r in rows) / len(rows),
        }

    def by(key: str) -> dict[str, dict[str, float | int]]:
        buckets: dict[str, list[HikeScore]] = {}
        for row in scores:
            buckets.setdefault(getattr(row, key), []).append(row)
        return {name: group(rows) for name, rows in sorted(buckets.items())}

    return {"all": group(scores), "by_cs_level": by("cs_level"), "by_category": by("category")}


@dataclass(frozen=True)
class Prediction:
    """What the model said for one row, and how long it took.

    HiKE is a public benchmark, so its text may be written to disk — unlike a
    meeting transcript. The file is still an output and stays uncommitted.
    """

    sample_id: str
    hypothesis: str
    seconds: float
    elapsed: float


def write_prediction(fh: TextIO, prediction: Prediction) -> None:
    """One JSON line, flushed, so a run interrupted mid-way keeps what it did."""
    fh.write(json.dumps(asdict(prediction), ensure_ascii=False) + "\n")
    fh.flush()


def read_predictions(path: Path) -> list[Prediction]:
    """Every complete line. A last line cut short by an interruption is dropped
    with a warning rather than raised, because --resume is needed exactly then."""
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    predictions: list[Prediction] = []
    for number, line in enumerate(lines, start=1):
        try:
            predictions.append(Prediction(**json.loads(line)))
        except (json.JSONDecodeError, TypeError) as exc:
            if number != len(lines):
                raise ValueError(f"{path.name} line {number} is not a prediction") from exc
            warnings.warn(
                f"{path.name}: dropping incomplete last line {number}", RuntimeWarning, stacklevel=2
            )
    return predictions
