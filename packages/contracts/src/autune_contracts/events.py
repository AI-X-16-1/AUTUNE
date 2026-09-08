"""Celery event names.

The only runtime coupling permitted between modules. Import these constants
rather than typing the strings — a typo here fails silently at runtime.
See docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

from typing import Final

TRANSCRIPT_READY: Final = "autune.transcript.ready"
EXTRACTION_COMPLETED: Final = "autune.extraction.completed"
GAP_COMPLETED: Final = "autune.gap.completed"
CONTEXT_COMPLETED: Final = "autune.context.completed"
INTELLIGENCE_COMPLETED: Final = "autune.intelligence.completed"

MODULES: Final = ("audio", "extraction", "gap", "context", "intelligence")
"""Registration order for apps/api and apps/worker. Nothing else reads this."""
