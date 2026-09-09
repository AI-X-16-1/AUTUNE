"""The evaluation harness: five-way scoring, the eval set loader, and its silence.

ADR 0006 makes macro F1 the number module B is judged on, so the arithmetic
behind it is worth pinning before there is a model to point at it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autune_contracts.enums import UtteranceKind
from autune_extraction.eval import (
    EvalSetError,
    action_item_f1,
    load_eval_set,
    load_predictions,
    score,
)
from autune_extraction.eval.__main__ import format_report
from autune_extraction.eval.dataset import fingerprint

K = UtteranceKind
ALL_FIVE = [K.COMMITMENT, K.DECISION, K.OPEN_QUESTION, K.CONCERN, K.AMBIGUOUS]


def test_a_perfect_run_scores_one() -> None:
    report = score(ALL_FIVE, list(ALL_FIVE))
    assert report.macro_f1 == 1.0
    assert report.accuracy == 1.0


def test_macro_averages_over_all_five_classes_not_over_the_ones_present() -> None:
    """The reason the metric is macro at all.

    Every commitment right, every decision wrong, three classes never seen. A
    micro average would call this 0.5; macro divides by five because the classes
    the model never predicts are exactly the ones this module needs.
    """
    gold = [K.COMMITMENT, K.COMMITMENT, K.DECISION, K.DECISION]
    predicted = [K.COMMITMENT, K.COMMITMENT, K.COMMITMENT, K.COMMITMENT]
    report = score(gold, predicted)

    assert report.accuracy == 0.5
    assert report.by_kind(K.DECISION).f1 == 0.0
    # commitment: P = 2/4, R = 2/2 -> F1 = 2/3. Everything else 0, over five.
    assert report.macro_f1 == pytest.approx((2 / 3) / 5)


def test_a_rare_class_weighs_as_much_as_a_common_one() -> None:
    """99 commitments right and the single concern wrong is not a good model.

    Accuracy says 0.99. Macro F1 says otherwise, which is the property that made
    it the metric: concern and ambiguous feed the NLI confirmation step.
    """
    gold = [K.COMMITMENT] * 99 + [K.CONCERN]
    predicted = [K.COMMITMENT] * 100
    report = score(gold, predicted)

    assert report.accuracy == pytest.approx(0.99)
    assert report.macro_f1 < 0.25


def test_a_class_in_neither_gold_nor_output_is_reported_not_hidden() -> None:
    """Its F1 is 0.0 and that zero is about the eval set, not the model."""
    gold = [K.COMMITMENT, K.DECISION]
    report = score(gold, list(gold))

    assert set(report.absent_kinds) == {K.OPEN_QUESTION, K.CONCERN, K.AMBIGUOUS}
    assert report.macro_f1 == pytest.approx(2 / 5)
    assert not report.by_kind(K.COMMITMENT).is_absent


def test_a_class_predicted_but_never_correct_is_not_reported_absent() -> None:
    """Scores 0.0 like an absent class and means the opposite."""
    gold = [K.COMMITMENT, K.COMMITMENT]
    predicted = [K.CONCERN, K.CONCERN]
    report = score(gold, predicted)

    assert report.by_kind(K.CONCERN).f1 == 0.0
    assert K.CONCERN not in report.absent_kinds


def test_mismatched_lengths_raise_rather_than_score() -> None:
    with pytest.raises(ValueError, match="2 labels"):
        score([K.COMMITMENT, K.DECISION], [K.COMMITMENT])


def test_an_empty_evaluation_set_raises() -> None:
    """Reporting 0.0 for an empty run would look like a failed model."""
    with pytest.raises(ValueError, match="empty"):
        score([], [])


def test_action_item_f1_is_the_commitment_class() -> None:
    """ADR 0006 derives it rather than measuring it separately."""
    gold = [K.COMMITMENT, K.COMMITMENT, K.DECISION]
    predicted = [K.COMMITMENT, K.DECISION, K.DECISION]
    report = score(gold, predicted)

    assert action_item_f1(report) == report.by_kind(K.COMMITMENT).f1
    assert action_item_f1(report) == pytest.approx(2 / 3)


def _write_jsonl(path: Path, rows: list[dict[str, str]]) -> Path:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return path


def test_predictions_are_matched_by_utterance_id_not_by_line_order(tmp_path: Path) -> None:
    """A run written in a different order would otherwise score as noise."""
    eval_set = _write_jsonl(
        tmp_path / "eval.jsonl",
        [
            {"utterance_id": "utt_1", "kind": "commitment", "text": "제가 하겠습니다"},
            {"utterance_id": "utt_2", "kind": "concern", "text": "그건 좀 어렵지 않을까요"},
        ],
    )
    predictions = _write_jsonl(
        tmp_path / "run.jsonl",
        [
            {"utterance_id": "utt_2", "kind": "concern"},
            {"utterance_id": "utt_1", "kind": "commitment"},
        ],
    )

    loaded = load_eval_set(eval_set)
    scored = score(loaded.labels, load_predictions(predictions, loaded))
    assert scored.macro_f1 == pytest.approx(2 / 5)


def test_predictions_missing_an_utterance_raise(tmp_path: Path) -> None:
    """Silently scoring a partial run would report a number for a different set."""
    eval_set = _write_jsonl(
        tmp_path / "eval.jsonl",
        [
            {"utterance_id": "utt_1", "kind": "commitment", "text": "제가 하겠습니다"},
            {"utterance_id": "utt_2", "kind": "concern", "text": "그건 좀 어렵지 않을까요"},
        ],
    )
    predictions = _write_jsonl(
        tmp_path / "run.jsonl", [{"utterance_id": "utt_1", "kind": "commitment"}]
    )

    loaded = load_eval_set(eval_set)
    with pytest.raises(EvalSetError, match="missing 1 of 2"):
        load_predictions(predictions, loaded)


def test_a_missing_evaluation_set_says_why_it_is_not_in_the_repository(tmp_path: Path) -> None:
    with pytest.raises(EvalSetError, match="not in the repository by design"):
        load_eval_set(tmp_path / "absent.jsonl")


def test_the_fingerprint_changes_when_the_evaluation_set_changes(tmp_path: Path) -> None:
    """testing.md: a metric that moves because the eval set changed is not a metric."""
    path = _write_jsonl(
        tmp_path / "eval.jsonl",
        [{"utterance_id": "utt_1", "kind": "commitment", "text": "제가 하겠습니다"}],
    )
    before = fingerprint(path)

    _write_jsonl(
        path,
        [
            {"utterance_id": "utt_1", "kind": "commitment", "text": "제가 하겠습니다"},
            {"utterance_id": "utt_2", "kind": "concern", "text": "그건 좀 어렵지 않을까요"},
        ],
    )
    assert fingerprint(path) != before


def test_a_malformed_line_is_reported_without_quoting_the_meeting(tmp_path: Path) -> None:
    """Invariant 11 through the back door: an exception message is a log line.

    The unmasked utterance must not reach one, and a loader that echoes the
    offending row into the traceback is how that happens by accident.
    """
    secret = "김민경 주민번호는 900101-1234567 입니다"
    path = tmp_path / "eval.jsonl"
    path.write_text(
        json.dumps({"utterance_id": "utt_1", "kind": "not_a_kind", "text": secret}),
        encoding="utf-8",
    )

    with pytest.raises(EvalSetError) as caught:
        load_eval_set(path)

    assert secret not in str(caught.value)
    assert "900101" not in str(caught.value)
    assert "line 1" in str(caught.value)


def test_the_printed_report_quotes_no_utterance_text(tmp_path: Path) -> None:
    """The report is the thing a person pastes into Slack."""
    secret = "다음 주까지 박재경 님이 계약서 검토합니다"
    eval_set = _write_jsonl(
        tmp_path / "eval.jsonl",
        [{"utterance_id": "utt_1", "kind": "commitment", "text": secret}],
    )
    predictions = _write_jsonl(
        tmp_path / "run.jsonl", [{"utterance_id": "utt_1", "kind": "commitment"}]
    )

    loaded = load_eval_set(eval_set)
    scored = score(loaded.labels, load_predictions(predictions, loaded))
    rendered = format_report(scored, loaded.fingerprint)

    assert secret not in rendered
    assert "박재경" not in rendered
    assert "macro F1" in rendered


def test_the_report_prints_the_reference_figure_beside_ours() -> None:
    """ADR 0006: our number is not readable by someone who does not know the task."""
    report = score(ALL_FIVE, list(ALL_FIVE))
    rendered = format_report(report, "abc123456789")

    assert "action item F1       1.0000" in rendered
    assert "0.4312" in rendered
    assert "ICASSP 2023" in rendered


def test_the_report_survives_a_korean_windows_console() -> None:
    """The team runs Korean Windows, where the console encoding is cp949.

    An em dash in the report raised UnicodeEncodeError there and killed the run
    after the scoring had already succeeded. Keeping the output ASCII is the
    fix; this is the guard against typography creeping back in.
    """
    rendered = format_report(score(ALL_FIVE, list(ALL_FIVE)), "abc123456789")

    rendered.encode("cp949")
    assert rendered.isascii()
