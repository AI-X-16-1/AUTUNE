"""What the training code reads, and what it must not be able to reach.

No model and no ``transformers`` -- everything here is arithmetic over label
strings and facts about the import graph, which is why it runs in CI on every
push rather than on whoever has the extra installed.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from autune_contracts.enums import UtteranceKind
from autune_extraction import training
from autune_extraction.labels import NONE
from autune_extraction.training.dataset import (
    LABEL_TO_ID,
    LABELS,
    DatasetError,
    TrainExample,
    class_weights,
    label_counts,
    read_split,
)


def write(directory: Path, name: str, rows: list[dict[str, str]]) -> Path:
    path = directory / f"{name}.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8"
    )
    return path


def rows(*labels: str) -> list[dict[str, str]]:
    return [
        {"utterance_id": f"utt_{i}", "text": f"발화 {i}", "kind": label}
        for i, label in enumerate(labels)
    ]


# --- the label order is part of the checkpoint -------------------------------


def test_label_order_is_pinned() -> None:
    """These ids are baked into every checkpoint ever trained.

    A model trained with ``decision`` at index 1 answers 1 for a decision
    forever. Deriving the order from ``UtteranceKind`` would let a reorder of
    the contract enum silently repoint every id in every existing checkpoint --
    and a test written as ``LABELS == tuple(UtteranceKind)`` would move with it
    and keep passing, which is the shape of assertion that let this through once
    already in the classifier seam.
    """
    assert LABELS == (
        "commitment",
        "decision",
        "open_question",
        "concern",
        "ambiguous",
        "none",
    )
    assert LABEL_TO_ID["commitment"] == 0
    assert LABEL_TO_ID["ambiguous"] == 4
    assert LABEL_TO_ID["none"] == 5, "appended, so the five kinds kept their ids (#149)"


def test_every_contract_kind_has_a_label_and_none_is_the_only_other() -> None:
    """Pinned order, but not a divergent set: a kind the contract has and this
    cannot train is a class the model will never predict. ``none`` is the one
    label the contract does not have, and it stays the only one."""
    assert set(LABELS) == {kind.value for kind in UtteranceKind} | {NONE}


# --- reading a split ---------------------------------------------------------


def test_a_split_reads_back_with_its_labels(tmp_path: Path) -> None:
    write(tmp_path, "train", rows("decision", "concern"))

    examples = read_split(tmp_path, "train")

    assert [e.label for e in examples] == ["decision", "concern"]
    assert [e.label_id for e in examples] == [1, 3]


def test_none_reads_back_as_the_sixth_label(tmp_path: Path) -> None:
    """What the loader writes for an utterance that is none of the kinds."""
    write(tmp_path, "train", rows("none", "commitment"))

    examples = read_split(tmp_path, "train")

    assert [e.label for e in examples] == ["none", "commitment"]
    assert [e.label_id for e in examples] == [5, 0]


def test_a_missing_split_stops_rather_than_training_on_nothing(tmp_path: Path) -> None:
    with pytest.raises(DatasetError) as caught:
        read_split(tmp_path, "train")

    assert "labeling" in str(caught.value)


def test_an_empty_split_stops(tmp_path: Path) -> None:
    write(tmp_path, "train", [])

    with pytest.raises(DatasetError):
        read_split(tmp_path, "train")


def test_a_duplicate_utterance_is_refused(tmp_path: Path) -> None:
    """The same utterance twice weights it twice, and it is the shape a
    regenerated corpus produces when the id rule changes."""
    duplicated = rows("decision", "concern")
    duplicated[1]["utterance_id"] = duplicated[0]["utterance_id"]
    write(tmp_path, "train", duplicated)

    with pytest.raises(DatasetError, match="duplicate"):
        read_split(tmp_path, "train")


def test_an_empty_text_is_refused(tmp_path: Path) -> None:
    """An utterance with no words carries a label the model cannot learn from,
    and it is what a resolver returns when a pointer does not resolve."""
    blank = rows("decision")
    blank[0]["text"] = "   "
    write(tmp_path, "train", blank)

    with pytest.raises(DatasetError, match="empty text"):
        read_split(tmp_path, "train")


def test_an_unknown_label_is_refused(tmp_path: Path) -> None:
    unknown = rows("decision")
    unknown[0]["kind"] = "smalltalk"
    write(tmp_path, "train", unknown)

    with pytest.raises(DatasetError):
        read_split(tmp_path, "train")


def test_a_parse_error_never_quotes_the_line(tmp_path: Path) -> None:
    """The line is corpus text. A message that quotes it puts that text into a
    traceback, a log line, and whatever collects them."""
    path = tmp_path / "train.jsonl"
    path.write_text('{"utterance_id": "utt_0", "text": "비밀 발화", "kind"', encoding="utf-8")

    with pytest.raises(DatasetError) as caught:
        read_split(tmp_path, "train")

    assert "비밀 발화" not in str(caught.value)
    assert "line 1" in str(caught.value)


# --- class weights -----------------------------------------------------------


def test_weights_favour_the_rare_class() -> None:
    """ambiguous recall measured 0.172 on the first run (#115): the confirmation
    DM missed 83% of what it exists for, and 53 of those were written down as
    settled decisions. A flat loss is what produces that -- guessing the common
    class is most of the accuracy."""
    examples = [TrainExample(f"utt_{i}", "t", "decision") for i in range(90)]
    examples += [TrainExample(f"utt_r{i}", "t", "ambiguous") for i in range(10)]
    examples += [
        TrainExample(f"utt_{label}{i}", "t", label)
        for label in ("commitment", "open_question", "concern")
        for i in range(10)
    ]

    weights = class_weights(examples)

    assert weights[LABEL_TO_ID["ambiguous"]] > weights[LABEL_TO_ID["decision"]]


def test_a_balanced_corpus_weights_everything_the_same() -> None:
    examples = [TrainExample(f"utt_{label}{i}", "t", label) for label in LABELS for i in range(4)]

    assert class_weights(examples) == pytest.approx([1.0] * len(LABELS))


def test_an_absent_class_weighs_nothing_rather_than_dividing_by_zero() -> None:
    """It also cannot be learned, which is why ``__main__`` prints the counts and
    ``train`` refuses the run -- a zero here is a reason to stop, not a weight."""
    examples = [
        TrainExample(f"utt_{label}{i}", "t", label) for label in LABELS[:4] for i in range(4)
    ]

    weights = class_weights(examples)

    assert weights[LABEL_TO_ID["ambiguous"]] == 0.0
    assert all(weight > 0 for weight in weights[:4])


def test_label_counts_reports_a_collapsed_class_as_zero() -> None:
    counts = label_counts([TrainExample("utt_0", "t", "decision")])

    assert counts["decision"] == 1
    assert counts["ambiguous"] == 0


# --- the training code cannot reach the database -----------------------------

FORBIDDEN = frozenset({"models", "service", "sqlalchemy", "autune_core", "slack", "tasks"})


def imported_names(source: str) -> list[str]:
    """Every module name ``source`` imports, from anywhere in the file.

    Both halves of an ``ImportFrom`` are read. ``from ..models import X`` puts
    the name in ``node.module``, but ``from .. import models`` leaves that
    ``None`` and puts it in ``node.names`` -- and the second is the form a person
    writes by hand. Reading only ``node.module`` let it through, which is what
    mkkim68 found in review.

    ``ast.walk`` rather than the top-level body, so an import inside a function
    is seen: that one imports cleanly and closes only when the function first
    runs, which is the version that survives review.

    Static imports only. ``importlib.import_module("..models")`` defeats this and
    nothing short of executing the code would catch it.
    """
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.module is not None:
                names.append(node.module)
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return names


def forbidden_in(source: str) -> list[str]:
    return [name for name in imported_names(source) if set(name.split(".")) & FORBIDDEN]


@pytest.mark.parametrize(
    "source",
    [
        "from ..models import ExtActionItem",
        "from .. import models",
        "import autune_extraction.models",
        "import autune_extraction.models as m",
        "from autune_extraction.service import create_action_item",
        "import sqlalchemy",
        "def later():\n    from .. import models",
    ],
    ids=[
        "from-module-import-name",
        "from-package-import-module",
        "absolute",
        "aliased",
        "service",
        "sqlalchemy",
        "inside-a-function",
    ],
)
def test_the_guard_catches_every_way_of_writing_it(source: str) -> None:
    """The guard is tested, not just used.

    A guard with a hole is worse than no guard: passing it reads as a signal.
    This one had exactly one hole -- ``from .. import models`` -- and it went
    unnoticed because the injection used to check the guard happened to use the
    other form.
    """
    assert forbidden_in(source), f"the guard misses: {source!r}"


def test_the_guard_does_not_fire_on_what_training_legitimately_imports() -> None:
    """Over-broad is its own failure: a guard that flags ``torch`` teaches people
    to delete it."""
    assert forbidden_in("import torch\nfrom transformers import Trainer") == []
    assert forbidden_in("from .dataset import LABELS") == []


def test_training_cannot_reach_the_database() -> None:
    """ADR 0003 and 0006: a user's corrections never become training labels.

    The rule is easiest to keep by making the code unable to break it. Nothing
    under ``training`` may import this module's ORM models, its service layer, a
    session, or SQLAlchemy -- with no path to ``ext_action_items`` there is
    nothing left to decide at review time.

    ``rglob`` rather than ``glob``: a subpackage added later is still inside the
    rule, and a directory is the obvious place for the import to end up.
    """
    package = Path(training.__file__).parent

    offenders = [
        f"{path.name}: {name}"
        for path in sorted(package.rglob("*.py"))
        for name in forbidden_in(path.read_text(encoding="utf-8"))
    ]

    assert not offenders, (
        f"training imports {offenders}. ADR 0003 and 0006 keep user corrections "
        "out of training labels; this package has no reason to reach the database."
    )
