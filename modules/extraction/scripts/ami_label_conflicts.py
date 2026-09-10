"""Third pass: apply the real label mapping to the corpus and count what it decides.

``ami_analyze`` and ``ami_layers`` report what each layer contains. This one puts
the layers together the way the loader will, runs
``autune_extraction.labeling.ami.label_for`` over every dialogue act, and reports
how often the layers disagree and which precedence rule settled it.

It imports the mapping rather than restating it. A script with its own copy of
the table would measure the copy.

The question it answers: ``PRECEDENCE`` is a judgement made without numbers
(#102). These are the numbers.

    uv run python modules/extraction/scripts/ami_label_conflicts.py <corpus-root>

Prints JSON on stdout. AMI Meeting Corpus, CC BY 4.0.
"""

from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

from autune_extraction.labeling.ami import (
    DIALOGUE_ACTS,
    EXCLUDED_ACTS,
    PRECEDENCE,
    Evidence,
    label_for,
)

NITE = "{http://nite.sourceforge.net/}"
ROOT = Path(sys.argv[1])

HREF = re.compile(r"^(?P<file>[^#]+)#id\((?P<a>[^)]+)\)(?:\.\.id\((?P<b>[^)]+)\))?$")

AP_NAMES = {"apt_1": "POS", "apt_2": "NEG", "apt_3": "UNC", "apt_4": "PART", "apt_5": "ELA"}


def da_names(root: Path) -> dict[str, str]:
    """da-type id -> gloss, e.g. ``ami_da_7`` -> ``Offer``.

    **The gloss, not the name.** The corpus puts a short code in ``name``
    (``off``, ``el.inf``, ``sug``) and the readable label in ``gloss``. The
    mapping keys on the readable one, because that is what the AMI documentation
    and ours both call these acts.

    Reading ``name`` here produced zero commitments and zero open questions in
    the first run of this script — no error, just two empty classes. That is why
    ``check_mapping_against_ontology`` exists.
    """
    tree = ET.parse(root / "ontologies" / "da-types.xml")
    return {
        el.get(f"{NITE}id", ""): el.get("gloss", "")
        for el in tree.getroot().iter("da-type")
        if not el.findall("da-type")  # leaves only; the parents are class names
    }


def check_mapping_against_ontology(known: set[str]) -> None:
    """Refuse to report numbers for a mapping whose keys the corpus never uses.

    A key that matches nothing produces an empty class, not an exception, and an
    empty class in a macro-averaged F1 is a zero somebody spends a day tracing
    back. Fail here, loudly, with the names side by side.
    """
    named = set(DIALOGUE_ACTS) | set(EXCLUDED_ACTS)
    bullets = "\n  ".join
    if missing := sorted(named - known):
        raise SystemExit(
            f"in the mapping but not in the corpus ontology:\n  {bullets(missing)}\n\n"
            f"the ontology calls its acts:\n  {bullets(sorted(known))}"
        )
    if unaccounted := sorted(known - named):
        raise SystemExit(
            "in the corpus but the mapping neither uses nor excludes them:\n  "
            f"{bullets(unaccounted)}"
        )


_word_index: dict[str, dict[str, int]] = {}


def word_index(filename: str) -> dict[str, int]:
    """word id -> position, for one words file. Cached; the corpus is 139 meetings."""
    if filename in _word_index:
        return _word_index[filename]
    path = ROOT / "words" / filename
    index: dict[str, int] = {}
    if path.exists():
        position = 0
        for el in ET.parse(path).getroot():
            wid = el.get(f"{NITE}id")
            if wid is not None:
                index[wid] = position
                position += 1
    _word_index[filename] = index
    return index


def spans_of(el: ET.Element) -> list[tuple[str, int, int]]:
    """``(words file, first, last)`` for each range this annotation points at."""
    out: list[tuple[str, int, int]] = []
    for child in el.findall(f"{NITE}child"):
        m = HREF.match(child.get("href", "").strip())
        if not m:
            continue
        index = word_index(m.group("file"))
        start = index.get(m.group("a"))
        end = index.get(m.group("b") or m.group("a"))
        if start is not None and end is not None:
            out.append((m.group("file"), start, end))
    return out


def text_of(spans: list[tuple[str, int, int]]) -> str:
    """Readable text for a span list. Only called for samples — it re-reads words."""
    parts: list[str] = []
    for filename, start, end in spans:
        path = ROOT / "words" / filename
        if not path.exists():
            continue
        words = [
            (el.text or "").strip()
            for el in ET.parse(path).getroot()
            if el.get(f"{NITE}id") is not None
        ]
        parts.extend(w for w in words[start : end + 1] if w)
    text = re.sub(r"\s+([,.?!'])", r"\1", " ".join(parts))
    return re.sub(r"\s+", " ", text).strip()


