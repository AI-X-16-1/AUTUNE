"""Loading the held-out evaluation set and a model's predictions over it.

The evaluation set is drawn from real meetings, so it is never committed. ADR
0003 keeps it out of the repository the same way it keeps the training corpora
out: ``dataset/`` is gitignored and the path is configuration.

``docs/engineering/testing.md`` asks for the set to be versioned, on the grounds
that "a metric that moves because the eval set changed is not a metric". The
loader answers that with a fingerprint over the file bytes, which the harness
prints beside every score.

Utterance text is read from the file and never kept. A score line that quotes
the meeting it scored is the unmasked-text leak invariant 11 forbids, arriving
through the back door; not holding the text at all is what closes that door
rather than watching it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from autune_contracts.enums import UtteranceKind


class EvalSetError(RuntimeError):
    """The evaluation set is missing or malformed. Never carries utterance text."""


@dataclass(frozen=True)
class EvalExample:
    """One labelled utterance -- its id and its kind, and deliberately not its text.

    The evaluation set file carries the utterance so a person can read it, and the
    loader checks the field is present. It is not kept: scoring never reads it, and
    a field that is never read is meeting content held in memory for no reason,
    printed by any dataclass repr that reaches a traceback. Not holding it makes
    invariant 11 structural here rather than a promise the tests watch.
    """

    utterance_id: str
    kind: UtteranceKind


@dataclass(frozen=True)
class EvalSet:
    examples: tuple[EvalExample, ...]
    fingerprint: str
    path: Path

    @property
    def labels(self) -> list[UtteranceKind]:
        return [e.kind for e in self.examples]

    def __len__(self) -> int:
        return len(self.examples)


def fingerprint(path: Path) -> str:
    """First 12 hex characters of the SHA-256 of the file, enough to spot a change."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def load_eval_set(path: Path) -> EvalSet:
    """Read a JSONL evaluation set: one object per line with ``utterance_id``,
    ``kind``, and ``text``.

    Raises rather than returning an empty set. A harness that reports 0.0 because
    it found no data is worse than one that stops.
    """
    if not path.exists():
        raise EvalSetError(
            f"no evaluation set at {path}. It is not in the repository by design - "
            "see docs/modules/extraction.md, 'Metric'."
        )

    examples: list[EvalExample] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            row["text"]  # required by the format; read to validate, never kept
            example = EvalExample(utterance_id=row["utterance_id"], kind=UtteranceKind(row["kind"]))
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            # Line number and reason only. The line itself is meeting text.
            raise EvalSetError(f"{path} line {number}: {type(exc).__name__}") from exc

        if example.utterance_id in seen:
            raise EvalSetError(f"{path} line {number}: duplicate utterance_id")
        seen.add(example.utterance_id)
        examples.append(example)

    if not examples:
        raise EvalSetError(f"{path} has no examples")
    return EvalSet(examples=tuple(examples), fingerprint=fingerprint(path), path=path)


def load_predictions(path: Path, eval_set: EvalSet) -> list[UtteranceKind]:
    """Read predictions as JSONL of ``utterance_id`` and ``kind``, ordered to
    match the evaluation set.

    Matching by id rather than by position: a predictions file written in a
    different order would otherwise score as noise and look like a bad model.
    """
    if not path.exists():
        raise EvalSetError(f"no predictions at {path}")

    predicted: dict[str, UtteranceKind] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            utterance_id, kind = row["utterance_id"], UtteranceKind(row["kind"])
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise EvalSetError(f"{path} line {number}: {type(exc).__name__}") from exc

        # Without this the last line silently wins, and two runs concatenated or
        # one resumed after a stop would score on whichever half came second --
        # with no warning, because every other malformed shape here does raise.
        if utterance_id in predicted:
            raise EvalSetError(f"{path} line {number}: duplicate utterance_id")
        predicted[utterance_id] = kind

    missing = [e.utterance_id for e in eval_set.examples if e.utterance_id not in predicted]
    if missing:
        raise EvalSetError(
            f"{path} is missing {len(missing)} of {len(eval_set)} utterances (first: {missing[0]})"
        )
    return [predicted[e.utterance_id] for e in eval_set.examples]
