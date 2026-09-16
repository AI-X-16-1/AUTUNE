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

from autune_audio.eval.hike import (
    HikeLabels,
    Prediction,
    labels,
    read_predictions,
    score,
    select_sample_ids,
    summarise,
    utterances,
    write_prediction,
)


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


class TestLabels:
    def test_labels_come_without_audio(self, tmp_path: Path) -> None:
        """Scoring predictions made elsewhere needs the text, not 235 MB of WAV."""
        path = _write(tmp_path / "hike.parquet", [_row("a", "word")])

        (row,) = labels(path)

        assert isinstance(row, HikeLabels)
        assert not hasattr(row, "waveform")
        assert row.loanwords == (("버그", "bug"), ("세션", "session"))


def _labels(sample_id: str, cs_level: str = "word", category: str = "business") -> HikeLabels:
    return HikeLabels(
        sample_id=sample_id,
        reference="이번 bug는 session에 문제가 있었어",
        reference_labeled="<tag 이번> <tag bug> <tag 는> <tag session> <tag 에> 문제가 있었어",
        cs_level=cs_level,
        category=category,
        loanwords=(("버그", "bug"), ("세션", "session")),
    )


class TestScore:
    def test_a_perfect_hypothesis_scores_zero_everywhere(self) -> None:
        scored = score(_labels("a"), "이번 bug는 session에 문제가 있었어", seconds=2.0, elapsed=1.0)
        assert (scored.mer.mer, scored.pier.pier, scored.cer_normalised.cer) == (0.0, 0.0, 0.0)
        assert scored.sample_id == "a"
        assert (scored.seconds, scored.elapsed) == (2.0, 1.0)

    def test_the_loanword_list_reaches_mer_and_pier_but_not_cer(self) -> None:
        """CER is our metric, kept comparable with evaluation 01; MER and PIER are
        HiKE's, and HiKE forgives the Korean spelling."""
        scored = score(_labels("a"), "이번 버그는 세션에 문제가 있었어", seconds=2.0, elapsed=1.0)
        assert (scored.mer.mer, scored.pier.pier) == (0.0, 0.0)
        assert scored.cer_normalised.cer > 0.0

    def test_raw_cer_sees_the_punctuation_that_normalised_cer_does_not(self) -> None:
        scored = score(
            _labels("a"), "이번 Bug는 session에 문제가 있었어.", seconds=2.0, elapsed=1.0
        )
        assert scored.cer_raw.cer > 0.0
        assert scored.cer_normalised.cer == 0.0


class TestSummarise:
    def test_groups_are_means_of_per_utterance_scores_as_hike_reports_them(self) -> None:
        scores = [
            score(
                _labels("a", "word", "business"),
                "이번 bug는 session에 문제가 있었어",
                seconds=2.0,
                elapsed=1.0,
            ),
            score(
                _labels("b", "word", "medical"),
                "이번 bug는 session에 문제가 있었다",
                seconds=2.0,
                elapsed=3.0,
            ),
            score(
                _labels("c", "phrase", "business"),
                "이번 버그는 세션에 문제가 있었어",
                seconds=4.0,
                elapsed=4.0,
            ),
        ]

        summary = summarise(scores)

        assert summary["all"]["n"] == 3
        assert summary["all"]["seconds"] == 8.0
        assert summary["all"]["rtf"] == pytest.approx(8.0 / 8.0)
        # b: one syllable wrong out of 12 mixed tokens; a and c are 0 on MER.
        assert summary["all"]["mer"] == pytest.approx((0 + 1 / 12 + 0) / 3)
        assert summary["by_cs_level"]["word"]["n"] == 2
        assert summary["by_cs_level"]["phrase"]["n"] == 1
        assert summary["by_category"]["business"]["n"] == 2
        assert summary["by_cs_level"]["word"]["rtf"] == pytest.approx(4.0 / 4.0)

    def test_an_empty_run_is_refused(self) -> None:
        with pytest.raises(ValueError):
            summarise([])


class TestPredictions:
    def test_a_run_is_appended_one_line_at_a_time_and_read_back(self, tmp_path: Path) -> None:
        """A 2-hour CPU run must survive a crash at minute 90: every utterance is
        flushed as it finishes, and --resume skips what is already there."""
        path = tmp_path / "predictions.jsonl"
        first = Prediction(sample_id="a", hypothesis="이번 bug는", seconds=1.5, elapsed=2.0)
        second = Prediction(sample_id="b", hypothesis="session에", seconds=1.0, elapsed=1.0)

        with path.open("a", encoding="utf-8") as fh:
            write_prediction(fh, first)
        with path.open("a", encoding="utf-8") as fh:
            write_prediction(fh, second)

        assert read_predictions(path) == [first, second]

    def test_a_missing_file_reads_as_no_predictions(self, tmp_path: Path) -> None:
        assert read_predictions(tmp_path / "none.jsonl") == []
