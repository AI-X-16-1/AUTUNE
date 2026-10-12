"""Module B's revisions are one line (CLAUDE.md invariant 7, docs/engineering/migrations.md).

Two branches that each add a revision onto the same parent leave the module
with two heads once both are merged; ``alembic upgrade heads`` still applies
them, in an order nobody chose. Read from the files, so no database is asked.
"""

from __future__ import annotations

import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[2] / "migrations"
REVISION = re.compile(r'^revision: str = "([0-9a-f]{12})"', re.MULTILINE)
DOWN = re.compile(r"^down_revision: [^=]+= (.+)$", re.MULTILINE)


def chain() -> dict[str, list[str]]:
    """Each revision of the module and what it revises."""
    found: dict[str, list[str]] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        revision, down = REVISION.search(text), DOWN.search(text)
        assert revision is not None and down is not None, path.name
        found[revision.group(1)] = re.findall(r'"([0-9a-f]{12})"', down.group(1).split("#")[0])
    return found


def test_the_module_has_one_root_and_one_head() -> None:
    revisions = chain()
    revised = {parent for parents in revisions.values() for parent in parents}

    assert revised <= set(revisions), "a revision revises one that is not the module's"
    assert len([r for r, parents in revisions.items() if not parents]) == 1
    assert len(set(revisions) - revised) == 1, sorted(set(revisions) - revised)
    # One line: no revision is revised by two.
    parents = [parent for found in revisions.values() for parent in found]
    assert len(parents) == len(set(parents))


def test_the_vectors_come_after_an_uploads_day() -> None:
    revisions = chain()

    assert revisions["7b3e9d2f5a61"] == ["c6e1f4a9d2b8"]
    assert revisions["c6e1f4a9d2b8"] == ["3b8f1d6a2c47"]
