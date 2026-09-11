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

EVENTS: Final = (
    TRANSCRIPT_READY,
    EXTRACTION_COMPLETED,
    GAP_COMPLETED,
    CONTEXT_COMPLETED,
    INTELLIGENCE_COMPLETED,
)
"""Every event the pipeline publishes.

``autune_core.publish`` refuses a name that is not in here, so a producer that
types the string by hand fails at the call site instead of sending into a
suffix nobody registered. ``apps/worker`` walks it to check that each one has
the subscribers it should.

Additive only, like everything in this package: appending an event is fine,
renaming one changes a task name in somebody else's module.
"""

MODULES: Final = ("audio", "extraction", "gap", "context", "intelligence")
"""Registration order for apps/api and apps/worker. Nothing else reads this."""
