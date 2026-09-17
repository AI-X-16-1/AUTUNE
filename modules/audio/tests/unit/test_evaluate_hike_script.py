"""The evaluate_hike.py CLI, with the transcriber stubbed and no model loaded.

What is worth pinning here is the file handling around a multi-hour run: an
existing predictions file is never silently truncated, --resume skips what is
done, and --score-only produces the summary sidecar without a model.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import soundfile as sf

from autune_audio.eval.hike import Prediction, read_predictions, write_prediction
from autune_audio.schemas import Segment, Transcription

SCRIPT = Path(__file__).parents[2] / "scripts" / "evaluate_hike.py"


@pytest.fixture(scope="module")
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("evaluate_hike", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wav(seconds: float = 0.5) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, np.zeros(int(seconds * 16_000), dtype=np.float32), 16_000, format="WAV")
    return buffer.getvalue()


def _row(sample_id: str, cs_level: str = "word") -> dict[str, object]:
    return {
        "audio": {"bytes": _wav(), "path": f"{sample_id}.wav"},
        "text": "이번 bug는 session에 문제가 있었어.",
        "text_normalized": "이번 bug는 session에 문제가 있었어",
        "text_pier_labeled": "<tag 이번> <tag bug> <tag 는> <tag session> <tag 에> 문제가 있었어",
        "cs_level": cs_level,
        "cs_levels_all": cs_level,
        "category": "software development",
        "loanwords": "[]",
        "sample_id": sample_id,
    }


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    path = tmp_path / "hike.parquet"
    pq.write_table(pa.Table.from_pylist([_row("a"), _row("b", "phrase"), _row("c")]), path)
    return path


@pytest.fixture
def stub_transcriber(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the model with a function that records which rows it saw."""
    import autune_audio.pipeline as pipeline

    seen: list[str] = []

    def transcribe(waveform: object, *, language: str | None, glossary: str) -> Transcription:
        seen.append(language or "detect")
        segment = Segment(start=0.0, end=0.5, text=" 이번 bug는 session에 문제가 있었어 ", words=())
        return Transcription(
            segments=(segment,), language="ko", language_probability=1.0, duration=0.5
        )

    monkeypatch.setattr(pipeline, "transcribe", transcribe)
    monkeypatch.setattr(pipeline, "_model", lambda: None)
    return seen


