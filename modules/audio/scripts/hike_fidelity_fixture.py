"""Regenerate tests/unit/fixtures/hike_fidelity.json from real predictions.

The fixture is what makes "our MER and PIER are comparable to the HiKE paper"
a tested statement. Every number in it is produced here by a line-by-line port
of HiKE's own scoring — jiwer's transform chain and rapidfuzz's ``editops``,
as ``src/main.py`` and ``src/metrics/pier`` in ThetaOne-AI/HiKE run them —
and never by ``autune_audio``. The test then asserts that ``autune_audio``
reproduces those numbers.

    uv run --with rapidfuzz --with regex python modules/audio/scripts/hike_fidelity_fixture.py \
        predictions.jsonl [more.jsonl ...] > modules/audio/tests/unit/fixtures/hike_fidelity.json

rapidfuzz and regex are deliberately not module dependencies: they exist to
check our implementation, not to be it. Rows are chosen to cover the cases
that distinguish the two normalisers (contractions, hyphens, digits, brackets,
multi-word tags, loanwords, perfect transcripts) and every CS level; only rows
where the port and ``autune_audio`` agree are pinned, and the header records
how many candidates disagreed and why.

Two known, deliberate divergences are counted in the header rather than fixed:
tie-breaking between equal-cost alignments (rapidfuzz's backtrace is not
ours; PIER can attribute an edit to a neighbouring position), and HiKE not
re-normalising the reference after loanword folding (``YouTuber``), which we
do. ``classify`` admits a row to either class only on evidence — identical
tokens and distance for a tie, a reference that normalisation would change
for the other — and counts everything else as unexplained, which the test
requires to be zero.
"""

from __future__ import annotations

import json
import random
import re
import sys
import unicodedata

import pyarrow.parquet as pq
import regex
from rapidfuzz.distance import Levenshtein

from autune_audio.eval import codeswitch as cs
from autune_audio.eval.alignment import edit_ops
from autune_audio.eval.hike import download

_PUNCT = {chr(i) for i in range(sys.maxunicode + 1) if unicodedata.category(chr(i)).startswith("P")}


def hike_normalize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"won't", "will not", s)
    s = re.sub(r"can\'t", "can not", s)
    s = re.sub(r"let\'s", "let us", s)
    s = re.sub(r"n\'t", " not", s)
    s = re.sub(r"\'re", " are", s)
    s = re.sub(r"\'s", " is", s)
    s = re.sub(r"\'d", " would", s)
    s = re.sub(r"\'ll", " will", s)
    s = re.sub(r"\'t", " not", s)
    s = re.sub(r"\'ve", " have", s)
    s = re.sub(r"\'m", " am", s)
    s = re.sub(r"[<\[][^>\]]*[>\]]", "", s)
    s = re.sub(r"\b—\b", " ", s)
    s = "".join(c for c in s if c not in _PUNCT)
    s = re.sub(r"\s", " ", s)
    s = re.sub(r"\s\s+", " ", s).strip()
    return " ".join(w for w in s.split(" ") if w)


def add_space(text):
    return regex.sub(r"([A-Za-z0-9]+)(?=\p{Script=Hangul})", r"\1 ", text)


def space_korean(s):
    s = re.sub(r"([가-힣])", r" \1 ", s)
    return re.sub(r"\s+", " ", s).strip()


def replace_loanword(text, loanwords):
    for lw in json.loads(loanwords):
        text = text.replace(lw["Korean"], lw["English"])
    return text


def rf_ops(ref, hyp):
    vocab = {w: i for i, w in enumerate(set(ref) | set(hyp))}
    a = "".join(chr(vocab[w]) for w in ref)
    b = "".join(chr(vocab[w]) for w in hyp)
    return [(o.tag, o.src_pos) for o in Levenshtein.editops(a, b)]


def hike_pier(ref_labeled, pred):
    """PIER, plus the token lists and distance the classifier compares."""
    ref = ref_labeled + " 뷁"
    pred = pred + " 뷁"
    poi = []
    for tags, m in enumerate(re.finditer(r"<tag (.*?)>", ref)):
        start = len(ref[: m.start()].split()) - tags
        poi.extend(range(start, start + len(m.group(1).split())))
    notag = re.sub(r"<tag (.*?)>", r"\1", ref)
    r, h = notag.split(), pred.split()
    ops = rf_ops(r, h)
    return sum(1 for t, p in ops if p in poi) / len(poi), (r[:-1], h[:-1], len(ops))


def hike_mer(ref, pred):
    r = space_korean(ref).split()
    h = space_korean(pred).split()
    return len(rf_ops(r, h)) / len(r)


