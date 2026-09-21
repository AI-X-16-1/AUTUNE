"""The web feature's hand-written mirror of this module's read model.

``apps/web/src/features/actions/types.ts`` types what ``/api/extraction``
returns. The contract part (``ActionItem``) is generated; what this module adds
on top is not, because no generator reads a module's own response bodies. This
file is the tripwire: a field added to or removed from the Python side fails
here, naming the TypeScript file that has to follow.
"""

from __future__ import annotations

import re
from pathlib import Path

from autune_contracts.extraction import ActionItem
from autune_extraction.schemas import ActionItemDetail, ActionItemRead, SourceUtterance

TYPES_TS = Path(__file__).resolve().parents[4] / "apps/web/src/features/actions/types.ts"


def ts_fields(interface: str) -> set[str]:
    """Field names declared directly in one TypeScript interface of types.ts."""
    source = TYPES_TS.read_text(encoding="utf-8")
    match = re.search(rf"export interface {interface}\b[^{{]*\{{(?P<body>.*?)\n\}}", source, re.S)
    assert match, f"{interface} is not declared in {TYPES_TS}"
    body = re.sub(r"/\*.*?\*/", "", match["body"], flags=re.S)
    return set(re.findall(r"^\s*(\w+)\??:", body, flags=re.M))


def test_the_web_read_model_mirror_is_current() -> None:
    """What ``ActionItemRead`` adds to the contract is what the web declares."""
    added = set(ActionItemRead.model_fields) - set(ActionItem.model_fields)

    assert added == {"meeting_id", "origin", "is_candidate", "assignee_name"}
    assert ts_fields("ActionItemRead") == added, f"update {TYPES_TS}"


def test_the_web_detail_mirror_is_current() -> None:
    assert set(ActionItemDetail.model_fields) - set(ActionItemRead.model_fields) == {"sources"}
    assert ts_fields("ActionItemDetail") == {"sources"}, f"update {TYPES_TS}"
    assert ts_fields("SourceUtterance") == set(SourceUtterance.model_fields), f"update {TYPES_TS}"
