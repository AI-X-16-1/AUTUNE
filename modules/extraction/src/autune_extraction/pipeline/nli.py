"""NLI implementations. Selected by ``AUTUNE_EXTRACTION_NLI_IMPL``.

- ``local``  -- default: in-process transformers (extra: ``local-models``).
- ``hosted`` -- our own inference server.
- ``fake``   -- deterministic, for tests.

klue/roberta fine-tuned on KorNLI by default (#172: server-only deployment,
weights never distributed, dev acc 0.8185 / held-out XNLI test acc 0.8273).
No ``external`` option, for the same reason ``classifier.py`` has none --
sending an utterance to somebody else's NLI endpoint is a privacy decision,
not a config string (privacy.md section 6, ``base.py``).

``torch`` and ``transformers`` are imported inside the class that needs
them -- see ``classifier.py``'s own note on why.
"""

from __future__ import annotations

from typing import Any

from autune_core import get_logger

from .base import NliScores

log = get_logger(__name__)


def _scores(entailment: float, contradiction: float, neutral: float) -> NliScores:
    ranked = {"entailment": entailment, "contradiction": contradiction, "neutral": neutral}
    return NliScores(
        label=max(ranked, key=lambda name: ranked[name]),
        entailment=entailment,
        contradiction=contradiction,
        neutral=neutral,
    )


def _nli_client(endpoint: str) -> Any:
    """Our inference server, as a client the way every other one is written --
    see ``classifier._classifier_client``. A subclass rather than a reassigned
    instance for the same reason: ``addressing`` can only be declared on a
    class, and every string here is an utterance -- nothing addresses the
    request, so declaring nothing is what fails closed.
    """
    from autune_integrations.base import HttpClient  # noqa: PLC0415

    class NliClient(HttpClient):
        service = "extraction-nli"

    return NliClient(endpoint)


class LocalNli:
    """In-process NLI. Needs the ``local-models`` optional dependency group.

    Loaded once per process by the registry, not per task -- the checkpoint is
    hundreds of megabytes, like ``LocalDeberta``.
    """

    def __init__(self, checkpoint: str, *, device: str = "cpu") -> None:
        if not checkpoint:
            raise ValueError("LocalNli needs a checkpoint")
        self._checkpoint = checkpoint
        self._device = device
        self._pipe: Any = None
        self._needs_segment_ids = False

    @property
    def model_version(self) -> str:
        return self._checkpoint

    def _load(self) -> None:
        if self._pipe is not None:
            return
        try:
            import torch  # noqa: PLC0415
            from transformers import pipeline as hf_pipeline  # noqa: PLC0415
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "the local NLI model needs the 'local-models' extra: "
                "uv sync --package autune-extraction --extra local-models"
            ) from exc

        if self._device == "cuda" and not torch.cuda.is_available():
            # Said here rather than left to a CUDA error inside the first
            # premise/hypothesis call -- see ``LocalDeberta._load``.
            raise RuntimeError(
                "AUTUNE_EXTRACTION_NLI_DEVICE=cuda but torch reports no CUDA "
                f"device (torch {torch.__version__}). See "
                "docs/engineering/environments.md."
            )

        self._pipe = hf_pipeline(
            "text-classification", model=self._checkpoint, device=self._device, top_k=None
        )
        # klue/roberta-base ships a BertTokenizer (2-segment 0/1 token_type_ids)
        # on top of a RoBERTa encoder (type_vocab_size=1): id=1 is out of range
        # for token_type_embeddings and crashes -- an IndexError on CPU, an
        # unrecoverable CUDA device-side assert on GPU. RoBERTa's own tokenizer
        # never emits token_type_ids for exactly this reason -- module D hit
        # this first (#322, same checkpoint family, same gap still open there:
        # its own fix is unconditional too). Read from the loaded model's own
        # config rather than assumed from the checkpoint name, so a real BERT
        # checkpoint (type_vocab_size=2) pointed at AUTUNE_EXTRACTION_NLI_LOCAL_MODEL
        # keeps its segment ids instead of silently merging premise and
        # hypothesis into one -- no crash, no error, just a wrong score no
        # test or CI would catch.
        self._needs_segment_ids = self._pipe.model.config.type_vocab_size > 1
        log.info("extraction_nli_loaded", checkpoint=self._checkpoint, device=self._device)

    def classify(self, pairs: list[tuple[str, str]]) -> list[NliScores]:
        if not pairs:
            return []
        self._load()
        out: list[NliScores] = []
        for premise, hypothesis in pairs:
            records = self._pipe(
                {"text": premise, "text_pair": hypothesis},
                return_token_type_ids=self._needs_segment_ids,
            )[0]
            scored = {r["label"].lower(): float(r["score"]) for r in records}
            out.append(
                _scores(
                    scored.get("entailment", 0.0),
                    scored.get("contradiction", 0.0),
                    scored.get("neutral", 0.0),
                )
            )
        return out


class HostedNli:
    """The same model on our own inference server.

    Expects ``POST {endpoint}/nli {"pairs": [[premise, hypothesis], ...]}`` ->
    ``{"results": [{"entailment": f, "contradiction": f, "neutral": f}, ...]}``,
    the same shape module D's own hosted client uses -- a real server behind
    this would answer both modules the same way, even though each module's
    client is its own copy (invariant 2).
    """

    def __init__(self, endpoint: str, model_version: str) -> None:
        self._client = _nli_client(endpoint)
        self._model_version = model_version

    @property
    def model_version(self) -> str:
        return self._model_version

    def classify(self, pairs: list[tuple[str, str]]) -> list[NliScores]:
        if not pairs:
            return []
        body = self._client.request("POST", "/nli", json={"pairs": [list(p) for p in pairs]})
        rows = body.get("results", []) if isinstance(body, dict) else body
        if not isinstance(rows, list) or len(rows) != len(pairs):
            raise ValueError(
                f"asked for {len(pairs)} NLI results, got {type(rows).__name__} "
                f"of length {len(rows) if isinstance(rows, list) else 'n/a'}"
            )
        return [
            _scores(float(row["entailment"]), float(row["contradiction"]), float(row["neutral"]))
            for row in rows
        ]


class FakeNli:
    """Deterministic, no weights, no network. What the tests run.

    Marks a pair ``entailment`` when the premise carries a real commitment
    marker (the same endings ``FakeClassifier`` keys ``commitment`` on),
    ``contradiction`` when it is negated, else ``neutral`` -- weak assent
    stays weak, which is the answer #12 needs a fake to give by default. Not
    an approximation of the model's accuracy; it exists so ``verify_
    utterances`` and everything after it can be built and tested before a
    checkpoint is loaded.
    """

    model_version = "fake"

    _COMMITS: tuple[str, ...] = ("겠습니다", "할게요", "하겠", "맡을게요", "맡겠습니다")
    _NEGATES: tuple[str, ...] = ("안 ", "못 ", "아니", "취소", "철회")

    def classify(self, pairs: list[tuple[str, str]]) -> list[NliScores]:
        out: list[NliScores] = []
        for premise, _hypothesis in pairs:
            if any(token in premise for token in self._NEGATES):
                out.append(_scores(0.0, 1.0, 0.0))
            elif any(token in premise for token in self._COMMITS):
                out.append(_scores(1.0, 0.0, 0.0))
            else:
                out.append(_scores(0.0, 0.0, 1.0))
        return out
