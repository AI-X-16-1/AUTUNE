"""NLI implementations. Selected by ``AUTUNE_CONTEXT_NLI_IMPL``.

- ``klue_kornli_http``  — the default: klue/roberta fine-tuned on KorNLI
  (in-house checkpoint) behind our inference server.
- ``klue_kornli_local`` — in-process transformers (extra: ``local-models``).
- ``fake`` — keyword heuristic, for unit tests.

Used for decision-change detection: entailment -> unchanged,
contradiction -> reversed, neutral -> modified.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from autune_context.pipeline._serving import probe
from autune_context.pipeline.base import NliScores

if TYPE_CHECKING:
    from autune_context.config import ContextSettings

_LABELS = ("entailment", "contradiction", "neutral")


def _argmax_label(scores: NliScores) -> str:
    return max(_LABELS, key=lambda name: getattr(scores, name))


class KlueKorNliHttp:
    """Expects ``POST {endpoint}/nli {"pairs": [[premise, hypothesis]]}`` ->
    ``{"results": [{"entailment": f, "contradiction": f, "neutral": f}]}``;
    ``GET {endpoint}/info`` -> ``{"model_version"}``.
    """

    def __init__(self, settings: ContextSettings) -> None:
        self._client = httpx.Client(base_url=settings.nli_endpoint, timeout=settings.nli_timeout_s)
        info = probe(self._client, service="nli")
        self._model_version = str(info.get("model_version", "klue-roberta-kornli"))

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
        self._pipe: Any = hf_pipeline(
            "text-classification", model=settings.nli_local_model, top_k=None
        )
        self._model_version = settings.nli_local_model

    @property
    def model_version(self) -> str:
        return self._model_version

    def classify(self, pairs: list[tuple[str, str]]) -> list[NliScores]:
        out: list[NliScores] = []
        for premise, hypothesis in pairs:
            # klue/roberta-base ships a BertTokenizer (2-segment 0/1
            # token_type_ids) on top of a RoBERTa encoder (type_vocab_size=1):
            # id=1 is out of range for token_type_embeddings and crashes --
            # an IndexError on CPU, an unrecoverable CUDA device-side assert
            # on GPU. RoBERTa's own tokenizer never emits token_type_ids for
            # exactly this reason; match that instead of trusting the
            # checkpoint's tokenizer_config.json.
            records = self._pipe(
                {"text": premise, "text_pair": hypothesis}, return_token_type_ids=False
            )[0]
            scored = {r["label"].lower(): float(r["score"]) for r in records}
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
