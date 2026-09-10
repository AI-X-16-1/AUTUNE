"""The fine-tuning run itself.

Everything that needs ``transformers`` is here, and nothing else is. The imports
are inside the function so that ``autune_extraction.training`` stays importable
-- and its arithmetic stays testable -- on a machine without the ``training``
extra, which is every CI runner.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from .dataset import LABELS, TrainExample, class_weights, label_counts, read_split

log = logging.getLogger(__name__)

BASE_CHECKPOINT = "kakaobank/kf-deberta-base"
"""The encoder to fine-tune. MIT licensed, so commercial use is settled.

Chosen on inference cost, measured rather than assumed (#112). Both candidates
are 12 layers / 768 hidden / 12 heads, so the difference was not capacity:

    kakaobank/kf-deberta-base     forward 1.63s   ->  2.0 min per meeting
    microsoft/mdeberta-v3-base    forward 29.9s   -> 37.4 min per meeting

A 45-minute meeting is about 2,400 utterances and module B classifies every one
of them, on every meeting. mdeberta spends most of a meeting's own length
classifying it, which ends the comparison before accuracy enters. Why an
identical architecture is 18x slower is a real question and not one this
decision needs answering.

Overridable so the comparison can be re-run against a new encoder without a code
change. Whichever is used is recorded in ``run.json`` beside the checkpoint.
"""

MAX_LENGTH = 96
"""Tokens per utterance. The AMI act-length distribution puts 99% under this, and
padding every batch to 512 would spend most of the forward pass on padding."""


@dataclass(frozen=True)
class RunConfig:
    """One training run, recorded in full beside the checkpoint it produced."""

    base_checkpoint: str = BASE_CHECKPOINT
    epochs: int = 3
    batch_size: int = 16
    learning_rate: float = 2e-5
    max_length: int = MAX_LENGTH
    seed: int = 20260910
    weighted_loss: bool = True
    """Inverse-frequency class weights -- see ``dataset.class_weights``. The rare
    classes are the ones the product is for."""


def fingerprint(path: Path) -> str:
    """First 12 hex characters of the SHA-256 of a split file.

    Recorded so a checkpoint can be tied back to the dataset that produced it.
    "Trained on AMI" is not enough: the label mapping changed three times while
    it was being written, and every change moved the numbers.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def train(data: Path, out: Path, config: RunConfig | None = None) -> Path:
    """Fine-tune and write a checkpoint directory. Returns the directory."""
    import numpy as np
    import torch
    from torch import nn
    from transformers import (
        AutoModelForSequenceClassification,
        AutoTokenizer,
        Trainer,
        TrainingArguments,
        set_seed,
    )

    config = config or RunConfig()
    set_seed(config.seed)

    splits = {name: read_split(data, name) for name in ("train", "validation")}
    for name, examples in splits.items():
        counts = label_counts(examples)
        log.info("split loaded: %s, %d examples, %s", name, len(examples), dict(counts))
        missing = [label for label in LABELS if counts[label] == 0]
        if missing:
            raise SystemExit(
                f"{name} split has no examples of {missing}. A class that is absent "
                "cannot be learned, and a run that proceeds anyway produces a "
                "checkpoint that silently never predicts it."
            )

    tokenizer = AutoTokenizer.from_pretrained(config.base_checkpoint)
    model = AutoModelForSequenceClassification.from_pretrained(
        config.base_checkpoint,
        num_labels=len(LABELS),
        id2label=dict(enumerate(LABELS)),
        label2id={label: index for index, label in enumerate(LABELS)},
    )

    def encode(examples: list[TrainExample]) -> list[dict[str, object]]:
        encoded = tokenizer(
            [example.text for example in examples],
            truncation=True,
            max_length=config.max_length,
        )
        return [
            {**{key: encoded[key][i] for key in encoded}, "labels": example.label_id}
            for i, example in enumerate(examples)
        ]

    weights = torch.tensor(class_weights(splits["train"]), dtype=torch.float)

    class WeightedTrainer(Trainer):
        """Cross-entropy with the class weights instead of the flat default.

        Subclassing is how transformers exposes the loss; there is no argument
        for it. Everything else here is the stock Trainer.
        """

        def compute_loss(self, model, inputs, return_outputs=False, **kwargs):  # noqa: ANN001, ANN201
            labels = inputs.pop("labels")
            outputs = model(**inputs)
            loss_fn = nn.CrossEntropyLoss(weight=weights.to(outputs.logits.device))
            loss = loss_fn(outputs.logits, labels)
            return (loss, outputs) if return_outputs else loss

    def macro_f1(prediction) -> dict[str, float]:  # noqa: ANN001
        """Macro F1 -- the unweighted mean over the five classes.

        Micro would let the common classes carry the score, which is the failure
        this whole file is arranged against.
        """
        predicted = np.argmax(prediction.predictions, axis=1)
        actual = prediction.label_ids
        scores: list[float] = []
        for index in range(len(LABELS)):
            tp = int(((predicted == index) & (actual == index)).sum())
            fp = int(((predicted == index) & (actual != index)).sum())
            fn = int(((predicted != index) & (actual == index)).sum())
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            harmonic = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
            scores.append(harmonic)
        report = {f"f1_{label}": score for label, score in zip(LABELS, scores, strict=True)}
        report["macro_f1"] = sum(scores) / len(scores)
        return report

    trainer_class = WeightedTrainer if config.weighted_loss else Trainer
    trainer = trainer_class(
        model=model,
        args=TrainingArguments(
            output_dir=str(out / "checkpoints"),
            num_train_epochs=config.epochs,
            per_device_train_batch_size=config.batch_size,
            per_device_eval_batch_size=config.batch_size * 2,
            learning_rate=config.learning_rate,
            eval_strategy="epoch",
            save_strategy="epoch",
            load_best_model_at_end=True,
            metric_for_best_model="macro_f1",
            seed=config.seed,
            report_to=[],
        ),
        train_dataset=encode(splits["train"]),
        eval_dataset=encode(splits["validation"]),
        compute_metrics=macro_f1,
    )
    trainer.train()
    metrics = trainer.evaluate()

    out.mkdir(parents=True, exist_ok=True)
    trainer.save_model(str(out))
    tokenizer.save_pretrained(str(out))
    (out / "run.json").write_text(
        json.dumps(
            {
                "config": asdict(config),
                "labels": list(LABELS),
                "splits": {
                    name: {
                        "examples": len(examples),
                        "counts": dict(label_counts(examples)),
                        "fingerprint": fingerprint(data / f"{name}.jsonl"),
                    }
                    for name, examples in splits.items()
                },
                "metrics": {
                    key: value for key, value in metrics.items() if isinstance(value, (int, float))
                },
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return out
