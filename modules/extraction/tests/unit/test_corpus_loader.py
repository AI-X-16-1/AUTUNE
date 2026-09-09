"""Turning an annotated corpus into a split, labelled dataset.

No corpus needed for most of it: the split is arithmetic over meeting names and
sizes, and the round trip goes through the evaluation harness's own reader. One
test builds a four-file AMI in ``tmp_path`` — small, but it covers the join,
which is where the ontology's `name`/`gloss` split cost a whole afternoon.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autune_extraction.eval.dataset import load_eval_set
from autune_extraction.labeling.corpus import (
    SPLITS,
    AmiReader,
    Example,
    split_by_meeting,
    write_jsonl,
)


def example(meeting: str, kind: str = "commitment", index: int = 0) -> Example:
    return Example(utterance_id=f"{meeting}.{index}", kind=kind, text="a b c", meeting=meeting)


def corpus(meetings: dict[str, int], *, decision_meetings: set[str] = frozenset()) -> list[Example]:
    """``{meeting: utterance count}`` into examples, some carrying decisions."""
    out: list[Example] = []
    for meeting, count in meetings.items():
        for index in range(count):
            kind = "decision" if meeting in decision_meetings and index == 0 else "commitment"
            out.append(example(meeting, kind, index))
    return out


# --- the split is by meeting -------------------------------------------------


def test_a_meeting_never_lands_in_two_splits() -> None:
    """The property the whole function exists for.

    Two utterances from one meeting share a topic, four speakers and a
    vocabulary. Splitting at the utterance level scores the model on
    conversations it has already read, and the number comes out flattering.
    """
    splits = split_by_meeting(corpus({f"ES{n:04d}": 40 for n in range(60)}))

    seen: dict[str, str] = {}
    for name in SPLITS:
        for row in splits[name]:
            assert seen.setdefault(row.meeting, name) == name


def test_every_example_is_placed_exactly_once() -> None:
    examples = corpus({f"ES{n:04d}": 7 for n in range(40)})

    splits = split_by_meeting(examples)

    placed = [row for name in SPLITS for row in splits[name]]
    assert sorted(r.utterance_id for r in placed) == sorted(r.utterance_id for r in examples)


def test_the_same_corpus_splits_the_same_way_twice() -> None:
    """A split that moves between runs reads as the model improving.

    ``hash()`` is salted per process, which is why the bucket is a SHA-256.
    """
    examples = corpus({f"ES{n:04d}": 11 for n in range(50)})

    first = {r.utterance_id: name for name in SPLITS for r in split_by_meeting(examples)[name]}
    second = {r.utterance_id: name for name in SPLITS for r in split_by_meeting(examples)[name]}

    assert first == second


# --- the proportions actually come out -------------------------------------


def test_meetings_of_different_lengths_still_give_the_asked_for_sizes() -> None:
    """Cutting at 80% *of the meetings* is not 80% of the utterances.

    Real meetings differ several-fold in length; on AMI a tenth of the meetings
    was an eighth of the utterances. Splits are filled by utterance count for
    that reason.
    """
    uneven = {f"ES{n:04d}": (5 if n % 3 else 90) for n in range(60)}
    splits = split_by_meeting(corpus(uneven))
    total = sum(len(rows) for rows in splits.values())

    assert len(splits["train"]) / total == pytest.approx(0.8, abs=0.05)
    assert len(splits["validation"]) / total == pytest.approx(0.1, abs=0.05)
    assert len(splits["test"]) / total == pytest.approx(0.1, abs=0.05)


def test_a_label_confined_to_some_meetings_reaches_every_split() -> None:
    """AMI annotates decisions in 47 of 139 meetings, and those meetings supply
    almost every ``decision`` label.

    Without stratifying, the first run put 47.5% decisions in train against 6.5%
    in validation — two splits that are not samples of one distribution, so a
    validation score says nothing about the training one.
    """
    meetings = {f"ES{n:04d}": 30 for n in range(60)}
    annotated = {f"ES{n:04d}" for n in range(0, 60, 4)}  # a quarter of them

    splits = split_by_meeting(corpus(meetings, decision_meetings=annotated))

    for name in SPLITS:
        rows = splits[name]
        decisions = sum(1 for row in rows if row.kind == "decision")
        assert decisions > 0, f"{name} has no decisions to score"


def test_ratios_that_do_not_sum_to_one_are_refused() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        split_by_meeting(corpus({"ES0001": 5}), ratios=(0.8, 0.8, 0.8))


# --- the file the harness reads ---------------------------------------------


def test_what_is_written_is_what_the_harness_reads(tmp_path: Path) -> None:
    """One format for training data and scoring, verified by the real reader.

    Not a second parser asserting on a shape this module made up — if the two
    ever disagree, this is where it shows.
    """
    path = tmp_path / "train.jsonl"
    write_jsonl(path, [example("ES0001", "decision", 0), example("ES0001", "concern", 1)])

    loaded = load_eval_set(path)

    assert [e.kind.value for e in loaded.examples] == ["decision", "concern"]
    assert [e.utterance_id for e in loaded.examples] == ["ES0001.0", "ES0001.1"]


def test_the_meeting_is_not_written_to_the_file() -> None:
    """It exists to make the split, and the harness has no field for it."""
    assert set(example("ES0001").as_row()) == {"utterance_id", "kind", "text"}


def test_writing_creates_the_directory(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "nested" / "train.jsonl", [example("ES0001")])

    assert (tmp_path / "nested" / "train.jsonl").exists()


# --- reading a corpus --------------------------------------------------------


def write_ami(root: Path) -> None:
    """The smallest AMI that exercises the join: ontology, words, acts, pairs."""
    nite = 'xmlns:nite="http://nite.sourceforge.net/"'
    (root / "ontologies").mkdir(parents=True)
    (root / "ontologies" / "da-types.xml").write_text(
        f'<da-type nite:id="root" name="da-type" {nite}>'
        '<da-type nite:id="c1" name="task" gloss="Task">'
        '<da-type nite:id="ami_da_7" name="off" gloss="Offer"/>'
        '<da-type nite:id="ami_da_9" name="ass" gloss="Assess"/>'
        "</da-type></da-type>",
        encoding="utf-8",
    )
    (root / "words").mkdir()
    (root / "words" / "ES2002a.A.words.xml").write_text(
        f'<nite:root nite:id="w" {nite}>'
        '<w nite:id="w0">I</w><w nite:id="w1">will</w><w nite:id="w2">do</w>'
        '<w nite:id="w3">it</w><w nite:id="w4">fine</w>'
        "</nite:root>",
        encoding="utf-8",
    )
    (root / "dialogueActs").mkdir()
    (root / "dialogueActs" / "ES2002a.A.dialog-act.xml").write_text(
        f'<nite:root nite:id="d" {nite}>'
        '<dact nite:id="d1">'
        '<nite:pointer role="da-aspect" href="da-types.xml#id(ami_da_7)"/>'
        '<nite:child href="ES2002a.A.words.xml#id(w0)..id(w3)"/>'
        "</dact>"
        '<dact nite:id="d2">'
        '<nite:pointer role="da-aspect" href="da-types.xml#id(ami_da_9)"/>'
        '<nite:child href="ES2002a.A.words.xml#id(w4)..id(w4)"/>'
        "</dact>"
        "</nite:root>",
        encoding="utf-8",
    )


def test_reading_joins_the_layers_and_resolves_the_text(tmp_path: Path) -> None:
    """The pointers are the corpus: a dialogue act carries neither its label nor
    its words, only references to both."""
    write_ami(tmp_path)

    examples = list(AmiReader(tmp_path).load())

    assert len(examples) == 1, "Assess maps to nothing and must be dropped"
    assert examples[0].kind == "commitment"
    assert examples[0].text == "I will do it"
    assert examples[0].meeting == "ES2002a"


def test_the_label_comes_from_the_gloss_not_the_name(tmp_path: Path) -> None:
    """``ami_da_7`` is ``off`` by name and ``Offer`` by gloss, and the mapping
    keys on the gloss.

    Reading the wrong field produced zero commitments and zero open questions
    with no error, and the run looked plausible enough to write up.
    """
    write_ami(tmp_path)
    names = AmiReader(tmp_path).act_names()

    assert names["ami_da_7"] == "Offer"
    assert "off" not in names.values()


def test_only_leaf_acts_are_read(tmp_path: Path) -> None:
    """``Task`` is a class name, not something an utterance is annotated with."""
    write_ami(tmp_path)

    assert "Task" not in AmiReader(tmp_path).act_names().values()


def test_a_mapping_the_corpus_does_not_match_is_refused(tmp_path: Path) -> None:
    """This fixture has two acts; the real mapping names sixteen.

    A key that matches nothing yields an empty class rather than an error, and an
    empty class in a macro-averaged F1 is a zero somebody traces back to a model
    that is fine.
    """
    write_ami(tmp_path)

    with pytest.raises(ValueError, match="not in the corpus ontology"):
        AmiReader(tmp_path).check_mapping()


def test_short_acts_are_dropped(tmp_path: Path) -> None:
    """A ``<vocalsound>`` resolves to the empty string, and an empty example is a
    row the model learns noise from."""
    write_ami(tmp_path)

    assert list(AmiReader(tmp_path).load(min_words=5)) == []


def test_an_unreadable_annotation_file_costs_only_itself(tmp_path: Path) -> None:
    """One truncated file must not cost the other 694."""
    write_ami(tmp_path)
    (tmp_path / "dialogueActs" / "ES2003a.A.dialog-act.xml").write_text(
        "<not xml", encoding="utf-8"
    )

    assert len(list(AmiReader(tmp_path).load())) == 1


def test_the_written_rows_are_valid_json_lines(tmp_path: Path) -> None:
    write_ami(tmp_path)
    examples = list(AmiReader(tmp_path).load())
    path = tmp_path / "out.jsonl"

    write_jsonl(path, examples)

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows == [{"utterance_id": "d1", "kind": "commitment", "text": "I will do it"}]


def test_a_class_concentrated_in_a_few_meetings_is_spread_across_splits() -> None:
    """Filling by total count alone balances sizes and not classes.

    Decisions are not spread evenly over the meetings that have any. Once the
    filler was removed from that class, a total-count fill left 29.9% decisions
    in train against 40.8% in test — so each meeting now goes to whichever split
    has the largest shortfall in its neediest class.
    """
    meetings = {f"ES{n:04d}": 30 for n in range(60)}
    # A tenth of the meetings carry nearly all of the decisions.
    dense = {f"ES{n:04d}" for n in range(0, 60, 10)}
    examples = [
        example(meeting, "decision" if meeting in dense and i < 25 else "commitment", i)
        for meeting, count in meetings.items()
        for i in range(count)
    ]

    splits = split_by_meeting(examples)

    shares = [
        sum(1 for row in splits[name] if row.kind == "decision") / len(splits[name])
        for name in SPLITS
    ]
    assert max(shares) - min(shares) < 0.25, f"decision share spread too wide: {shares}"


def test_a_split_is_never_left_empty() -> None:
    """The first two greedy objectives put all 139 meetings in one split.

    Scoring the receiving split alone picks whichever is nearest done — the
    smallest — and minimising the worst across all splits is flat, because the
    maximum sits on a split the meeting is not going to.
    """
    splits = split_by_meeting(corpus({f"ES{n:04d}": 20 for n in range(40)}))

    assert all(splits[name] for name in SPLITS)
