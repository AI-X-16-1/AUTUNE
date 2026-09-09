"""NLI implementations. Selected by ``AUTUNE_CONTEXT_NLI_IMPL``.

- ``klue_kornli_http``  — the default: klue/roberta fine-tuned on KorNLI
  (in-house checkpoint) behind our inference server.
- ``klue_kornli_local`` — in-process transformers (extra: ``local-models``).
- ``fake`` — keyword heuristic, for unit tests.

Used for decision-change detection: entailment -> unchanged,
contradiction -> reversed, neutral -> modified.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from autune_context.pipeline.base import NliScores

if TYPE_CHECKING:
    from autune_context.config import ContextSettings

_LABELS = ("entailment", "contradiction", "neutral")


def _argmax_label(scores: NliScores) -> str:
    return max(_LABELS, key=lambda name: getattr(scores, name))


class KlueKorNliHttp:
    """Expects ``POST {endpoint}/nli {"pairs": [[premise, hypothesis]]}`` ->
    ``{"results": [{"entailment": f, "contradiction": f, "neutral": f}]}``.
    """

    def __init__(self, settings: ContextSettings) -> None:
        self._client = httpx.Client(base_url=settings.nli_endpoint, timeout=settings.nli_timeout_s)
        self._model_version = self._read_version(fallback="klue-roberta-kornli")

    def _read_version(self, fallback: str) -> str:
        try:
            resp = self._client.get("/info")
            resp.raise_for_status()
            return str(resp.json()["model_version"])
        except (httpx.HTTPError, KeyError, ValueError):
            return fallback

    @property
    def model_version(self) -> str:
        return self._model_version

    def classify(self, pairs: list[tuple[str, str]]) -> list[NliScores]:
        if not pairs:
            return []
        resp = self._client.post("/nli", json={"pairs": [list(p) for p in pairs]})
        resp.raise_for_status()
        out: list[NliScores] = []
        for row in resp.json()["results"]:
            partial = NliScores(
                label="",
                entailment=float(row["entailment"]),
                contradiction=float(row["contradiction"]),
                neutral=float(row["neutral"]),
            )
            out.append(_with_label(partial))
        return out


class KlueKorNliLocal:
    """In-process NLI. Needs the ``local-models`` optional dependency group."""

    def __init__(self, settings: ContextSettings) -> None:
        try:
            from transformers import pipeline as hf_pipeline
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "klue_kornli_local needs the 'local-models' extra: "
                "uv sync --package autune-context --extra local-models"
            ) from exc
        if not settings.nli_local_model:
            raise RuntimeError("set AUTUNE_CONTEXT_NLI_LOCAL_MODEL to the in-house checkpoint")
        self._pipe = hf_pipeline("text-classification", model=settings.nli_local_model, top_k=None)
        self._model_version = settings.nli_local_model

    @property
    def model_version(self) -> str:
        return self._model_version

    def classify(self, pairs: list[tuple[str, str]]) -> list[NliScores]:
        out: list[NliScores] = []
        for premise, hypothesis in pairs:
            scored = {
                d["label"].lower(): float(d["score"])
                for d in self._pipe({"text": premise, "text_pair": hypothesis})[0]
            }
            out.append(
                _with_label(
                    NliScores(
                        label="",
                        entailment=scored.get("entailment", 0.0),
                        contradiction=scored.get("contradiction", 0.0),
                        neutral=scored.get("neutral", 0.0),
                    )
                )
            )
        return out


class FakeNli:
    """Marks a pair ``contradiction`` when one side negates ('안', '못', '아니'),
    ``entailment`` when the two strings are equal, else ``neutral``.
    """

    def __init__(self, settings: ContextSettings | None = None) -> None:
        self._model_version = "fake-nli-v1"

    @property
    def model_version(self) -> str:
        return self._model_version

    def classify(self, pairs: list[tuple[str, str]]) -> list[NliScores]:
        out: list[NliScores] = []
        for premise, hypothesis in pairs:
            if premise.strip() == hypothesis.strip():
                out.append(NliScores("entailment", 1.0, 0.0, 0.0))
            elif _negates(premise) != _negates(hypothesis):
                out.append(NliScores("contradiction", 0.0, 1.0, 0.0))
            else:
                out.append(NliScores("neutral", 0.0, 0.0, 1.0))
        return out


def _with_label(scores: NliScores) -> NliScores:
    return NliScores(
        label=_argmax_label(scores),
        entailment=scores.entailment,
        contradiction=scores.contradiction,
        neutral=scores.neutral,
    )


def _negates(text: str) -> bool:
    return any(token in text for token in ("안 ", "못 ", "아니", "취소", "철회"))
