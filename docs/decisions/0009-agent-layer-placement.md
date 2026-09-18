# 0009. The agent layer is a top-level peer of `modules/` and `apps/`

**Status:** Proposed
**Date:** 2026-09-18
**Deciders:** 김민경 (proposer). The import-linter contracts are shared, so this
needs the whole team — see issue #260.

## Context

Issue #260 proposes an agent layer above modules A–E: the modules are exposed
as tools, an orchestrator decides which to call, and new state makes the system
able to wake itself. `../architecture/agent-layer.md` describes it.

The layer has one property the rest of the repository does not: **it must
import every module.** That is its whole job — it routes between them. And
that collides with the two rules that hold this codebase apart.

`import-linter` currently enforces three contracts, and two of them are in the
way:

| Contract | What it says |
| --- | --- |
| Modules are independent | `autune_audio`, `autune_extraction`, `autune_gap`, `autune_context`, `autune_intelligence` may not import one another |
| Packages do not depend on modules | `autune_contracts`, `autune_core`, `autune_integrations` may not import any module |
| Contracts depend on nothing of ours | `autune_contracts` imports no first-party package |

Invariant 6 adds a third constraint from the other direction: `apps/` is
assembly only — routers and tasks are discovered by iterating the module list,
and no business logic lives there.

So the layer cannot go in `packages/` without breaking a contract, and cannot
go in `apps/` without breaking an invariant. There is no existing home for it.

That is not an accident of the rules being badly drawn. It is the rules
correctly reporting that this is a new kind of thing: the first component whose
purpose is to know about all five modules at once.

## Decision

Add a top-level `agent/` directory, a peer of `modules/`, `apps/`, `packages/`
and `infra/`, as a `uv` workspace member named `autune-agent`.

```
autune/
├── packages/     contracts, core, integrations   — know nothing of modules
├── modules/      A–E                             — know nothing of each other
├── agent/        orchestrator, tools, triggers   — knows every module   ◀ new
├── apps/         api, worker, bot                — assembly only
└── infra/
```

A fourth import-linter contract is added and enforced from the same commit:

```ini
[importlinter:contract:agent-layer]
name = The agent layer imports modules; nothing imports the agent layer
type = layers
layers =
    autune_agent
    autune_audio | autune_extraction | autune_gap | autune_context | autune_intelligence
    autune_core | autune_integrations
    autune_contracts
```

Two consequences of that contract are the point of writing it down:

- **`autune_agent` may import any module.** That is the exception, and it is
  named and bounded rather than implied.
- **No module may import `autune_agent`.** Modules stay usable, testable and
  deployable with the agent layer absent. This is what keeps the fixed pipeline
  working when the agent is switched off.

`apps/api` and `apps/worker` may import `autune_agent`, as they already import
every module, and continue to do so by iteration rather than by name.

CODEOWNERS: `/agent/ @mkkim68`. The `tools.py` file in each module stays with
that module's owner.

Tables created by the layer take the `agent_` prefix, exactly as a module's do
(invariant 3). `agent_` is registered in `../architecture/data-model.md` as a
layer prefix rather than a module prefix.

## Alternatives considered

### `packages/agent/`, with an exception carved into the contract

The original proposal. Rejected because *Packages do not depend on modules* is
not a formality — it is the rule that lets `packages/core` be imported by
everything without creating a cycle. Carving a hole in it for one package makes
the contract's name false, and the next person who wants an exception has a
precedent rather than a conversation. A rule that has one exception is a rule
that is on its way to having three.

### `apps/agent/`

Cheaper than it looks: `apps/` already imports every module, so no contract
changes at all. Rejected on invariant 6. `apps/` is assembly — the reason
nobody has to read `apps/api/main.py` to understand a feature is that there is
never a feature in it. The orchestrator is the densest business logic in the
repository; putting it there would make "apps is assembly only" a sentence we
no longer mean, and that sentence is doing real work in five people's heads.

### A sixth module, `modules/agent/`

Impossible rather than unwise. *Modules are independent* would forbid the one
thing this component exists to do.

### No layer — extend module E

E already aggregates across B, C and D. Rejected: E would then import B, C and
D directly, which breaks *Modules are independent* in the most load-bearing
place in the codebase, and it makes the owner of module E the owner of the
agent as well.

## Consequences

**Easier**

- The exception is written down and machine-checked, in one contract, instead
  of living in someone's memory.
- The agent layer can be deleted, disabled or left unfinished without touching
  a module. The six-week demo has a version that does not depend on it.
- A new module needs nothing from the agent layer beyond its own `tools.py`.

**Harder**

- One more top-level directory, one more workspace member, one more line in
  every path-based config (ruff `src`, mypy `files`, pytest paths, CI).
- Reviewers must learn a fourth contract. It is stated in one place, but it is
  a fourth thing.
- `agent/` importing all five modules means its test suite pulls in every
  module's dependencies, including torch. Its unit tests must run against the
  tool registry with mock tools, not against real modules, or CI slows for
  everyone.

**Accepted costs**

- The layer is a single owner's surface for six weeks. If 김민경 is
  unavailable, the agent layer stops. This is why the fixed pipeline is kept
  and why the demo does not depend on the agent — stated as a risk in #260,
  accepted here.
- `agent_` occupies a prefix that is not a module's, which makes invariant 3's
  sentence slightly longer than it was: a prefix marks an owner, and an owner
  is now a module *or* the agent layer.

## Not decided here

Two questions in `../architecture/agent-layer.md` section 10 remain open and
are not settled by this ADR: how Celery is reached outside the worker (#258,
#207, #227), and whether content derived from a transcript may be sent to a web
search or an LLM provider (#92). Neither depends on where the layer lives.
