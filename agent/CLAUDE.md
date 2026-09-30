# CLAUDE.md — the agent layer

Read the root `CLAUDE.md` first. Design: `docs/architecture/agent-layer.md`.
Placement: ADR 0010. This layer is code only once #260 is decided and ADR 0010
is `Accepted`; until then the root `CLAUDE.md` invariants apply unchanged.

## Who owns what

| Path | Owner |
| --- | --- |
| `src/autune_agent/main/`, `results.py`, `testing.py` | 김민경 |
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
4. **Never call a write yourself.** Return `ProposedAction`s; the main agent
   puts L2 through plan mode.
5. **No personal-only tool anywhere in the layer.** A module declares a
   speaking-ratio read in `PERSONAL_ONLY_TOOLS` and the registry never loads
   it; `Subagent` also refuses the name (invariant 11).
6. **No LangGraph checkpointer, no LangChain tool that reaches outside, no
   LangSmith tracing.** Every outbound call goes through `packages/integrations`
   and its privacy guard. The graph refuses to build with
   `LANGSMITH_TRACING` or `LANGCHAIN_TRACING_V2` on.
7. **Return at most five items, evidence as ids.** `ToolResult` cuts and
   refuses the rest.

## Starting a subagent

Copy the shape of `autune_agent.testing.example_subagent`, export it as
`SUBAGENT` from your package, and test it with `mock_tool` and `FakeRouter` —
see `tests/test_main_graph.py`. You do not need anyone's module to be ready.
