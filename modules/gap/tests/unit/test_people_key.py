"""The digest ``gap_scorings`` compares, and the grouping it is taken over.

Pure functions: the rescore (#415) decides a meeting is stale by comparing two
of these, so what moves the digest and what does not is the whole behaviour.
"""

from __future__ import annotations

from autune_gap.service import _group_people, _people_key

SPLIT = [("par_1", None), ("par_2", None)]
MERGED = [("par_1", "usr_a"), ("par_2", "usr_a")]


def test_two_labels_confirmed_as_one_user_are_one_person() -> None:
    assert _group_people(MERGED) == {"par_1": "par_1", "par_2": "par_1"}


def test_a_confirmation_moves_the_digest() -> None:
    assert _people_key(_group_people(SPLIT)) != _people_key(_group_people(MERGED))


def test_naming_a_speaker_who_stays_one_person_does_not_move_it() -> None:
    """A user id on a label that merges with nobody changes no silent share,
    so it is no reason to rescore."""
    named = [("par_1", "usr_a"), ("par_2", None)]

    assert _people_key(_group_people(SPLIT)) == _people_key(_group_people(named))


def test_the_order_rows_come_back_in_does_not_move_it() -> None:
    assert _people_key(_group_people(MERGED)) == _people_key(_group_people(MERGED[::-1]))


def test_a_withdrawal_moves_it() -> None:
    """``_people`` reads consenting rows only, so a participant who withdrew
    is a row that is no longer there."""
    assert _people_key(_group_people(SPLIT)) != _people_key(_group_people(SPLIT[:1]))
