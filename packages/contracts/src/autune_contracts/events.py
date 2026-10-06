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
EXTRACTION_AGENDA_CHANGED: Final = "autune.extraction.agenda_changed"
"""B -> D. A team's open Jira issues, republished every five minutes, changed or
not -- the name says what it carries, not that something changed; do not wait
for a change to arrive. The payload is a ``TeamAgenda``, stale after
``AGENDA_STALE_AFTER``. About a team, not a meeting, so it is outside the
per-meeting pipeline (#436)."""
EXTRACTION_ACTION_PROGRESS: Final = "autune.extraction.action_progress"
"""B -> E. A team's action-item counts per meeting, republished every
``ACTION_PROGRESS_PUBLISH_EVERY`` whether or not anything changed. The payload
is a ``TeamActionProgress``; keep the latest ``as_of`` and treat one older than
``ACTION_PROGRESS_STALE_AFTER`` as unknown. About a team, not a meeting (#605)."""
INTELLIGENCE_MEETING_REPORT_CHANGED: Final = "autune.intelligence.meeting_report_changed"
"""E -> the agent layer. A person saved an edited meeting-report draft or a
correction to a posted report on the dashboard card, and it waits for L2
approval before it reaches the team channel (#674). The payload is a plain
``Payload``: the meeting id and nothing else. What waits to be posted is read
through E's tools when the event is handled, because an id carried here could
already be stale by then."""
INTELLIGENCE_MEETING_REPORT_POSTED: Final = "autune.intelligence.meeting_report_posted"
"""E -> C. A meeting's report went out to the team channel; the payload is a
``MeetingReportPosted`` (channel and the message ts), so C's question cards can
reply in that thread rather than post apart (#824). Sent once per report."""

EVENTS: Final = (
    TRANSCRIPT_READY,
    EXTRACTION_COMPLETED,
    GAP_COMPLETED,
    CONTEXT_COMPLETED,
    INTELLIGENCE_COMPLETED,
    EXTRACTION_AGENDA_CHANGED,
    EXTRACTION_ACTION_PROGRESS,
    INTELLIGENCE_MEETING_REPORT_CHANGED,
    INTELLIGENCE_MEETING_REPORT_POSTED,
)
"""Every event the pipeline publishes.

``autune_core.publish`` refuses a name that is not in here, so a producer that
types the string by hand fails at the call site instead of sending into a
suffix nobody registered. ``apps/worker`` walks it to check that each one has
the subscribers it should.

Additive only, like everything in this package: appending an event is fine,
renaming one changes a task name in somebody else's module.
"""

TERMINAL_EVENTS: Final = (
    INTELLIGENCE_COMPLETED,
    INTELLIGENCE_MEETING_REPORT_CHANGED,
    INTELLIGENCE_MEETING_REPORT_POSTED,
)
"""Events that may reach no task, on purpose.

The module pipeline ends at E: no module consumes `autune.intelligence.completed`.
The agent layer does when it is loaded (`autune.agent.on_intelligence_completed`,
#509), so the event is no longer unconsumed everywhere -- but a process without
the layer still publishes it to nobody, and that is the design rather than a
mistake. `publish` needs to know which is which:
without this list, "nobody is listening" is one message for a normal end of a
meeting and for a consumer whose task name has a typo in it, and the first one
happens on every meeting. A warning that fires on the normal path is a warning
nobody reads, and it was the only signal the second case had.

`autune.intelligence.meeting_report_changed` is the same case (#674). Only the
agent layer consumes it, and E publishes it from the API process, where the
layer's tasks may not be registered.

`autune.intelligence.meeting_report_posted` is here until C's
``on_intelligence_meeting_report_posted`` lands (#824); C may also keep posting
apart from the thread. Take it out when C subscribes.

Declared here rather than in the worker's tests, which is where it started: a
list that decides a log level in production cannot live in a test.
"""

MODULES: Final = ("audio", "extraction", "gap", "context", "intelligence")
"""Registration order for apps/api and apps/worker. Nothing else reads this."""
