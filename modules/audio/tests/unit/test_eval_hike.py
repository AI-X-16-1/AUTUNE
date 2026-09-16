"""Reading the HiKE parquet, on a fixture shaped exactly like the real file.

The real file is 235 MB and lives in the Hugging Face cache; these tests build
a three-row copy of its schema so the loader runs without a network.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import soundfile as sf

from autune_audio.eval.hike import select_sample_ids, utterances


def _wav(seconds: float, sample_rate: int = 16_000) -> bytes:
    samples = np.zeros(int(seconds * sample_rate), dtype=np.float32)
    buffer = io.BytesIO()
    sf.write(buffer, samples, sample_rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def _row(sample_id: str, cs_level: str, *, wav: bytes | None = None) -> dict[str, object]:
    return {
        "audio": {"bytes": wav or _wav(0.5), "path": f"{sample_id}.wav"},
        "text": "이번 bug는 session에 문제가 있었어.",
        "text_normalized": "이번 bug는 session에 문제가 있었어",
        "text_pier_labeled": "<tag 이번> <tag bug> <tag 는> <tag session> <tag 에> 문제가 있었어",
        "cs_level": cs_level,
        "cs_levels_all": cs_level,
        "category": "software development",
        "loanwords": json.dumps(
            [{"Korean": "버그", "English": "bug"}, {"Korean": "세션", "English": "session"}],
            ensure_ascii=False,
        ),
        "sample_id": sample_id,
    }


def _write(path: Path, rows: list[dict[str, object]]) -> Path:
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path)
    return path


class TestUtterances:
    def test_a_row_becomes_a_waveform_and_its_labels(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "hike.parquet", [_row("a", "word")])

        (utterance,) = utterances(path)

        assert utterance.sample_id == "a"
        assert utterance.waveform.sample_rate == 16_000
        assert utterance.waveform.samples.dtype == np.float32
        assert utterance.waveform.samples.shape == (8_000,)
        assert utterance.reference == "이번 bug는 session에 문제가 있었어"
        assert utterance.reference_labeled.startswith("<tag 이번>")
        assert utterance.cs_level == "word"
        assert utterance.category == "software development"
        assert utterance.loanwords == (("버그", "bug"), ("세션", "session"))

    def test_only_the_requested_sample_ids_are_read(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "hike.parquet", [_row("a", "word"), _row("b", "phrase")])

        assert [u.sample_id for u in utterances(path, sample_ids={"b"})] == ["b"]

    def test_audio_at_another_rate_is_refused_by_id_not_content(self, tmp_path: Path) -> None:
        """The pipeline's Waveform is 16 kHz by contract; resampling here would
        hide a corpus change. The error names the row, never its text."""
        path = _write(tmp_path / "hike.parquet", [_row("odd", "word", wav=_wav(0.5, 8_000))])

        with pytest.raises(ValueError, match="odd") as raised:
            list(utterances(path))
        assert "bug" not in str(raised.value)


class TestSelectSampleIds:
    def test_a_limit_is_spread_across_cs_levels(self, tmp_path: Path) -> None:
        rows = [_row(f"w{i}", "word") for i in range(6)]
        rows += [_row(f"p{i}", "phrase") for i in range(3)]
        rows += [_row("s0", "sentence")]
        path = _write(tmp_path / "hike.parquet", rows)

        chosen = select_sample_ids(path, limit=5, seed=1)

        assert len(chosen) == 5
        levels = {c[0] for c in chosen}
        assert levels == {"w", "p", "s"}, "every level is represented, even the rare one"

    def test_the_same_seed_picks_the_same_rows(self, tmp_path: Path) -> None:
        rows = [_row(f"w{i}", "word") for i in range(10)]
        path = _write(tmp_path / "hike.parquet", rows)

        assert select_sample_ids(path, limit=3, seed=7) == select_sample_ids(path, limit=3, seed=7)
        assert select_sample_ids(path, limit=3, seed=7) != select_sample_ids(path, limit=3, seed=8)

    def test_no_limit_means_every_row(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "hike.parquet", [_row("a", "word"), _row("b", "phrase")])

        assert sorted(select_sample_ids(path, limit=None, seed=0)) == ["a", "b"]
