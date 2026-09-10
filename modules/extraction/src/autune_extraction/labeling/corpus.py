"""Reading the AMI corpus into labelled utterances.

AMI keeps text and annotation in different files that point at each other, so
nothing here is a parse of one document — it is a join. A dialogue act names its
type in an ontology file and its words as a range in a third file; the polarity
that separates a concern from an endorsement is in a fourth; the spans a human
marked as a decision are in a fifth. ``ami.label_for`` decides what the joined
row means, and this module is what puts the row together.

The output is the same JSONL the evaluation harness reads — ``utterance_id``,
``kind``, ``text``, one object per line — so one format serves training data and
scoring, and a file written here can be inspected with the tools that already
exist.

**A file written by this module is not an evaluation set.** The eval set is
drawn from the team's own meetings and is what ADR 0006 measures against; AMI is
English design roleplay and a score on it says nothing about Korean meetings.
Keeping the format identical is a convenience, not a claim that the two are
interchangeable.

Corpora are downloaded per machine and never committed — ``dataset/`` is
gitignored. AMI is CC BY 4.0 and requires attribution wherever results are
published.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from .ami import DIALOGUE_ACTS, EXCLUDED_ACTS, Evidence, label_for

NITE = "{http://nite.sourceforge.net/}"

HREF = re.compile(r"^(?P<file>[^#]+)#id\((?P<a>[^)]+)\)(?:\.\.id\((?P<b>[^)]+)\))?$")
"""A NITE pointer: a file, a first word id, and optionally a last one."""

SPLITS: tuple[str, ...] = ("train", "validation", "test")


@dataclass(frozen=True)
class Act:
    """One dialogue act, joined across the layers but not yet labelled.

    The seam between reading the corpus and deciding what a row means. The loader
    turns these into training examples; the conflict script counts what the
    mapping does with them. Both read the same join, so neither can drift into
    measuring a corpus the other does not build from.
    """

    meeting: str
    act_id: str
    name: str
    """The dialogue-act gloss, e.g. ``Offer``. Empty when the act has no type."""

    pair_type: str | None
    """``apt_1``..``apt_5`` when this act answers another one."""

    spans: list[tuple[str, int, int]]
    in_decision_span: bool

    @property
    def evidence(self) -> Evidence:
        return Evidence(
            in_decision_span=self.in_decision_span,
            dialogue_act=self.name,
            adjacency_pair_type=self.pair_type,
        )


@dataclass(frozen=True)
class Example:
    """One labelled utterance, ready to train on."""

    utterance_id: str
    kind: str
    text: str
    meeting: str
    """Which meeting it came from. Not written to the file — it exists so the
    split can be made by meeting rather than by utterance."""

    def as_row(self) -> dict[str, str]:
        return {"utterance_id": self.utterance_id, "kind": self.kind, "text": self.text}


class AmiReader:
    """One pass over an AMI corpus root.

    A class rather than functions because every lookup needs the word files, and
    reading them once per dialogue act would read each one a few hundred times.
    The cache is per instance so two corpora in one process do not share it.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._words: dict[str, list[str]] = {}
        self._index: dict[str, dict[str, int]] = {}

    # --- the files that say what a label means ---------------------------

    def act_names(self) -> dict[str, str]:
        """da-type id -> gloss, e.g. ``ami_da_7`` -> ``Offer``.

        The gloss, not the ``name``: the corpus puts a short code in ``name``
        (``off``, ``el.inf``) and the readable label in ``gloss``, and the
        mapping keys on the readable one. Reading the wrong field produces empty
        classes and no error, which is what ``check_mapping`` exists for.

        Leaves only — the parents are class names (``Task``, ``Elicit``), not
        acts anything is annotated with.
        """
        tree = ET.parse(self.root / "ontologies" / "da-types.xml")
        return {
            el.get(f"{NITE}id", ""): el.get("gloss", "")
            for el in tree.getroot().iter("da-type")
            if not el.findall("da-type")
        }

    def polarity_by_act(self) -> dict[str, str]:
        """dialogue-act id -> adjacency-pair type, for the act that *responds*.

        Polarity describes how the target relates to the source, so it belongs to
        the target. Hanging it on the source would label the act being answered
        with the answer's sign.
        """
        out: dict[str, str] = {}
        for path in sorted((self.root / "dialogueActs").glob("*.adjacency-pairs.xml")):
            root = self._parse(path)
            if root is None:
                continue
            for pair in root.findall("adjacency-pair"):
                apt = _pointer(pair, "type")
                target = _pointer(pair, "target")
                if apt and target:
                    # A dact can be the target of more than one pair; the first
                    # wins so a rerun labels the corpus the same way twice.
                    out.setdefault(target, apt)
        return out

    def decision_spans(self) -> dict[str, list[tuple[int, int]]]:
        """words file -> the ranges a human marked as where a decision was made.

        Only 47 of the corpus's meetings carry this layer, so an act outside them
        is not "not a decision" — it is unannotated. ``load`` passes
        ``in_decision_span=False`` for those, which is the same thing the
        mapping would conclude, but the count of meetings is worth knowing when
        reading how many decisions came out.
        """
        out: dict[str, list[tuple[int, int]]] = {}
        for path in sorted((self.root / "decision" / "manual").glob("*.decision.xml")):
            root = self._parse(path)
            if root is None:
                continue
            for decision in root.findall("decision"):
                for filename, start, end in self.spans_of(decision):
                    out.setdefault(filename, []).append((start, end))
        return out

    # --- resolving a pointer to words ------------------------------------

    def _load_words(self, filename: str) -> None:
        if filename in self._words:
            return
        path = self.root / "words" / filename
        texts: list[str] = []
        index: dict[str, int] = {}
        if path.exists():
            for element in ET.parse(path).getroot():
                word_id = element.get(f"{NITE}id")
                if word_id is None:
                    continue
                index[word_id] = len(texts)
                # <w> carries text; <vocalsound>, <gap> and <disfmarker> do not.
                texts.append((element.text or "").strip())
        self._words[filename] = texts
        self._index[filename] = index

    def spans_of(self, element: ET.Element) -> list[tuple[str, int, int]]:
        """``(words file, first, last)`` for each range this annotation points at."""
        out: list[tuple[str, int, int]] = []
        for child in element.findall(f"{NITE}child"):
            match = HREF.match(child.get("href", "").strip())
            if not match:
                continue
            filename = match.group("file")
            self._load_words(filename)
            index = self._index[filename]
            start = index.get(match.group("a"))
            end = index.get(match.group("b") or match.group("a"))
            if start is not None and end is not None:
                out.append((filename, start, end))
        return out

    def text_of(self, spans: list[tuple[str, int, int]]) -> str:
        """The spans as one readable string, punctuation reattached."""
        parts: list[str] = []
        for filename, start, end in spans:
            self._load_words(filename)
            parts.extend(word for word in self._words[filename][start : end + 1] if word)
        text = re.sub(r"\s+([,.?!'])", r"\1", " ".join(parts))
        return re.sub(r"\s+", " ", text).strip()

    def _parse(self, path: Path) -> ET.Element | None:
        try:
            return ET.parse(path).getroot()
        except ET.ParseError:
            # One unreadable annotation file must not cost the other 694.
            return None

    # --- the join --------------------------------------------------------

    def acts(self) -> Iterator[Act]:
        """Every dialogue act in the corpus, with the other layers joined on.

        Unlabelled: what a joined act means is ``ami.label_for``'s decision, and
        keeping the two apart is what lets the conflict script count the mapping
        without rebuilding the join.
        """
        names = self.act_names()
        polarity = self.polarity_by_act()
        decisions = self.decision_spans()

        for path in sorted((self.root / "dialogueActs").glob("*.dialog-act.xml")):
            root = self._parse(path)
            if root is None:
                continue
            meeting = path.name.split(".")[0]

            for dact in root.findall("dact"):
                act_id = dact.get(f"{NITE}id")
                if not act_id:
                    # Every row this becomes is keyed by it. Two id-less acts
                    # would both write ``utterance_id: ""``, which the harness
                    # rejects as a duplicate (#95) — but only when it reads the
                    # file, long after the loader said it was fine.
                    continue

                spans = self.spans_of(dact)
                yield Act(
                    meeting=meeting,
                    act_id=act_id,
                    name=names.get(_pointer(dact, "da-aspect") or "", ""),
                    pair_type=polarity.get(dact.get(f"{NITE}id", "")),
                    spans=spans,
                    in_decision_span=_overlaps(spans, decisions),
                )

    def load(self, *, min_words: int = 1) -> Iterator[Example]:
        """Every act the mapping gives a kind, with its text resolved.

        Acts the mapping is silent about are dropped rather than labelled. They
        are most of the corpus — 100,039 of 117,915 — and a loader that forced a
        label on them would teach the model that everything is a commitment.

        ``min_words`` drops acts that resolve to nothing or to a single token. A
        ``<vocalsound>`` with no words resolves to the empty string, and an empty
        training example is a row the model learns noise from.
        """
        for act in self.acts():
            label = label_for(act.evidence)
            if label is None:
                continue
            text = self.text_of(act.spans)
            if len(text.split()) < min_words:
                continue
            yield Example(
                utterance_id=act.act_id,
                kind=label.kind.value,
                text=text,
                meeting=act.meeting,
            )

    def check_mapping(self) -> None:
        """Refuse to read a corpus the mapping does not match.

        A mapping key that matches nothing produces an empty class, not an
        exception, and an empty class in a macro-averaged F1 is a zero somebody
        spends a day tracing back to a model that is fine. Both directions are
        checked: a name we invented, and an act the corpus has that nobody
        decided about.
        """
        known = {gloss for gloss in self.act_names().values() if gloss}
        named = set(DIALOGUE_ACTS) | set(EXCLUDED_ACTS)
        bullets = "\n  ".join
        if missing := sorted(named - known):
            raise ValueError(
                f"in the mapping but not in the corpus ontology:\n  {bullets(missing)}\n\n"
                f"the ontology calls its acts:\n  {bullets(sorted(known))}"
            )
        if unaccounted := sorted(known - named):
            raise ValueError(
                "in the corpus but the mapping neither uses nor excludes them:\n  "
                f"{bullets(unaccounted)}"
            )


