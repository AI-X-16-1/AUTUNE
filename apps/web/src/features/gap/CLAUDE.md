# features/gap — Module C frontend

Owner: 박재경. Backend counterpart: `modules/gap/` (`/api/gap`).
Screens: S20 gap report, S15 gaps tab — see `/docs/design/ui-spec.md`.

## Rules

- **Never import another feature.** Shared code lives in `@/shared`; eslint
  blocks cross-feature imports. If you need something another feature has, ask
  its owner to move it to `shared/` — that is a team decision.
- **Call only `/api/gap`.** Use `api.gap()` from `@/shared/api/client`.
- **Types for API payloads come from `@autune/contracts`**, generated from the
  Pydantic models. Regenerate with `pnpm run gen:contracts`; never hand-write a
  mirror.
- **Use tokens, never literals.** Colours, sizes, radius, control heights and
  motion all come from `src/app/tokens.css`, generated from
  `docs/design/design-tokens.json`. A value that is not a token is a token
  change, not a local style.
- **Shared components come from `@/shared/ui`** — `StatusDot`, `Row`, `Band`,
  `ScoreLabel`, `Button`, `ChipToggle`, `PiiToken`, `Quote`, `Tabs`,
  `RecordingFrame`. Do not build a second version of one of these.
- **User-facing copy is Korean.** Code, comments and identifiers stay English.

## Privacy

Read `/docs/architecture/privacy.md` before rendering transcripts or anything
per-person. Masked spans render as `PiiToken` and there is no "reveal original"
control, because the original was never stored. No screen may show one person's
speaking ratio to anyone else.
