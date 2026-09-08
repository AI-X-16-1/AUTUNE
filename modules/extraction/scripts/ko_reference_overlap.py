"""How often an unresolved reference shares an utterance with a class cue.

Written for the pipeline-order question: whether reference resolution runs before
classification or after it. Order can only matter where a reference and a class
cue meet, so this measures the size of that overlap in Korean meeting transcripts.

Read the output as population size, not as accuracy. Deciding which order
classifies better needs a labelled evaluation set and two trained classifiers;
neither exists yet. What this can say is how much of the corpus is even in scope,
and how much LLM work each order implies.

Both pattern sets below are hand-written approximations, not the classifier:

- Class cues key on Korean sentence endings, which suits `commitment` and
  `open_question` and is rough for `concern` and `ambiguous`.
- The reference list is the demonstratives and elided subjects a resolver would
  have to fill in. It is not exhaustive.

Neither is a substitute for measuring against real labels once #10 has them.
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path
from typing import Final

REFERENCE: Final = re.compile(
    r"(그거|그건|그걸|그게|저거|저건|저걸|이거|이건|이걸|이게|그때|그쪽|"
    r"그 부분|이 부분|저희가|우리가|저희는|우리는|거기|여기|이런|그런)"
)

CUES: Final[dict[str, re.Pattern[str]]] = {
    "commitment": re.compile(
        r"(할게요|하겠습니다|할게|드릴게요|드리겠습니다|하죠|준비하겠|정리하겠|보내드리|올리겠)"
    ),
    "decision": re.compile(r"(하기로|으로 하죠|로 하죠|결정했|확정|가는 걸로|하는 걸로)"),
    "open_question": re.compile(r"(할까요\?|나요\?|인가요\?|어떨까요|어떻게 할|무엇|뭐죠)"),
    "concern": re.compile(r"(우려|걱정|리스크|문제가|어렵|힘들|안 될|위험)"),
    "ambiguous": re.compile(r"(같아요|같습니다|볼게요|봐야|아마|일단|한번)"),
}

RESOLVED_BY_CLASSIFYING_FIRST: Final = ("commitment", "decision", "ambiguous")
"""The classes whose utterances a resolver would still have to visit.

Slot filling needs the first two; the confirmation step needs the third. Anything
else can be skipped when classification runs first, which is where the difference
in LLM calls between the two orders comes from.
"""


def utterances(root: Path) -> list[str]:
    """Masked utterance text from every AI Hub label file under ``root``."""
    out: list[str] = []
    for path in root.rglob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        for item in data.get("utterance", []):
            # `form` is the masked field; `original_form` is not ours to read.
            text = (item.get("form") or "").strip()
            if text:
                out.append(text)
    return out


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: ko_reference_overlap.py <corpus-root>")

    texts = utterances(Path(sys.argv[1]))
    if not texts:
        raise SystemExit("no utterances found; check the corpus path")

    total = len(texts)
    with_reference = sum(1 for t in texts if REFERENCE.search(t))

    by_cue: dict[str, dict[str, int | float]] = {}
    samples: dict[str, list[str]] = {kind: [] for kind in CUES}

    for kind, pattern in CUES.items():
        hits = precedes = also = 0
        for text in texts:
            cue = pattern.search(text)
            if cue is None:
                continue
            hits += 1
            reference = REFERENCE.search(text)
            if reference is None:
                continue
            also += 1
            if reference.start() < cue.start():
                precedes += 1
                if len(samples[kind]) < 200 and 25 <= len(text) <= 90:
                    samples[kind].append(text)
        by_cue[kind] = {
            "utterances": hits,
            "with_reference": also,
            "reference_precedes_cue": precedes,
            "share_with_reference": round(also / hits, 4) if hits else 0.0,
        }

    # What each order costs in LLM calls: everything, versus the classes a
    # resolver would still have to visit once classification has run.
    resolve_first = total
    classify_first = sum(int(by_cue[k]["utterances"]) for k in RESOLVED_BY_CLASSIFYING_FIRST)

    random.seed(0)
    payload = json.dumps(
        {
            "utterances": total,
            "with_reference": with_reference,
            "share_with_reference": round(with_reference / total, 4),
            "by_cue": by_cue,
            "llm_calls": {
                "resolve_first": resolve_first,
                "classify_first": classify_first,
                "ratio": round(resolve_first / classify_first, 1) if classify_first else None,
            },
            "samples": {k: random.sample(v, min(4, len(v))) for k, v in samples.items() if v},
        },
        ensure_ascii=False,
        indent=2,
    )
    # Written as bytes: redirected stdout on Windows defaults to the console
    # codepage, which cannot encode the Korean in the samples.
    sys.stdout.buffer.write(payload.encode("utf-8") + b"\n")


if __name__ == "__main__":
    main()
