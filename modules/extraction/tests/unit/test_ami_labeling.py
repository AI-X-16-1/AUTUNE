"""The AMI mapping, and what it deliberately refuses to label.

No corpus needed. Every rule here is a fact about the mapping, so the definitions
can be argued with in review without anyone downloading 207MB first.
"""

from __future__ import annotations

import inspect

import pytest

from autune_contracts.enums import UtteranceKind
from autune_extraction.labeling import ami
from autune_extraction.labeling.ami import (
    ADJACENCY_PAIRS,
    ASSERTIVE_ACTS,
    DIALOGUE_ACTS,
    EXCLUDED_ACTS,
    PRECEDENCE,
    Evidence,
    Label,
    label_for,
)

# --- what the corpus is allowed to say --------------------------------------


def test_the_five_kinds_are_all_reachable() -> None:
    """A class no layer can produce would train on nothing and score zero.

    ADR 0006 makes five-way macro F1 the metric, and macro averaging weights an
    unreachable class the same as the others.
    """
    reachable = {label.kind for label in _label_everything()}

    assert reachable == set(UtteranceKind)


def test_most_utterances_get_no_label() -> None:
    """Silence is the common case and it has to stay expressible.

    A meeting is mostly none of these five things. A loader that forced a label
    on every utterance would teach the model that everything is a commitment.
    """
    assert label_for(Evidence()) is None
    assert label_for(Evidence(dialogue_act="Inform")) is None
    assert label_for(Evidence(adjacency_pair_type="apt_1")) is None


def test_plain_agreement_is_not_a_commitment() -> None:
    """POS is the failure the confirmation DM exists to catch.

    Someone saying "네" is agreement, not a promise. Mapping apt_1 to commitment
    would train the model to make exactly the mistake ADR 0006 asks us to find.
    """
    assert "apt_1" not in ADJACENCY_PAIRS
    assert label_for(Evidence(adjacency_pair_type="apt_1")) is None


def test_suggest_is_not_folded_into_commitment() -> None:
    """A proposal is not a promise, and there are six times as many of them."""
    assert "Suggest" not in DIALOGUE_ACTS
    assert "Suggest" in EXCLUDED_ACTS
    assert label_for(Evidence(dialogue_act="Suggest")) is None


def test_concern_does_not_come_from_an_act() -> None:
    """The act inventory has no polarity, so concern is keyed on the pairs.

    ``Assess`` is the largest task act and covers both "that works" and "that
    will never work". Mapping it either way would be a coin flip on the class
    that feeds gap detection.
    """
    assert UtteranceKind.CONCERN not in DIALOGUE_ACTS.values()
    assert ADJACENCY_PAIRS["apt_2"] is UtteranceKind.CONCERN
    assert "Assess" in EXCLUDED_ACTS


def test_every_unmapped_act_says_why() -> None:
    """An act left out silently reads as an oversight a year from now."""
    assert not (DIALOGUE_ACTS.keys() & EXCLUDED_ACTS.keys())
    assert all(reason.strip() for reason in EXCLUDED_ACTS.values())


def test_all_sixteen_ami_acts_are_accounted_for() -> None:
    """AMI's ontology has sixteen leaf acts; every one is mapped or excluded.

    ``Unlab`` was missing until the corpus pass refused to run. Nothing failed
    before that — an unrecognised act simply produced no label, which is
    indistinguishable from an act somebody decided not to use.
    """
    assert len(DIALOGUE_ACTS) + len(EXCLUDED_ACTS) == 16


def test_all_four_elicit_acts_are_one_class() -> None:
    elicits = {act for act in DIALOGUE_ACTS if act.startswith("Elicit-")}

    assert len(elicits) == 4
    assert {DIALOGUE_ACTS[act] for act in elicits} == {UtteranceKind.OPEN_QUESTION}


# --- what happens when the layers disagree ----------------------------------


def test_precedence_covers_every_kind() -> None:
    """A kind missing from the ordering would raise on the first conflict."""
    assert set(PRECEDENCE) == set(UtteranceKind)
    assert len(PRECEDENCE) == len(set(PRECEDENCE))