def pointer(el: ET.Element, role: str) -> str | None:
    for ptr in el.findall(f"{NITE}pointer"):
        if ptr.get("role") == role:
            m = re.search(r"#id\(([^)]+)\)", ptr.get("href", ""))
            return m.group(1) if m else None
    return None


def polarity_by_dact() -> dict[str, str]:
    """dialogue-act id -> adjacency-pair type id, for the act that *responds*.

    The polarity describes how the target relates to the source, so it belongs to
    the target. Attaching it to the source would label the act being answered
    with the answer's sign.
    """
    out: dict[str, str] = {}
    for path in sorted((ROOT / "dialogueActs").glob("*.adjacency-pairs.xml")):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for pair in root.findall("adjacency-pair"):
            apt = pointer(pair, "type")
            target = pointer(pair, "target")
            if apt and target:
                # A dact can be the target of more than one pair. Keep the first;
                # the count of these is reported as multi_pair_targets.
                out.setdefault(target, apt)
    return out


def decision_spans() -> dict[str, list[tuple[int, int]]]:
    """words file -> the ranges a human marked as where a decision was made."""
    out: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for path in sorted((ROOT / "decision" / "manual").glob("*.decision.xml")):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for decision in root.findall("decision"):
            for filename, start, end in spans_of(decision):
                out[filename].append((start, end))
    return dict(out)


def overlaps(
    spans: list[tuple[str, int, int]], decisions: dict[str, list[tuple[int, int]]]
) -> bool:
    for filename, start, end in spans:
        for d_start, d_end in decisions.get(filename, ()):
            if start <= d_end and d_start <= end:
                return True
    return False


def main() -> None:
    names = da_names(ROOT)
    check_mapping_against_ontology({name for name in names.values() if name})
    polarity = polarity_by_dact()
    decisions = decision_spans()
    decision_meetings = {name.split(".")[0] for name in decisions}

    labelled: Counter[str] = Counter()
    unlabelled = 0
    total = 0
    sources: Counter[str] = Counter()
    conflicts: Counter[str] = Counter()
    acts_seen: Counter[str] = Counter()
    samples: dict[str, list[str]] = defaultdict(list)
    decision_overlap_dacts = 0

    for path in sorted((ROOT / "dialogueActs").glob("*.dialog-act.xml")):
        meeting = path.name.split(".")[0]
        in_decision_corpus = meeting in decision_meetings
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue

        for dact in root.findall("dact"):
            total += 1
            act = names.get(pointer(dact, "da-aspect") or "", "")
            acts_seen[act or "(none)"] += 1

            apt = polarity.get(dact.get(f"{NITE}id", ""))
            spans = spans_of(dact) if in_decision_corpus else []
            in_span = bool(spans) and overlaps(spans, decisions)
            if in_span:
                decision_overlap_dacts += 1

            label = label_for(
                Evidence(
                    in_decision_span=in_span,
                    dialogue_act=act,
                    adjacency_pair_type=apt,
                )
            )
            if label is None:
                unlabelled += 1
                continue

            labelled[label.kind.value] += 1
            sources[label.source.split(":")[0]] += 1
            if label.overruled:
                key = f"{label.kind.value} over {', '.join(k.value for k in label.overruled)}"
                conflicts[key] += 1
                if len(samples[key]) < 6:
                    text = text_of(spans or spans_of(dact))
                    if 3 <= len(text.split()) <= 30:
                        samples[key].append(f"[{act}/{AP_NAMES.get(apt or '', '-')}] {text}")

    random.seed(0)
    contested = sum(conflicts.values())
    print(
        json.dumps(
            {
                "meta": {
                    "dialogue_acts": total,
                    "meetings_with_decision_layer": len(decision_meetings),
                    "dacts_inside_a_decision_span": decision_overlap_dacts,
                    "dacts_that_are_a_pair_target": len(polarity),
                    "precedence": [kind.value for kind in PRECEDENCE],
                },
                "labelled": {
                    "total": sum(labelled.values()),
                    "share_of_corpus": round(sum(labelled.values()) / max(total, 1), 4),
                    "by_kind": labelled.most_common(),
                    "by_layer": sources.most_common(),
                },
                "unlabelled": unlabelled,
                "acts_seen": acts_seen.most_common(),
                "conflicts": {
                    "total": contested,
                    "share_of_labelled": round(contested / max(sum(labelled.values()), 1), 4),
                    "by_rule": conflicts.most_common(),
                },
                "conflict_samples": dict(samples),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
