"""Extract dialogue-act and decision annotations from the AMI corpus.

Resolves NITE word-range pointers to text so each annotation can be read as an
utterance, then reports the distribution and samples per dialogue-act type.
"""

from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

NITE = "{http://nite.sourceforge.net/}"
ROOT = Path(sys.argv[1])

HREF = re.compile(r"^(?P<file>[^#]+)#id\((?P<a>[^)]+)\)(?:\.\.id\((?P<b>[^)]+)\))?$")


def da_types(root: Path) -> dict[str, tuple[str, str, str]]:
    """id -> (name, gloss, parent class gloss)."""
    tree = ET.parse(root / "ontologies" / "da-types.xml")
    out: dict[str, tuple[str, str, str]] = {}
    for parent in tree.getroot().iter("da-type"):
        pgloss = parent.get("gloss", "")
        for child in parent.findall("da-type"):
            out[child.get(f"{NITE}id", "")] = (
                child.get("name", ""),
                child.get("gloss", ""),
                pgloss,
            )
    return out


_words_cache: dict[str, list[tuple[str, str]]] = {}


def words_of(root: Path, filename: str) -> list[tuple[str, str]]:
    """Ordered (id, text) for one words file. Punctuation kept, gaps skipped."""
    if filename in _words_cache:
        return _words_cache[filename]
    path = root / "words" / filename
    seq: list[tuple[str, str]] = []
    if path.exists():
        for el in ET.parse(path).getroot():
            wid = el.get(f"{NITE}id")
            if wid is None:
                continue
            # <w> carries text; <vocalsound>, <gap>, <disfmarker> do not.
            seq.append((wid, (el.text or "").strip()))
    _words_cache[filename] = seq
    return seq


def resolve(root: Path, hrefs: list[str]) -> str:
    """Turn one or more word-range pointers into a readable string."""
    parts: list[str] = []
    for href in hrefs:
        m = HREF.match(href.strip())
        if not m:
            continue
        seq = words_of(root, m.group("file"))
        index = {wid: i for i, (wid, _) in enumerate(seq)}
        start = index.get(m.group("a"))
        end = index.get(m.group("b") or m.group("a"))
        if start is None or end is None:
            continue
        parts.extend(t for _, t in seq[start : end + 1] if t)
    text = " ".join(parts)
    text = re.sub(r"\s+([,.?!'])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def children(el: ET.Element) -> list[str]:
    return [c.get("href", "") for c in el.findall(f"{NITE}child")]


@dataclass
class Decision:
    """One annotated decision, with the utterance spans it was settled over."""

    meeting: str
    spans: int
    speakers: list[str]
    words: int
    text: str


def main() -> None:
    types = da_types(ROOT)
    by_type: dict[str, list[str]] = defaultdict(list)
    counts: Counter[str] = Counter()

    da_files = sorted((ROOT / "dialogueActs").glob("*.dialog-act.xml"))
    for path in da_files:
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            continue
        for dact in tree.getroot().findall("dact"):
            ptr = dact.find(f"{NITE}pointer")
            if ptr is None:
                continue
            m = re.search(r"#id\(([^)]+)\)", ptr.get("href", ""))
            if not m:
                continue
            name, gloss, _ = types.get(m.group(1), ("?", "?", "?"))
            counts[gloss] += 1
            text = resolve(ROOT, children(dact))
            # Keep utterances long enough to judge; cap what we hold in memory.
            if 4 <= len(text.split()) <= 40 and len(by_type[gloss]) < 400:
                by_type[gloss].append(text)

    decisions: list[Decision] = []
    for path in sorted((ROOT / "decision" / "manual").glob("*.decision.xml")):
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            continue
        for dec in tree.getroot().findall("decision"):
            hrefs = children(dec)
            text = resolve(ROOT, hrefs)
            if text:
                decisions.append(
                    Decision(
                        meeting=path.name.split(".")[0],
                        spans=len(hrefs),
                        speakers=sorted({h.split("#")[0].split(".")[1] for h in hrefs if "#" in h}),
                        words=len(text.split()),
                        text=text,
                    )
                )

    random.seed(0)
    out = {
        "meta": {
            "da_files": len(da_files),
            "dacts": sum(counts.values()),
            "decision_files": len(list((ROOT / "decision" / "manual").glob("*.xml"))),
            "decisions": len(decisions),
        },
        "da_distribution": counts.most_common(),
        "da_samples": {k: random.sample(v, min(8, len(v))) for k, v in by_type.items()},
        "decision_stats": {
            "multi_span": sum(1 for d in decisions if d.spans > 1),
            "multi_speaker": sum(1 for d in decisions if len(d.speakers) > 1),
            "span_counts": Counter(d.spans for d in decisions).most_common(10),
        },
        "decision_samples": [asdict(d) for d in random.sample(decisions, min(12, len(decisions)))],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
