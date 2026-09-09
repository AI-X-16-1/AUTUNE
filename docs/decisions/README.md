# Architecture Decision Records

An ADR records a decision that shaped the system, why it was made, and what it
cost. Its value is six weeks later, when someone asks "why is it like this?" and
the answer is not in anyone's head any more.

## When to write one

Write an ADR when a decision:
- constrains how other people work (module boundaries, contract policy),
- is expensive to reverse (datastore choice, monorepo shape),
- looks wrong without the reasoning behind it, or
- was made after a real disagreement.

Do not write one for a routine choice — a library, a variable name, a threshold
you can change tomorrow.

## Format

```
NNNN-short-kebab-title.md
```

Number sequentially. Never renumber or delete an ADR: a superseded decision is
still history. Mark it `Superseded by 0007` and leave it.

```markdown
# NNNN. Title

**Status:** Proposed | Accepted | Superseded by NNNN
**Date:** YYYY-MM-DD
**Deciders:** names

## Context
What forced a decision. Constraints, deadlines, what was already true.

## Decision
What we decided, stated so someone can follow it.

## Alternatives considered
What else was on the table and why it lost. This section is the point of the
document — a decision with no rejected alternatives was not a decision.

## Consequences
What this makes easy, what it makes hard, what we accept as a cost.
```

Written in English, like every document here.

## Index

| # | Title | Status |
| --- | --- | --- |
| [0001](0001-role-based-monorepo.md) | Role-based monorepo instead of frontend/backend | Accepted |
| [0002](0002-no-cross-module-imports.md) | Modules communicate only through contracts and events | Accepted |
| [0003](0003-privacy-first-data-handling.md) | Privacy constraints are code-level, not policy | Accepted |
| [0004](0004-pgvector-over-chroma.md) | Embeddings live in PostgreSQL, not a separate vector database | Accepted |
| [0005](0005-no-graph-database.md) | No graph database — graphs are PostgreSQL rows | Accepted |
| [0006](0006-extraction-quality-target-and-user-editing.md) | Extraction aims at the published ceiling, and the user finishes the list | Accepted |
