# 0009. Frontend tests are component-level, and the app-wide privacy rule is not one of them

**Status:** Proposed
**Date:** 2026-09-23
**Deciders:** 김민경, 강민구, 박재경, 문민재, 이승환 — the tooling sits in root
config and `.github/`, so it is everyone's.

## Context

The frontend has no test infrastructure. No `vitest`, no `jest`, no
`@testing-library/*`, and no `test` script in either `package.json`. The CI
JavaScript job runs four steps — lint, types, build, and two checks that
generated output (design tokens, contract types) is current. **Nothing in CI
executes a component.**

This did not matter while the five feature directories were skeletons of
`api.ts`, `types.ts` and empty `components/`. #105 (S17 action board) was the
first screen with components in it, and #46, #48, #49 and #50 arrive at the same
wall behind it.

What type checking cannot see is what a component renders. From #105 alone:
whether a low-confidence item goes to the candidate band rather than a column,
whether an item somebody typed by hand (confidence 1.0) stays out of that band,
whether deleting offers an undo it cannot honour, and whether a masked span
renders as `PiiToken` with no control that reveals the original. The last one is
invariant 11. On the backend the equivalent rules are pinned by tests that
assert a whole column set (#79, #99, #103); on the screen they are pinned by
whoever reads the diff.

**Invariant 11 is not one kind of claim.** @kjfcvx12 made this distinction on
#106 and it is the reason this ADR exists rather than a one-line "add vitest"
PR:

| Rule | Shape | A component test can pin it |
| --- | --- | --- |
| A masked span renders as `PiiToken`, with no reveal control | what *this* component draws | yes |
| A low-confidence item goes to the candidate band | what *this* component draws | yes |
| One person's speaking ratio reaches nobody else | what *no* component draws | **no** |

The third is a negative proposition about the whole application, and
`docs/architecture/privacy.md` section 3 states it that way: no endpoint, query,
dashboard or export. A component test says "`ActionCard` does not draw a
speaking ratio" — it says nothing about the component nobody has written yet.
Worse, five such tests live in five owners' directories, and each owner can
delete their own and stay inside CODEOWNERS.

A decision that stops at "vitest, component level" would leave the rule looking
tested when it is not. So this ADR decides where each half of invariant 11 is
enforced, not only which runner to install.

## Decision

**1. The runner is `vitest` with `@testing-library/react` and `jsdom`.**
Component level only. A `test` script in `apps/web`, run from the root by
`pnpm -r test` like `lint` and `typecheck` already are.

**2. End-to-end tests are out of scope for the six weeks.** Playwright, a
browser matrix and fixture meetings served over HTTP are a second
infrastructure, and the screens they would drive are still being drawn. Revisit
after the six-week review, as P2.

**3. CI runs the tests between `Types` and `Build`.** A behaviour failure should
not wait behind the slowest step in the job, and a broken component is a more
interesting failure than a broken bundle.

**4. The positive half of invariant 11 is a component test, written by the
owner of the component.** `docs/engineering/testing.md` gets a Frontend section
listing them the way "Required tests" already lists the backend's: masked spans
render as `PiiToken` and no control reveals the original; a destructive action
offers no undo the server cannot honour.

**5. The negative half is not a component test.** It is enforced in the two
places where it is decidable, both of them shared files that one module owner
cannot quietly remove:

- **The contract, structurally.** The browser can only render what the payload
  carries, and `packages/contracts` already refuses to carry this:
  *"Speaking ratios are not in this payload and never will be"*
  (`intelligence.py`), and `extraction.py` refuses per-person stance for the
  same reason. `packages/contracts/ts/index.d.ts` and `schema.json` are
  generated from those models and CI already fails when they are stale. A test
  in `packages/contracts` asserts that no generated type carries a
  ratio-shaped property. The rule then holds for the sixth component nobody has
  written, because there is nothing for it to read.
- **A repo-wide lint rule.** `apps/web/eslint.config.mjs` is one config for the
  whole app and already carries a `no-restricted-imports` block. A
  `no-restricted-syntax` rule banning the ratio surface runs over all five
  feature directories in the existing Lint step. It is a tripwire, not a proof —
  its value is that it fires in the PR that introduces the field, and that
  removing it is a change to a shared file, which CODEOWNERS sends to all five.

**6. What this ADR does not decide.** Coverage thresholds (a number nobody can
justify yet), snapshot testing (rejected below), and the treatment of
`useTemplateComparison`-style hooks, which the first hook PR should settle.

## Alternatives considered

**Jest instead of vitest.** The team already runs Vite-flavoured tooling
nowhere, so neither is incumbent; the difference is configuration. Jest with
Next 15 and React 19 needs `next/jest`, a transform for ESM dependencies, and a
separate module resolution story from the one `tsconfig` already describes.
Vitest reads `tsconfig` paths directly and starts with a five-line config. If
the team later needs Jest's ecosystem, the `@testing-library` tests move
unchanged — the coupling is to the library, not the runner.

**Playwright now, component tests later.** E2E is what would actually exercise
"no screen shows a speaking ratio", because it drives the whole app. It also
needs a running API, a seeded database and fixture meetings, and it fails for
reasons that have nothing to do with the change under review. In six weeks with
five people it would be the thing everybody skips, and a skipped gate is worse
than a missing one: it reads as coverage.

**Snapshot tests.** Cheap to write, and they would have caught none of the four
cases above — a snapshot records that the output changed, not that it is wrong,
and the reflex on a failing snapshot is to update it. Explicit assertions about
what renders, or nothing.

**Per-feature privacy tests as the enforcement of invariant 11.** This is the
option this ADR exists to reject, and the table in Context is the argument: five
tests in five owners' directories cannot state a property of the whole app, and
they disappear one at a time without anyone outside that directory seeing it.
They remain useful for the positive half, which is why decision 4 keeps them.

**A single central test file that imports every component and asserts none of
them renders a ratio.** It fails the same way, one import at a time, and the
component that breaks the rule is the one that was never added to the list. The
contract-level check has no list.

## Consequences

**Easier.** The four behaviours listed in Context become assertions rather than
review comments. The four screens still to be built (#46, #48, #49, #50) arrive
into infrastructure that already exists, instead of each one re-opening this
question. Privacy rules on the screen stop depending on who reviewed the PR.

**Harder.** Every frontend PR now carries tests, including the ones that are
"just a layout change" — and the first few will be slower while the fixtures for
a contract-shaped payload get written. The `@autune/contracts` fixtures are
Python; the TS side has none, and someone has to write the first one.

**Accepted costs.** The lint rule is a string match: it catches the field named
the obvious way and not a ratio computed on the client out of two other numbers.
That reading is what the contract-level check covers, and neither one is a
substitute for reviewers who know the rule. No E2E means no gate on "the
dashboard, end to end, shows nobody else's ratio" until P2 — stated here so that
it is a known hole rather than an assumed cover.

**Cost to land.** One PR: `vitest`, `@testing-library/react`, `jsdom` and
`@vitejs/plugin-react` in `apps/web`, a `vitest.config.ts`, a `test` script, one
CI step, the contract-level test, the lint rule, and the Frontend section of
`docs/engineering/testing.md`. The first component tests come with #105 and #48
rather than in it.

## References

- #106, where this was raised and where @kjfcvx12 split invariant 11 into its
  two shapes.
- `docs/architecture/privacy.md` section 3 — the rule the negative proposition
  comes from.
- `docs/engineering/testing.md` — the backend's "Required tests", which the
  Frontend section mirrors.
- ADR 0003, which made privacy a code-level constraint rather than a policy
  document; this is the same argument applied to the screen.

<!-- 0007 is reserved for the legal review tracked in #92. -->
