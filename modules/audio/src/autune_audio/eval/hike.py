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
from collections.abc import Collection, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import soundfile as sf
from huggingface_hub import hf_hub_download

from autune_audio.schemas import SAMPLE_RATE, Waveform

DATASET = "thetaone-ai/HiKE"
PARQUET = "data/test-00000-of-00001.parquet"
CS_LEVELS = ("word", "phrase", "sentence")

_LABELS = (
    "sample_id",
    "text_normalized",
    "text_pier_labeled",
    "cs_level",
    "category",
    "loanwords",
)


@dataclass(frozen=True)
class HikeUtterance:
    sample_id: str
    waveform: Waveform
    reference: str
    """``text_normalized``: what MER and CER are scored against."""
    reference_labeled: str
    """``text_pier_labeled``: the reference with ``<tag …>`` around each switch."""
    cs_level: str
    category: str
    loanwords: tuple[tuple[str, str], ...]
    """(Korean, English) spellings the annotators marked as the same word."""


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
                sample_id=row["sample_id"],
                waveform=_waveform(row["audio"]["bytes"], row["sample_id"]),
                reference=row["text_normalized"],
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
