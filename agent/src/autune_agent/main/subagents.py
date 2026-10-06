"""How a subagent plugs into the main agent (agent-layer.md sections 3.1 and 3.2).

Each ``autune_agent.subagents.<name>`` package exports ``SUBAGENT``. The main
agent collects them by iterating ``SUBAGENT_NAMES``; a package that does not
define ``SUBAGENT`` yet is skipped, so an owner can merge their folder before
their subagent works.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from importlib import import_module
from typing import Any, Literal, Protocol, TypedDict

from autune_agent.results import SubagentResult
from autune_contracts import (
    INTELLIGENCE_COMPLETED,
    INTELLIGENCE_MEETING_REPORT_CHANGED,
    TRANSCRIPT_READY,
)

from .registry import Toolbox, is_personal_only

SUBAGENT_NAMES = ("research", "briefing", "followup", "workload", "report")

TRIGGER_EVENTS = (TRANSCRIPT_READY, INTELLIGENCE_COMPLETED, INTELLIGENCE_MEETING_REPORT_CHANGED)
"""The pipeline events the main agent listens to on everyone's behalf -- section
6's event rows. ``autune_agent.tasks`` has one task per entry, and a test
holds the two lists together; a subagent may name only these.

A subagent that reads B's, C's or D's results wakes on
``INTELLIGENCE_COMPLETED``. ``TRANSCRIPT_READY`` reaches those modules at the
same moment it reaches this layer, so their results do not exist yet.

``INTELLIGENCE_MEETING_REPORT_CHANGED`` is E's: a person edited a report on the
dashboard card, and the edit waits for L2 approval instead of being posted
(#674). Report wakes on it; a run supersedes the proposal an earlier run left
for the same meeting, so only the edited report waits on ``/approvals``."""

PERIODIC_TICK = timedelta(hours=1)
"""How often ``autune.agent.periodic.wake_subagents`` looks for a due
subagent, so the finest period a ``Periodic`` trigger can ask for."""


@dataclass(frozen=True)
class Periodic:
    """Wake this subagent on a timer, once per team that has members (#634).

    agent-layer.md section 3.1 gives Workload and Follow-up "state,
    @periodic": what they judge changes between meetings -- an action item is
    confirmed in the app, and the API process has no Celery app to publish an
    event about it (#170) -- so they are woken by time, not by the pipeline.
    One beat task serves every subagent that declares one
    (``triggers.on_tick``); a subagent never registers a task of its own.

    A periodic run is about the team, not a meeting: ``meeting_id`` is NULL
    and the run's ``request`` is ``PERIODIC_REQUEST``. A subagent woken this
    way should also declare ``proposals_per="team"``, or each run leaves one
    more pending proposal beside the last.
    """

    hours: int

    def __post_init__(self) -> None:
        if self.hours < 1:
            raise ValueError("a periodic trigger fires at most hourly (PERIODIC_TICK)")

    @property
    def every(self) -> timedelta:
        return timedelta(hours=self.hours)


PERIODIC_REQUEST = "agent.periodic"
"""What a periodic run's ``request`` says, the way an event-woken run's says
the event name."""
"""Owners in agent-layer.md section 3.1 and CODEOWNERS."""


class SubagentState(TypedDict, total=False):
    """The subgraph's state. The main agent writes ``request``; the subagent
    must leave ``outcome``. Anything else it keeps is its own business."""

    request: str
    outcome: SubagentResult


class CompiledSubagent(Protocol):
    """What ``StateGraph(SubagentState).compile()`` returns, as far as we use it."""

    def invoke(self, state: SubagentState, /) -> Any: ...


@dataclass(frozen=True)
class Subagent:
    name: str
    description: str
    """What the main agent routes on. Say when to use it first, like a tool docstring."""
    tools: tuple[str, ...]
    """Registry names this subagent may call, and the only ones it will see."""
    build: Callable[[Toolbox], CompiledSubagent]
    """Given its toolbox, return the compiled subgraph."""
    triggers: tuple[str | Periodic, ...] = ()
    """Pipeline events that wake this subagent (agent-layer.md section 6), from
    ``TRIGGER_EVENTS``, and at most one ``Periodic``. The main agent subscribes once for everyone
    (``autune_agent.tasks``); a subagent never registers a task of its own. A
    woken run's ``request`` is the event name, and its scope carries the
    meeting the event was about."""
    proposals_per: Literal["meeting", "team"] = "meeting"
    """What one of its L2 proposals is about, so what a newer run replaces
    (``pending.queue_l2``). ``"meeting"``: a run supersedes the pending
    proposals an earlier run made about the same meeting, and a run about no
    meeting supersedes nothing. ``"team"``: the proposals judge the whole team
    -- Workload weighs everyone's open items -- so a run supersedes the team's
    earlier pending proposals from this subagent whatever meeting, if any,
    woke it (#631). Otherwise each meeting's run leaves another proposal
    moving the same item to someone else, and approving both lets the later
    approval win."""

    answers_lookups: bool = False
    """Whether a request that only looks something up may come here (#879).

    Off, the router sends a plain lookup to the ask loop, as before #879: a
    subagent that writes or proposes (Research drafts a document, Workload
    proposes moves) must not be woken by a question. On, a lookup its
    description covers comes here -- for a subagent with reads the ask loop
    does not hold (Report and module E)."""

    def __post_init__(self) -> None:
        # The registry never holds a declared personal-only tool; this refuses
        # the name as well, so the mistake surfaces where it was written.
        personal = [tool for tool in self.tools if is_personal_only(tool)]
        if personal:
            raise ValueError(f"subagent {self.name} may not read personal-only tools: {personal}")
        unknown = [
            t for t in self.triggers if not isinstance(t, Periodic) and t not in TRIGGER_EVENTS
        ]
        if unknown:
            raise ValueError(f"subagent {self.name} names events nothing listens to: {unknown}")
        if sum(isinstance(t, Periodic) for t in self.triggers) > 1:
            raise ValueError(f"subagent {self.name} declares more than one period")

    @property
    def period(self) -> Periodic | None:
        """Its ``Periodic`` trigger, if it has one."""
        return next((t for t in self.triggers if isinstance(t, Periodic)), None)


def collect_subagents(names: Iterable[str] = SUBAGENT_NAMES) -> dict[str, Subagent]:
    found: dict[str, Subagent] = {}
    for name in names:
        package = import_module(f"autune_agent.subagents.{name}")
        subagent = getattr(package, "SUBAGENT", None)
        if subagent is None:
            continue
        if subagent.name != name:
            raise ValueError(f"autune_agent.subagents.{name} exports {subagent.name!r}")
        found[name] = subagent
    return found