def test_a_hesitant_question_stays_a_question() -> None:
    """406 cases in AMI, the largest single rule, and why the ordering changed.

    An ``ambiguous`` label triggers a DM asking the speaker whether they meant to
    commit. "Do we need an LCD display?" is a question — the person was not
    assenting at all, and asking them to confirm a promise they never made is not
    a near miss. Measured by ``scripts/ami_label_conflicts.py``.
    """
    label = label_for(Evidence(dialogue_act="Elicit-Assessment", adjacency_pair_type="apt_3"))

    assert label is not None
    assert label.kind is UtteranceKind.OPEN_QUESTION
    assert UtteranceKind.AMBIGUOUS in label.overruled


def test_an_uncertain_offer_is_ambiguous_not_a_commitment() -> None:
    """ "한번 볼게요" — the shape of a commitment without the substance.

    Letting the act win would train the model to promote weak assent to a
    promise, which is the error the NLI verification step and the confirmation DM
    both exist to undo.

    Only 5 cases in AMI, and structurally so: polarity is a property of a
    response and an ``Offer`` is an initiating move, so the two rarely land on
    one utterance. Right where it fires, and nothing should be tuned on it.
    """
    label = label_for(Evidence(dialogue_act="Offer", adjacency_pair_type="apt_3"))

    assert label is not None
    assert label.kind is UtteranceKind.AMBIGUOUS
    assert UtteranceKind.COMMITMENT in label.overruled
    assert label.is_contested


def test_an_uncertain_objection_is_a_concern() -> None:
    """Asking "did you mean to commit?" about a stated objection is worse than
    silence — it was not assent at all."""
    label = label_for(Evidence(dialogue_act="Offer", adjacency_pair_type="apt_2"))

    assert label is not None
    assert label.kind is UtteranceKind.CONCERN


def test_a_decision_span_outranks_everything_it_can_promote() -> None:
    """The most specific human judgement in the corpus, and the field D keys on."""
    label = label_for(
        Evidence(in_decision_span=True, dialogue_act="Offer", adjacency_pair_type="apt_2")
    )

    assert label is not None
    assert label.kind is UtteranceKind.DECISION
    assert label.overruled == (UtteranceKind.CONCERN, UtteranceKind.COMMITMENT)


def test_filler_inside_a_decision_span_is_not_a_decision() -> None:
    """A span is a region, not an utterance, and "Hmm." is filler wherever it sits.

    Promoting everything inside the region produced 9,835 decision labels from
    288 annotated decisions, a third of them acts ``EXCLUDED_ACTS`` had already
    refused by name: 1,331 Fragments, 1,271 Backchannels, 730 Stalls. The model
    would have learned that "Yeah." is where a meeting settles something.
    """
    for filler in ("Backchannel", "Stall", "Fragment"):
        assert label_for(Evidence(in_decision_span=True, dialogue_act=filler)) is None


def test_a_question_inside_a_decision_span_stays_a_question() -> None:
    """``Elicit-*`` asks rather than asserts, so the region cannot promote it.

    It costs no decision entity either: ``group_decisions`` spans a gap of two
    and absorbs the question into the run around it.
    """
    label = label_for(Evidence(in_decision_span=True, dialogue_act="Elicit-Inform"))

    assert label is not None
    assert label.kind is UtteranceKind.OPEN_QUESTION


def test_only_assertive_acts_can_carry_a_decision() -> None:
    """The four that put something on the record, and no others."""
    assert {"Inform", "Assess", "Suggest", "Offer"} == ASSERTIVE_ACTS
    assert set(DIALOGUE_ACTS) | set(EXCLUDED_ACTS) >= ASSERTIVE_ACTS


def test_an_assertive_act_outside_a_span_is_not_a_decision() -> None:
    """Membership is required in addition to the region, never instead of it."""
    assert label_for(Evidence(dialogue_act="Inform")) is None
    assert label_for(Evidence(dialogue_act="Assess")) is None


