"""Follow-up subagent -- 박재경 (@PARKJAEKYUNG0525). agent-layer.md section 3.1.

When progress and unresolved topics say another meeting is needed, proposes
one to an approver with scope ``followup`` only. Reads C's topic-level
aggregates only (a topic's silent_share, undismissed gaps) -- never
participation per role or per person; in a small team a role is a person
(privacy.md section 3).

Not built yet. Define ``SUBAGENT = Subagent(...)`` here when it is; until then
the main agent skips this package. ``autune_agent.testing.example_subagent``
is the shape to start from.
"""
