"""Every module imports first, on its own.

``template`` imports ``pipeline.base``, and ``pipeline`` imports the verifier,
which reads ``verification``, ``semantic`` and ``template`` for its types. A
runtime import anywhere on that path closes a cycle, and the test suite never
sees it: by the time a test runs, something else has already imported the
package in an order that works. ``python -m autune_gap.eval`` imported
``template`` first and failed.

Each case imports one module in a fresh interpreter. In-process re-imports
cannot stand in for that: ``models`` registers its tables on the shared
SQLAlchemy metadata, and importing it twice in one process is an error of its
own.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

ENTRY_POINTS = (
    "autune_gap.template",
    "autune_gap.semantic",
    "autune_gap.verification",
    "autune_gap.pipeline",
    "autune_gap.pipeline.verifier",
    "autune_gap.detect",
    "autune_gap.service",
    "autune_gap.tasks",
    "autune_gap.eval.__main__",
)


@pytest.mark.parametrize("module", ENTRY_POINTS)
def test_the_module_imports_first(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stderr.strip().splitlines()[-1:]
