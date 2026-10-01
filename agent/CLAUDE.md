# CLAUDE.md — the agent layer

Read the root `CLAUDE.md` first. Design: `docs/architecture/agent-layer.md`.
Placement: ADR 0010 (`Accepted`; #260 closed 2026-09-29).

## Who owns what

| Path | Owner |
| --- | --- |
| `src/autune_agent/main/`, `results.py`, `testing.py`, `models.py`, `router.py`, `config.py`, `migrations/` | 김민경 |
| `src/autune_agent/subagents/research/` | 김민경 |
| `src/autune_agent/subagents/briefing/` | 문민재 |
| `src/autune_agent/subagents/followup/` | 박재경 |
| `src/autune_agent/subagents/workload/` | 강민구 |
| `src/autune_agent/subagents/report/` | 이승환 |

Stay in your own `subagents/<name>/`. A change you need in `main/` is an issue
to its owner.

## Rules that break the build or the law

1. **A subagent never imports another subagent.** import-linter enforces it.
   Delegation goes through the main agent.
2. **Nothing below imports `autune_agent`.** Modules expose `tools.py` returning
   plain dicts; the registry validates them into `ToolResult`.
3. **Read through the `Toolbox` only.** It holds your allow-list and the run's
   budget. import-linter refuses a subagent importing a module.
4. **Never call a write yourself.** Return `ProposedAction`s. The main agent
   runs L1 at the end of the run (`main/actions.py`) and puts L2 through plan
   mode. The level that counts is the one the owning module declared
   (`L1_ACTIONS` in its `tools.py`); a proposal cannot demote a write.
   An L2 proposal's arguments are ids and short scalars under short lowercase keys; store text in your own table first and pass its id.
5. **No personal-only tool anywhere in the layer.** A module declares a
   speaking-ratio read in `PERSONAL_ONLY_TOOLS` and the registry never loads
   it; `Subagent` also refuses the name (invariant 11).
6. **No LangGraph checkpointer, no LangChain tool that reaches outside, no
   LangSmith tracing.** Every outbound call goes through `packages/integrations`
   and its privacy guard. The graph refuses to build with
   `LANGSMITH_TRACING` or `LANGCHAIN_TRACING_V2` on.
7. **Return at most five items, evidence as ids.** `ToolResult` cuts and
   refuses the rest.
8. **A stored row keeps no meeting text at all.** `agent_runs` stores tool
   names, evidence ids, and what ran -- never the answer, a proposal's title,
   body or arguments, even for a run about a meeting, because that answer may
   quote another meeting the cascade would miss (`main/store.py`).

## Starting a subagent

Copy the shape of `autune_agent.testing.example_subagent`, export it as
`SUBAGENT` from your package, and test it with `mock_tool` and `FakeRouter` —
see `tests/test_main_graph.py`. You do not need anyone's module to be ready.

To be woken by the pipeline rather than a chat message, list the events in
`Subagent.triggers` (`TRIGGER_EVENTS`: `autune.transcript.ready`,
`autune.intelligence.completed`). Your run then gets the event name as its
`request` and the meeting in its scope, so every tool and action you name is
already bound to that meeting. Do not write a Celery task.