def _pointer(element: ET.Element, role: str) -> str | None:
    for pointer in element.findall(f"{NITE}pointer"):
        if pointer.get("role") == role:
            match = re.search(r"#id\(([^)]+)\)", pointer.get("href", ""))
            return match.group(1) if match else None
    return None


def _overlaps(
    spans: list[tuple[str, int, int]], decisions: dict[str, list[tuple[int, int]]]
) -> bool:
    for filename, start, end in spans:
        for decision_start, decision_end in decisions.get(filename, ()):
            if start <= decision_end and decision_start <= end:
                return True
    return False


# --- splitting ---------------------------------------------------------------


def split_by_meeting(
    examples: list[Example], *, ratios: tuple[float, float, float] = (0.8, 0.1, 0.1)
) -> dict[str, list[Example]]:
    """Divide into train / validation / test **by meeting, never by utterance**.

    Two utterances from one meeting share a topic, a room, four speakers and a
    vocabulary. Splitting at the utterance level puts some of every meeting in
    training, so the model is scored on conversations it has already read and the
    number comes out flattering — the mistake ADR 0006 exists to stop us making
    with a headline figure.

    **Stratified on whether the meeting carries a decision layer.** AMI annotates
    decisions in 47 of its 139 meetings, and those meetings supply almost every
    ``decision`` label. Bucketing all 139 together put 47.5% decisions in train
    against 6.5% in validation — two splits that are not samples of one
    distribution, and a validation score from that says nothing about the
    training one.

    See ``_place_stratum`` for how a stratum is divided, and for the three ways
    of doing it that looked right and were not.
    """
    if not 0.999 <= sum(ratios) <= 1.001:
        raise ValueError(f"ratios must sum to 1, got {sum(ratios)}")

    profiles: dict[str, Counter[str]] = defaultdict(Counter)
    for example in examples:
        profiles[example.meeting][example.kind] += 1
        profiles[example.meeting]["*"] += 1

    decision_meetings = {m for m, counts in profiles.items() if counts["decision"]}
    # A meeting with any decision belongs to the annotated stratum, even though
    # most of its utterances are something else.
    strata = (decision_meetings, set(profiles) - decision_meetings)

    placement: dict[str, str] = {}
    for meetings in strata:
        placement.update(_place_stratum(meetings, profiles, ratios))

    out: dict[str, list[Example]] = {name: [] for name in SPLITS}
    for example in examples:
        out[placement[example.meeting]].append(example)
    return out


