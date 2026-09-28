"""The web feature's hand-written mirror of this module's read model.

``apps/web/src/features/gap/types.ts`` types what ``/api/gap`` returns. The
contract part (``GapReport`` and what it holds) is generated; the topic graph is
not, because no generator reads a module's own response bodies — E receives
``GapReport`` and never draws a graph, which is why that shape is not in
``packages/contracts``. This file is the tripwire: a field added to or removed
from the Python side fails here, naming the TypeScript file that has to follow.

The same shape module B uses for ``ActionItemRead``
(``modules/extraction/tests/unit/test_web_mirror.py``).
"""

from __future__ import annotations

import re
from pathlib import Path

from autune_gap.schemas import (
    TemplateComparison,
    TemplateItemRead,
    TopicEdgeRead,
    TopicGraphRead,
    TopicNodeRead,
)

TYPES_TS = Path(__file__).resolve().parents[4] / "apps/web/src/features/gap/types.ts"


def ts_fields(interface: str) -> set[str]:
    """Field names declared directly in one TypeScript interface of types.ts."""
    source = TYPES_TS.read_text(encoding="utf-8")
    match = re.search(rf"export interface {interface}\b[^{{]*\{{(?P<body>.*?)\n\}}", source, re.S)
    assert match, f"{interface} is not declared in {TYPES_TS}"
    body = re.sub(r"/\*.*?\*/", "", match["body"], flags=re.S)
    return set(re.findall(r"^\s*(\w+)\??:", body, flags=re.M))


def test_the_web_topic_graph_mirror_is_current() -> None:
    assert ts_fields("TopicGraph") == set(TopicGraphRead.model_fields), f"update {TYPES_TS}"


def test_the_web_topic_node_mirror_is_current() -> None:
    """``betweenness`` is the field that makes this shape not a contract: the
    graph carries it and ``autune_contracts.Topic`` does not."""
    assert ts_fields("TopicNode") == set(TopicNodeRead.model_fields), f"update {TYPES_TS}"
    assert "betweenness" in ts_fields("TopicNode")


def test_the_web_topic_edge_mirror_is_current() -> None:
    assert ts_fields("TopicEdge") == set(TopicEdgeRead.model_fields), f"update {TYPES_TS}"


def test_the_web_template_comparison_mirror_is_current() -> None:
    """The S20 rail. Not a contract either — a checklist is module C's own
    screen and no other module reads one."""
    assert ts_fields("TemplateComparison") == set(TemplateComparison.model_fields), (
        f"update {TYPES_TS}"
    )


def test_the_web_template_item_mirror_is_current() -> None:
    """``TemplateChecklistItem`` on the web: named apart from the contract's own
    ``TemplateItem``, which is the string on ``Gap.template_item``."""
    assert ts_fields("TemplateChecklistItem") == set(TemplateItemRead.model_fields), (
        f"update {TYPES_TS}"
    )


def test_the_rail_can_say_an_item_was_never_compared() -> None:
    """``coverage`` is nullable on both sides. A meeting with no topic graph has
    compared nothing, and a rail that rendered that as "covered" would show a
    full checklist of green dots for a meeting nobody has processed."""
    assert TemplateItemRead.model_fields["coverage"].default is None
    assert "coverage: Coverage | null" in TYPES_TS.read_text(encoding="utf-8")
