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

TERMINAL_EVENTS: Final = (INTELLIGENCE_COMPLETED,)
"""Events nothing consumes, on purpose.

The pipeline ends at E, so `autune.intelligence.completed` reaches no task and
that is the design rather than a mistake. `publish` needs to know which is which:
without this list, "nobody is listening" is one message for a normal end of a
meeting and for a consumer whose task name has a typo in it, and the first one
happens on every meeting. A warning that fires on the normal path is a warning
nobody reads, and it was the only signal the second case had.

Declared here rather than in the worker's tests, which is where it started: a
list that decides a log level in production cannot live in a test.
"""

MODULES: Final = ("audio", "extraction", "gap", "context", "intelligence")
"""Registration order for apps/api and apps/worker. Nothing else reads this."""
