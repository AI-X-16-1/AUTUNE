"""Second pass: abstractive sections and adjacency-pair polarity.

The dialogue-act inventory alone has no polarity and no settled statement.
Those live in two other layers, which is what this pass measures.
"""

from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import NamedTuple
from xml.etree import ElementTree as ET

NITE = "{http://nite.sourceforge.net/}"
ROOT = Path(sys.argv[1])

AP_TYPES = {
    "apt_1": "POS Support/Positive",
    "apt_2": "NEG Objection/Negative",
    "apt_3": "UNC Uncertain",
    "apt_4": "PART Partial agreement",
    "apt_5": "ELA Elaboration",
}


class Sections(NamedTuple):
    """Sentence counts and samples per abstractive-summary section."""

    files: int
    counts: list[tuple[str, int]]
    samples: dict[str, list[str]]


def abstractive() -> Sections:
    counts: Counter[str] = Counter()
    samples: dict[str, list[str]] = defaultdict(list)
    files = sorted((ROOT / "abstractive").glob("*.abssumm.xml"))
    for path in files:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for section in root:
            tag = section.tag
            for sent in section.findall("sentence"):
                text = re.sub(r"\s+", " ", (sent.text or "")).strip()
                if not text:
                    continue
                counts[tag] += 1
                if len(samples[tag]) < 300:
                    samples[tag].append(text)
    return Sections(len(files), counts.most_common(), dict(samples))


def adjacency() -> dict[str, int | list[tuple[str, int]]]:
    counts: Counter[str] = Counter()
    files = sorted((ROOT / "dialogueActs").glob("*.adjacency-pairs.xml"))
    for path in files:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for ap in root.findall("adjacency-pair"):
            for ptr in ap.findall(f"{NITE}pointer"):
                if ptr.get("role") != "type":
                    continue
                m = re.search(r"#id\(([^)]+)\)", ptr.get("href", ""))
                if m:
                    counts[AP_TYPES.get(m.group(1), m.group(1))] += 1
    return {"files": len(files), "counts": counts.most_common()}


def main() -> None:
    random.seed(0)
    sections = abstractive()
    out = {
        "abstractive": {"files": sections.files, "counts": sections.counts},
        "abstractive_samples": {
            k: random.sample(v, min(10, len(v))) for k, v in sections.samples.items()
        },
        "adjacency_pairs": adjacency(),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
