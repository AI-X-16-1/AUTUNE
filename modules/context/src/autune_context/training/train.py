"""Fine-tunes klue/roberta-base on KorNLI, model-selecting on dev.

``train()`` never touches the test split — that is ``evaluate_test()``,
called separately and exactly once, so the held-out number is not spent while
still iterating on epochs, batch size, or learning rate. See ``dataset``'s
docstring for the split, and docs/modules/context.md, "Phased delivery".

The output directory is a standard ``transformers`` checkpoint — point
``AUTUNE_CONTEXT_NLI_LOCAL_MODEL`` at it to use it via
``klue_kornli_local`` (see ``autune_context.pipeline.nli``), or push it behind
the self-hosted ``/nli`` endpoint that ``klue_kornli_http`` expects.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autune_context.training.dataset import LABELS, load_dev, load_test, load_train

if TYPE_CHECKING:
    from transformers import PreTrainedModel, PreTrainedTokenizerBase

_BASE_MODEL = "klue/roberta-base"


@dataclass(frozen=True)
class TrainConfig:
    output_dir: Path
    max_length: int = 128
    num_epochs: float = 3.0
    train_batch_size: int = 32
    eval_batch_size: int = 64
    learning_rate: float = 2e-5


def train(config: TrainConfig) -> dict[str, float]:
    """Fine-tune on KorNLI train, select the best checkpoint on dev, save it
    to ``config.output_dir``, and return the *dev* metrics only."""
    from transformers import DataCollatorWithPadding, Trainer, TrainingArguments

    tokenizer, model = _fresh_model()

    train_ds = _tokenize(load_train(), tokenizer, config.max_length)
    dev_ds = _tokenize(load_dev(), tokenizer, config.max_length)

    args = TrainingArguments(
        output_dir=str(config.output_dir),
        num_train_epochs=config.num_epochs,
        per_device_train_batch_size=config.train_batch_size,
        per_device_eval_batch_size=config.eval_batch_size,
        learning_rate=config.learning_rate,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="accuracy",
        report_to=[],
    )
    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=train_ds,
        eval_dataset=dev_ds,
        data_collator=DataCollatorWithPadding(tokenizer),
        compute_metrics=_compute_accuracy,
    )
    trainer.train()
    dev_metrics = trainer.evaluate()

    trainer.save_model(str(config.output_dir))
    tokenizer.save_pretrained(str(config.output_dir))
    return dev_metrics


def evaluate_test(
    model_dir: Path, *, max_length: int = 128, eval_batch_size: int = 64
) -> dict[str, float]:
    """Score an already-trained checkpoint on the KorNLI test split.

    The one number that goes in the PR description. Call this once, after
    ``train`` has already picked a checkpoint on dev — not as part of the
    training loop.
    """
    from transformers import (
        AutoModelForSequenceClassification,
        AutoTokenizer,
        DataCollatorWithPadding,
        Trainer,
        TrainingArguments,
    )

    tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
    model = AutoModelForSequenceClassification.from_pretrained(str(model_dir))
    test_ds = _tokenize(load_test(), tokenizer, max_length)

    trainer = Trainer(
        model=model,
        args=TrainingArguments(
            output_dir=str(model_dir / "_test_eval_scratch"),
            per_device_eval_batch_size=eval_batch_size,
            report_to=[],
        ),
        data_collator=DataCollatorWithPadding(tokenizer),
        compute_metrics=_compute_accuracy,
    )
    return trainer.evaluate(test_ds)


def _fresh_model() -> tuple[PreTrainedTokenizerBase, PreTrainedModel]:
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(_BASE_MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(
        _BASE_MODEL,
        num_labels=len(LABELS),
        id2label=dict(enumerate(LABELS)),
        label2id={label: i for i, label in enumerate(LABELS)},
    )
    return tokenizer, model


def _tokenize(dataset: Any, tokenizer: PreTrainedTokenizerBase, max_length: int) -> Any:
    return dataset.map(
        lambda batch: tokenizer(
            batch["premise"], batch["hypothesis"], truncation=True, max_length=max_length
        ),
        batched=True,
    )


def _compute_accuracy(eval_pred: Any) -> dict[str, float]:
    import numpy as np

    logits, labels = eval_pred
    predictions = np.argmax(logits, axis=-1)
    return {"accuracy": float((predictions == labels).mean())}
