"""How well explain_metric finds the right passage: recall@1, recall@3, MRR.

    python -m autune_intelligence.retrieval_eval [--dense-model NAME] [--json]

BM25 always runs. Dense and hybrid need the ``local-models`` extra and are
skipped with a note when it is not installed.
"""

from __future__ import annotations

import argparse
import json
from importlib import resources
from typing import Any

from . import glossary, retrieval
from .config import get_settings


def questions() -> list[dict[str, str]]:
    raw = resources.files("autune_intelligence.glossary").joinpath("questions.json")
    return json.loads(raw.read_text(encoding="utf-8"))


def score(ranked: list[list[str]], expected: list[str]) -> dict[str, float]:
    n = len(expected)
    pairs = list(zip(ranked, expected, strict=True))
    r1 = sum(1 for r, e in pairs if r[:1] == [e]) / n
    r3 = sum(1 for r, e in pairs if e in r[:3]) / n
    mrr = sum(1 / (r.index(e) + 1) if e in r else 0.0 for r, e in pairs) / n
    return {"recall@1": r1, "recall@3": r3, "mrr": mrr}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m autune_intelligence.retrieval_eval")
    parser.add_argument("--dense-model", default=get_settings().gap_classifier_backbone)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    qs = questions()
    corpus = glossary.passages()
    expected = [q["expected"] for q in qs]
    rows: dict[str, Any] = {}
    bm25 = retrieval.BM25Retriever(corpus)
    rows["bm25"] = score([bm25.ranking(q["question"]) for q in qs], expected)
    skipped: str | None = None
    try:
        dense = retrieval.DenseRetriever(corpus, args.dense_model)
    except (ImportError, OSError) as exc:
        skipped = (
            "no local-models extra"
            if isinstance(exc, ImportError)
            else f"model unavailable: {type(exc).__name__}"
        )
        rows["dense"] = rows["hybrid"] = None
    else:
        dense_ranked = [dense.ranking(q["question"]) for q in qs]
        rows["dense"] = score(dense_ranked, expected)
        fused = [
            retrieval.rrf([bm25.ranking(q["question"]), d])
            for q, d in zip(qs, dense_ranked, strict=True)
        ]
        rows["hybrid"] = score(fused, expected)
    if args.json:
        payload: dict[str, Any] = {"questions": len(qs), "dense_model": args.dense_model, **rows}
        if skipped:
            payload["skipped"] = skipped
        print(json.dumps(payload))
    else:
        print(f"questions: {len(qs)}  dense model: {args.dense_model}")
        for name, s in rows.items():
            line = (
                f"skipped ({skipped})"
                if s is None
                else "  ".join(f"{k} {v:.2f}" for k, v in s.items())
            )
            print(f"{name:7} {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
