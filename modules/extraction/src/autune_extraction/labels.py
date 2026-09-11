"""The one label module B uses that the contract does not have.

``UtteranceKind`` lists the five kinds of utterance the product acts on. Most of
a meeting is none of them: 84.8% of AMI's dialogue acts map to no kind (#149),
and a classifier that cannot say so gives every utterance a kind anyway. Measured
on AMI with the unlabelled acts left in, that was 1,888 false labels in a
2,400-utterance meeting, half of them ``decision``. ``none`` is how the model,
the training data and the evaluation set say "none of these".

It never leaves module B. The contract has no ``none`` and must not grow one: an
utterance that is none of the kinds is simply absent from
``ExtractionResult.classifications``. Inside the pipeline it is
``Prediction.kind is None``, so turning one into a contract ``Classification``
is a type error rather than something a reviewer has to notice.
"""

from __future__ import annotations

from typing import Final

from autune_contracts.enums import UtteranceKind

NONE: Final = "none"


def kind_or_none(value: str) -> UtteranceKind | None:
    """Read a label as a file stores it: a kind, or ``none``.

    Raises ``ValueError`` on anything else, as ``UtteranceKind(value)`` does, so
    a misspelt label stops a load instead of quietly becoming "none of these".
    """
    return None if value == NONE else UtteranceKind(value)