def classify(r, lw, hyp, port_tokens, o_p, f_p) -> str:
    """Why the port and autune_audio disagree on a row, strictly.

    "tie_break" only when both saw the same tokens and the same distance, so
    the backtrace is the only thing left to differ. "reference_not_renormalised"
    when normalising the folded reference changes it, which is the one place
    our chain touches the reference and HiKE's does not. Anything else is
    "unexplained", and the test refuses a fixture that has any.
    """
    folded_ref = replace_loanword(r["text_normalized"], r["loanwords"])
    folded_pier = re.sub(
        r"<tag (.*?)>", r"\1", replace_loanword(r["text_pier_labeled"], r["loanwords"])
    )
    if hike_normalize(folded_ref) != folded_ref or hike_normalize(folded_pier) != folded_pier:
        return "reference_not_renormalised"
    our_ref, our_poi = cs._tagged_words(cs.fold_loanwords(r["text_pier_labeled"], lw))
    our_hyp = cs._LATIN_BEFORE_HANGUL.sub(
        r"\1 ", cs.hike_normalise(cs.fold_loanwords(hyp, lw))
    ).split()
    port_ref, port_hyp, port_distance = port_tokens
    our_distance = len(edit_ops(our_ref, our_hyp))
    if (our_ref, our_hyp, our_distance) == (port_ref, port_hyp, port_distance):
        return "tie_break"
    return "unexplained"


def main(paths: list[str]) -> None:
    rows = {
        r["sample_id"]: r
        for r in pq.read_table(
            download(),
            columns=["sample_id", "text_normalized", "text_pier_labeled", "loanwords", "cs_level"],
        ).to_pylist()
    }
    preds: dict[str, list[str]] = {}
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    p = json.loads(line)
                    preds.setdefault(p["sample_id"], []).append(p["hypothesis"])

    candidates: list[dict] = []
    disagreements: dict[str, int] = {
        "tie_break": 0,
        "reference_not_renormalised": 0,
        "unexplained": 0,
    }
    for sid, hyps in preds.items():
        r = rows[sid]
        lw = tuple((e["Korean"], e["English"]) for e in json.loads(r["loanwords"]))
        for hyp in hyps:
            pred = hike_normalize(replace_loanword(hyp, r["loanwords"]))
            f_m = hike_mer(replace_loanword(r["text_normalized"], r["loanwords"]), pred)
            f_p, port_tokens = hike_pier(
                replace_loanword(r["text_pier_labeled"], r["loanwords"]), add_space(pred)
            )
            o_m = cs.mixed_error_rate(r["text_normalized"], hyp, loanwords=lw).mer
            o_p = cs.point_of_interest_error_rate(r["text_pier_labeled"], hyp, loanwords=lw).pier
            same = abs(o_m - f_m) < 1e-9 and abs(o_p - f_p) < 1e-9
            if not same:
                disagreements[classify(r, lw, hyp, port_tokens, o_p, f_p)] += 1
            features = {
                "contraction": "'" in hyp,
                "hyphen": "-" in hyp or "-" in r["text_normalized"],
                "digit": bool(re.search(r"\d", hyp + r["text_normalized"])),
                "loanword": bool(lw),
                "bracket": bool(re.search(r"[\[<]", hyp)),
                "perfect": f_m == 0.0,
                "multiword_tag": bool(re.search(r"<tag [^>]+ [^>]+>", r["text_pier_labeled"])),
            }
            candidates.append(
                {
                    "sample_id": sid,
                    "cs_level": r["cs_level"],
                    "text_normalized": r["text_normalized"],
                    "text_pier_labeled": r["text_pier_labeled"],
                    "loanwords": [list(pair) for pair in lw],
                    "hypothesis": hyp,
                    "mer": f_m,
                    "pier": f_p,
                    "_same": same,
                    "_features": [k for k, v in features.items() if v],
                }
            )

    total = len(candidates)
    agree = total - sum(disagreements.values())
    print(f"candidates={total} agree={agree} disagree={disagreements}", file=sys.stderr)

    rng = random.Random(3)
    chosen: list[dict] = []
    used: set[str] = set()

    def take(pred, n):
        pool = [c for c in candidates if c["_same"] and c["sample_id"] not in used and pred(c)]
        rng.shuffle(pool)
        for c in pool[:n]:
            chosen.append(c)
            used.add(c["sample_id"])

    for feature in (
        "contraction",
        "hyphen",
        "digit",
        "bracket",
        "multiword_tag",
        "loanword",
        "perfect",
    ):
        take(lambda c, f=feature: f in c["_features"], 3)
    for level in ("word", "phrase", "sentence"):
        take(lambda c, lv=level: c["cs_level"] == lv, 2)
    take(lambda c: 0 < c["mer"] < 0.5, 3)

    for c in chosen:
        c["features"] = c.pop("_features")
        c.pop("_same")
    fixture = {
        "_about": (
            "Rows from thetaone-ai/HiKE (https://huggingface.co/datasets/thetaone-ai/HiKE, "
            "Apache-2.0; Lee et al., EACL Findings 2026) with "
            "hypotheses produced by faster-whisper large-v3 on CPU. mer and pier were computed "
            "by a line-by-line port of HiKE's own scoring (jiwer transforms + rapidfuzz "
            "editops, src/main.py and src/metrics/pier), not by autune_audio. Regenerate with "
            "modules/audio/scripts/hike_fidelity_fixture.py; do not edit numbers by hand."
        ),
        "candidates_scored": total,
        "candidates_agreeing": agree,
        "candidates_differing": disagreements,
        "rows": chosen,
    }
    json.dump(fixture, sys.stdout, ensure_ascii=False, indent=2)
    print(file=sys.stdout)


if __name__ == "__main__":
    main(sys.argv[1:])
