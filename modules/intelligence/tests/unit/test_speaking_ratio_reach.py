"""Where E may turn a transcript's timings into a per-person speech volume (#371).

docs/architecture/privacy.md section 3 (#361): a transcript may say who spoke
and when, but no module may aggregate those timings into one person's speech
volume except to deliver it to that speaker. E does exactly that in two places
-- ``speaking_ratio_for_user`` (the person's own ``/me`` route) and
``send_personal_feedback`` (the person's own DM) -- through
``compute_speaking_shares``.

Speaker identification (#370) is what made this computable at all. This test
reads E's source and fails when anything else reaches the share computation or
the utterance timings: a new read path, a score feature, an export. Moving a
call into one of the two places is the review it asks for, not a fix to make it
pass.
"""

from __future__ import annotations

import ast
from pathlib import Path

import autune_intelligence

PACKAGE = Path(autune_intelligence.__file__).parent

SHARES = {"compute_speaking_shares", "speaking_shares"}
"""The computations of one person's share of a meeting's speech."""

TIMINGS = {"start_sec", "end_sec"}
"""Utterance timings: summed per speaker, they are that speaker's volume."""

ALLOWED = {
    # The two deliveries to the speaker themselves.
    ("service.py", "speaking_ratio_for_user"),
    ("service.py", "send_personal_feedback"),
    # The read that builds the segments for them, and the arithmetic itself.
    ("service.py", "compute_speaking_shares"),
    ("speaking.py", "speaking_shares"),
    ("speaking.py", "SpeechSegment"),
    ("speaking.py", "duration"),
}


def _uses(names: set[str]) -> set[tuple[str, str]]:
    """``(file, enclosing function or class)`` for every reference to one of
    ``names``: a name, an attribute, a keyword argument or a definition."""
    found: set[tuple[str, str]] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(PACKAGE).as_posix()

        def visit(node: ast.AST, where: str, rel: str = rel) -> None:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                where = node.name
            name = (
                node.id
                if isinstance(node, ast.Name)
                else node.attr
                if isinstance(node, ast.Attribute)
                else node.arg
                if isinstance(node, ast.keyword)
                else node.name
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                else None
            )
            if name in names:
                found.add((rel, where))
            for child in ast.iter_child_nodes(node):
                visit(child, where)

        visit(tree, "<module>")
    return found


def test_only_the_deliveries_to_the_speaker_compute_a_share() -> None:
    reached = _uses(SHARES)

    assert reached - ALLOWED == set(), "a share computed outside its delivery (privacy.md 3)"
    # The deliveries are still there: a renamed one would leave this test empty.
    assert {
        ("service.py", "speaking_ratio_for_user"),
        ("service.py", "send_personal_feedback"),
    } <= reached


def test_only_the_share_computation_reads_utterance_timings() -> None:
    reached = _uses(TIMINGS)

    assert reached - ALLOWED == set(), "utterance timings read outside the share computation"
    assert ("service.py", "compute_speaking_shares") in reached