def test_an_uncontested_label_says_so() -> None:
    """Almost every labelled utterance takes this path."""
    label = label_for(Evidence(dialogue_act="Offer"))

    assert label is not None
    assert label.kind is UtteranceKind.COMMITMENT
    assert label.overruled == ()
    assert not label.is_contested


def test_the_label_records_which_layer_produced_it() -> None:
    """A corpus pass that cannot say where a label came from cannot be audited."""
    assert label_for(Evidence(dialogue_act="Offer")).source == "act:Offer"  # type: ignore[union-attr]
    assert label_for(Evidence(adjacency_pair_type="apt_2")).source == "pair:apt_2"  # type: ignore[union-attr]
    span = Evidence(in_decision_span=True, dialogue_act="Inform")
    assert label_for(span).source == "decision"  # type: ignore[union-attr]


def test_overruled_is_ordered_by_precedence() -> None:
    """So a conflict tally reads the same way every time it is printed."""
    label = label_for(
        Evidence(in_decision_span=True, dialogue_act="Inform", adjacency_pair_type="apt_3")
    )

    assert label is not None
    assert label.overruled == (UtteranceKind.AMBIGUOUS,)


# --- the mapping is data, not a mutable global ------------------------------


@pytest.mark.parametrize("mapping", [DIALOGUE_ACTS, ADJACENCY_PAIRS, EXCLUDED_ACTS])
def test_the_mapping_cannot_be_edited_at_runtime(mapping: object) -> None:
    """These define the training set. A caller mutating one would silently change
    what the next model learned, with nothing in the diff to show it."""
    with pytest.raises(TypeError):
        mapping["Offer"] = UtteranceKind.CONCERN  # type: ignore[index]


def _label_everything() -> list[Label]:
    """Every label the mapping can produce, one layer at a time."""
    evidence = [Evidence(in_decision_span=True, dialogue_act="Inform")]
    evidence += [Evidence(dialogue_act=act) for act in DIALOGUE_ACTS]
    evidence += [Evidence(adjacency_pair_type=pair) for pair in ADJACENCY_PAIRS]
    return [label for item in evidence if (label := label_for(item)) is not None]


# --- the numbers in the prose are the numbers ------------------------------


MEASURED = {
    "labelled": "17,876",
    "contested": "809",
    "open_question over ambiguous": "406",
    "decision total": "365",
    "ambiguous over commitment": "5",
}
"""What ``scripts/ami_label_conflicts.py`` reports on AMI 1.6.2.

Not a claim about the model — a claim about what the prose beside ``PRECEDENCE``
says, which is the only thing a reader has to go on when deciding whether the
ordering was chosen or guessed.
"""


def test_the_reasoning_beside_precedence_quotes_the_current_numbers() -> None:
    """The counts moved when ``ASSERTIVE_ACTS`` landed and the docstring did not.

    It kept 21,601 / 1,439 / 362 from before the gate, and said ``decision`` was
    "most of the disagreement" — which the gate had made false, in the same
    commit that made it false. A file whose whole purpose is keeping the reason
    next to the constant went stale internally, and a human reviewer caught it
    rather than a test.
    """
    source = inspect.getsource(ami)

    for label, number in MEASURED.items():
        assert number in source, f"{label} ({number}) is missing from ami.py"

    for stale in ("21,601", "1,439", "362 cases", "31 cases"):
        # One deliberate mention survives: the docstring says what it used to
        # claim, so the correction is legible rather than silent.
        assert source.count(stale) <= 1, f"{stale} appears more than once"


def test_the_share_decision_wins_is_stated_and_current() -> None:
    """``decision`` is 365 of 809 — 45%, and no longer the largest rule.

    The prose said it was "most of the disagreement", which was true until
    ``ASSERTIVE_ACTS`` removed 606 conflicts it had been winning over ``Elicit-*``
    acts it should never have promoted.

    Checked as "the current share is stated" rather than "the old phrase is
    absent": the old phrase is quoted a few lines up, on purpose, so the
    correction is legible instead of silent. A test that forbids a string
    forbids the sentence explaining why it was wrong.
    """
    source = inspect.getsource(ami)

    assert "365 of the 809" in source
    assert "45%, not most" in source
