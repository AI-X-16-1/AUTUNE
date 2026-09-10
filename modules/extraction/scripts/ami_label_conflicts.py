"""Third pass: how often the annotation layers disagree, and which rule settles it.

``ami_analyze`` and ``ami_layers`` report what each layer contains. This one puts
them together the way the loader does, runs the real mapping over every dialogue
act, and counts the disagreements.

It reads the corpus through ``autune_extraction.labeling.corpus`` and the mapping
through ``labeling.ami`` — imported, not restated. A script with its own copy of
either would measure the copy, and the numbers it prints are the ones
``PRECEDENCE`` is argued from (#102).

    uv run python modules/extraction/scripts/ami_label_conflicts.py <corpus-root>

Prints JSON on stdout. AMI Meeting Corpus, CC BY 4.0.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from autune_extraction.labeling.ami import PRECEDENCE, label_for
from autune_extraction.labeling.corpus import AmiReader

AP_NAMES = {"apt_1": "POS", "apt_2": "NEG", "apt_3": "UNC", "apt_4": "PART", "apt_5": "ELA"}


def main() -> None:
    reader = AmiReader(Path(sys.argv[1]))
    try:
        reader.check_mapping()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    labelled: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    conflicts: Counter[str] = Counter()
    acts_seen: Counter[str] = Counter()
    samples: dict[str, list[str]] = defaultdict(list)
    meetings_with_decisions: set[str] = set()
    unlabelled = 0
    total = 0
    in_span = 0

    for act in reader.acts():
        total += 1
        acts_seen[act.name or "(none)"] += 1
        in_span += act.in_decision_span
        if act.in_decision_span:
            meetings_with_decisions.add(act.meeting)

        label = label_for(act.evidence)
        if label is None:
            unlabelled += 1
            continue

        labelled[label.kind.value] += 1
        sources[label.source.split(":")[0]] += 1
        if label.overruled:
            rule = f"{label.kind.value} over {', '.join(k.value for k in label.overruled)}"
            conflicts[rule] += 1
            if len(samples[rule]) < 6:
                text = reader.text_of(act.spans)
                if 3 <= len(text.split()) <= 30:
                    pair = AP_NAMES.get(act.pair_type or "", "-")
                    samples[rule].append(f"[{act.name}/{pair}] {text}")

    contested = sum(conflicts.values())
    print(
        json.dumps(
            {
                "meta": {
                    "dialogue_acts": total,
                    "meetings_with_decisions": len(meetings_with_decisions),
                    "dacts_inside_a_decision_span": in_span,
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