class TestPredictionsFile:
    def test_an_existing_file_is_not_overwritten_without_resume(
        self,
        script: ModuleType,
        corpus: Path,
        tmp_path: Path,
        stub_transcriber: list[str],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The likeliest mistake after an interruption is re-running the same
        command without --resume. That must not cost the hours already run."""
        predictions = tmp_path / "p.jsonl"
        with predictions.open("w", encoding="utf-8") as fh:
            write_prediction(fh, Prediction("a", "이번 bug는", 0.5, 0.5))

        with pytest.raises(SystemExit):
            script.main(["--corpus", str(corpus), "--predictions", str(predictions)])

        assert "--resume" in capsys.readouterr().err
        assert len(read_predictions(predictions)) == 1
        assert stub_transcriber == []

    def test_resume_over_a_torn_last_line_leaves_a_readable_file(
        self, script: ModuleType, corpus: Path, tmp_path: Path, stub_transcriber: list[str]
    ) -> None:
        """An interrupted write leaves half a line. Appending after it would glue
        the next row onto the fragment, and the file would fail at scoring time,
        hours later."""
        predictions = tmp_path / "p.jsonl"
        with predictions.open("w", encoding="utf-8") as fh:
            write_prediction(fh, Prediction("a", "이번 bug는", 0.5, 0.5))
            fh.write('{"sample_id": "b", "hypo')

        with pytest.warns(RuntimeWarning):
            script.main(["--corpus", str(corpus), "--predictions", str(predictions), "--resume"])

        assert [p.sample_id for p in read_predictions(predictions)] == ["a", "b", "c"]
        summary = json.loads((tmp_path / "p.jsonl.summary.json").read_text(encoding="utf-8"))
        assert summary["all"]["n"] == 3

    def test_a_limit_below_one_is_refused_before_anything_loads(
        self,
        script: ModuleType,
        corpus: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import autune_audio.pipeline as pipeline

        monkeypatch.setattr(pipeline, "_model", lambda: pytest.fail("model loaded"))

        with pytest.raises(SystemExit):
            script.main(
                [
                    "--corpus",
                    str(corpus),
                    "--predictions",
                    str(tmp_path / "p.jsonl"),
                    "--limit",
                    "0",
                ]
            )
        assert "--limit" in capsys.readouterr().err

    def test_resume_skips_rows_already_predicted(
        self, script: ModuleType, corpus: Path, tmp_path: Path, stub_transcriber: list[str]
    ) -> None:
        predictions = tmp_path / "p.jsonl"
        with predictions.open("w", encoding="utf-8") as fh:
            write_prediction(fh, Prediction("a", "이번 bug는", 0.5, 0.5))

        script.main(["--corpus", str(corpus), "--predictions", str(predictions), "--resume"])

        assert [p.sample_id for p in read_predictions(predictions)] == ["a", "b", "c"]
        assert len(stub_transcriber) == 2

    def test_overwrite_starts_the_file_again(
        self, script: ModuleType, corpus: Path, tmp_path: Path, stub_transcriber: list[str]
    ) -> None:
        predictions = tmp_path / "p.jsonl"
        with predictions.open("w", encoding="utf-8") as fh:
            write_prediction(fh, Prediction("stale", "x", 0.5, 0.5))

        script.main(["--corpus", str(corpus), "--predictions", str(predictions), "--overwrite"])

        assert [p.sample_id for p in read_predictions(predictions)] == ["a", "b", "c"]


class TestScoreOnly:
    def test_writes_the_summary_sidecar_without_a_model(
        self, script: ModuleType, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autune_audio.pipeline as pipeline

        monkeypatch.setattr(pipeline, "_model", lambda: pytest.fail("model loaded"))
        predictions = tmp_path / "p.jsonl"
        with predictions.open("w", encoding="utf-8") as fh:
            write_prediction(fh, Prediction("a", "이번 bug는 session에 문제가 있었어", 0.5, 0.5))
            write_prediction(fh, Prediction("b", "이번 bug는 session에 문제가 있었다", 0.5, 1.0))

        script.main(["--corpus", str(corpus), "--score-only", str(predictions)])

        summary = json.loads((tmp_path / "p.jsonl.summary.json").read_text(encoding="utf-8"))
        assert summary["all"]["n"] == 2
        assert summary["by_cs_level"]["phrase"]["n"] == 1
        assert "run" not in summary

    def test_predictions_for_unknown_rows_are_counted_not_dropped_silently(
        self, script: ModuleType, corpus: Path, tmp_path: Path
    ) -> None:
        predictions = tmp_path / "p.jsonl"
        with predictions.open("w", encoding="utf-8") as fh:
            write_prediction(fh, Prediction("a", "이번 bug는 session에 문제가 있었어", 0.5, 0.5))
            write_prediction(fh, Prediction("not-in-corpus", "무언가", 0.5, 0.5))

        script.main(["--corpus", str(corpus), "--score-only", str(predictions)])

        summary = json.loads((tmp_path / "p.jsonl.summary.json").read_text(encoding="utf-8"))
        assert summary["all"]["n"] == 1
        assert summary["unmatched_predictions"] == 1


class TestRunSettings:
    def test_the_language_mode_is_recorded_with_the_numbers(
        self, script: ModuleType, corpus: Path, tmp_path: Path, stub_transcriber: list[str]
    ) -> None:
        predictions = tmp_path / "p.jsonl"

        script.main(["--corpus", str(corpus), "--predictions", str(predictions), "--language", ""])

        summary = json.loads((tmp_path / "p.jsonl.summary.json").read_text(encoding="utf-8"))
        assert summary["run"]["language"] == "detect"
        assert stub_transcriber == ["detect", "detect", "detect"]
        assert "compute_type" not in summary["run"]


@pytest.fixture(autouse=True)
def quiet_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdout", io.StringIO())
