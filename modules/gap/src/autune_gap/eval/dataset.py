"""Loads the hand-labeled gap-detection evaluation set.

Kept as data, not code, next to ``gap_detection_v1.json`` so a change to the
labels shows up as a data diff rather than a Python diff. Bump the filename's
version suffix on a change that shifts what counts as correct — a metric that
moves because the set changed underneath it is not a metric
(docs/engineering/testing.md, "Evaluation"). Fixing a typo in a Korean sentence
without changing which items the meeting settled does not need a bump.

**The set is closed-world.** Every item of a case's template is either in
``real_gaps`` or in ``settled``, and the loader refuses a case where it is not.
Without that rule "raised and not in ``real_gaps``" cannot be read as a false
positive — it might be an item nobody got round to labeling — and the precision
figure would be measuring the labeler's diligence.

Mirrors module D's ``eval/*/dataset.py`` and ``autune_contracts.fixtures``'s
``importlib.resources`` pattern.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from autune_core.errors import ValidationError
from autune_gap.template import get_template

_DATA_DIR = files(__package__) / "fixtures"
DEFAULT_DATASET = "gap_detection_v1.json"


class EvalSetError(Exception):
    """The evaluation set is not usable. Names the case and the field, never a
    line of its transcript."""


@dataclass(frozen=True)
class EvalLine:
    speaker: str
    text: str
    """Authored Korean meeting speech, already in the shape module A would hand
    over: masked, one utterance per line. No real meeting content is committed
    here — see the dataset's own ``notes``."""


@dataclass(frozen=True)
class EvalCase:
    id: str
    template_key: str
    lines: tuple[EvalLine, ...]
    real_gaps: frozenset[str]
    """Template item keys this meeting genuinely left unsettled. Empty means the
    meeting settled everything, so any gap raised on it is a false positive —
    the most direct case a precision metric can have."""

    settled: frozenset[str]
    """The rest of the template, stated rather than inferred, so the closed-world
    check has something to verify against."""

    evidence: dict[str, tuple[str, ...]]
    """Settled item key -> the nouns a reader would point at as settling it.

    **An empty tuple is a claim, not a missing label.** It says the meeting
    settled the item without saying any noun that could name it — "이건우님이
    맡고 다음 주 금요일까지" settles ownership with a verb and a date, and there
    is no noun in it for a keyword list to match. That distinction is what
    separates a false positive a template edit could fix from one it could not,
    so the labeler has to decide it rather than leave the field out.

    Labeled from the transcript, never from what the pipeline produced. Reading
    the graph first and writing down what is in it turns the diagnosis into a
    description of current behaviour.
    """

    @property
    def speakers(self) -> list[str]:
        return list(dict.fromkeys(line.speaker for line in self.lines))


def load_cases(name: str = DEFAULT_DATASET) -> list[EvalCase]:
    raw = json.loads((_DATA_DIR / name).read_text(encoding="utf-8"))
    if raw.get("version") != 1:
        raise EvalSetError(f"{name}: unknown eval set version {raw.get('version')!r}")

    cases = [_parse_case(case, name) for case in raw["cases"]]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise EvalSetError(f"{name}: duplicate case ids")
    return cases


def _parse_case(raw: dict[str, Any], name: str) -> EvalCase:
    case_id = raw["id"]
    template_key = raw["template"]
    real = frozenset(raw.get("real_gaps", ()))
    settled = frozenset(raw.get("settled", ()))

    try:
        template = get_template(template_key)
    except ValidationError as exc:
        # Reraised as this module's error so `__main__` reports it as a bad
        # evaluation set and exits 2, rather than showing a traceback for what
        # is a typo in a JSON field.
        raise EvalSetError(f"{name}: case {case_id!r} names {exc.message}") from exc

    known = {item.key for item in template.items}

    unknown = sorted((real | settled) - known)
    if unknown:
        raise EvalSetError(
            f"{name}: case {case_id!r} labels items template {template_key!r} does not "
            f"define: {unknown}"
        )

    both = sorted(real & settled)
    if both:
        raise EvalSetError(f"{name}: case {case_id!r} labels items twice: {both}")

    unlabeled = sorted(known - real - settled)
    if unlabeled:
        raise EvalSetError(
            f"{name}: case {case_id!r} leaves items unlabeled: {unlabeled}. Every item of "
            "the template has to be one or the other, or a gap raised on it cannot be read "
            "as right or wrong"
        )

    evidence = {key: tuple(terms) for key, terms in raw.get("evidence", {}).items()}

    stray = sorted(set(evidence) - settled)
    if stray:
        raise EvalSetError(
            f"{name}: case {case_id!r} carries evidence for items it did not call settled: "
            f"{stray}. Evidence explains why a gap raised on a settled item is wrong; for an "
            "item the meeting really did leave open there is nothing to explain"
        )

    if evidence:
        missing_evidence = sorted(settled - set(evidence))
        if missing_evidence:
            raise EvalSetError(
                f"{name}: case {case_id!r} labels evidence for some settled items and not "
                f"{missing_evidence}. Partial evidence would classify some false positives and "
                "silently leave the rest out of the split"
            )

    return EvalCase(
        id=case_id,
        template_key=template_key,
        lines=tuple(EvalLine(speaker=line["speaker"], text=line["text"]) for line in raw["lines"]),
        real_gaps=real,
        settled=settled,
        evidence=evidence,
    )