def _place_stratum(
    meetings: set[str], profiles: dict[str, Counter[str]], ratios: tuple[float, float, float]
) -> dict[str, str]:
    """Assign one stratum's meetings, balancing every class at once.

    Three earlier attempts are worth knowing about, because each looked right.

    Comparing every meeting's hash against 0.8 does not stratify at all: a
    meeting's hash is the same number whichever group it is considered in, so
    grouping changes no assignment. It produced exactly the split it was meant to
    fix.

    Ranking within the stratum and cutting at 80% *of the meetings* fixes the
    class shares and not the sizes, because meetings differ in length: a tenth of
    the meetings was an eighth of the utterances.

    Filling by total count fixes the sizes and not the classes. Decisions are not
    spread evenly over the meetings that have any — once the filler was removed
    from that class, the same split held 29.9% decisions in train against 40.8%
    in test.

    So each meeting goes wherever it leaves the worst-served class best served:
    the score is the largest shortfall any class would still have, and the
    meeting goes to the split that minimises it. Meetings are considered in hash
    order, stable across machines and runs; the greedy choice then depends on
    what came before, so adding meetings to a corpus reshuffles the ones after.
    Acceptable for a fixed corpus download, and not for the team's own meetings,
    which arrive one at a time — an evaluation set built that way needs a
    placement it can store rather than recompute.
    """
    ordered = sorted(meetings, key=_bucket)
    totals: Counter[str] = Counter()
    for meeting in ordered:
        totals.update(profiles[meeting])

    targets = {
        name: {kind: count * ratio for kind, count in totals.items()}
        for name, ratio in zip(SPLITS, ratios, strict=True)
    }
    filled: dict[str, Counter[str]] = {name: Counter() for name in SPLITS}

    def shortfall(name: str) -> float:
        """How badly served this split's neediest class is, as a share of target.

        Goes negative once a class is over-filled, so a split that has had enough
        stops competing rather than merely competing less.
        """
        have = filled[name]
        return max(
            (target - have[kind]) / target for kind, target in targets[name].items() if target > 0
        )

    placement: dict[str, str] = {}
    for meeting in ordered:
        # The split whose neediest class is furthest from its target. Shortfall is
        # a *share* of the target, so train's eightfold larger quota does not let
        # it win every round — one meeting closes eight times less of its gap.
        #
        # Two objectives that read better and do not work: placing the meeting
        # where the receiving split ends up best served picks whichever split is
        # nearest done, which is the smallest one; and minimising the worst
        # shortfall across all splits is flat, because the maximum sits on a split
        # this meeting is not going to. Both put all 139 meetings in ``test``.
        split = max(SPLITS, key=lambda name: (shortfall(name), SPLITS.index(name)))
        placement[meeting] = split
        filled[split].update(profiles[meeting])
    return placement


def _bucket(meeting: str) -> float:
    """A meeting's place in [0, 1). Stable across machines and Python runs.

    ``hash()`` is salted per process and would put a meeting in training one day
    and in test the next, which reads as the model improving.
    """
    digest = hashlib.sha256(meeting.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def write_jsonl(path: Path, examples: list[Example]) -> None:
    """Write the split, one object per line, in the harness's format."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for example in examples:
            handle.write(json.dumps(example.as_row(), ensure_ascii=False) + "\n")
