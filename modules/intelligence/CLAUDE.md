# modules/intelligence — Module E: Meeting Intelligence

Read `/CLAUDE.md` first. This file holds only what is specific to this module.
Full detail: `/docs/modules/intelligence.md`.

| | |
| --- | --- |
| **Package** | `autune_intelligence` |
| **Owner** | 이승환 |
| **Frontend** | `apps/web/src/features/dashboard/` |
| **Table prefix** | `intel_` — mandatory on every table this module creates |
| **API prefix** | `/api/intelligence` |
| **Alembic branch** | `intelligence` |

## What this module does

Aggregate B, C, and D across meetings: quality score, gap patterns, alignment
heatmap, predictions, weekly report, and each person's own speaking ratio.

## Consumes

`ExtractionResult`, `GapReport`, and `ContextLinks` from their completion
events. Read-only access to shared entities.

**Handle partial input.** Any of B, C, or D can fail. Aggregate when all three
arrive or when the timeout elapses (10 minutes), and mark which sources were
missing. Never block a user-visible result on a failed module.

## Publishes

`IntelligenceSnapshot` on `autune.intelligence.completed`.

## Owns

`intel_scores`, `intel_gap_patterns`, `intel_alignment`, `intel_predictions`,
`intel_reports`, `intel_completion`.

Reference B's and C's outputs by plain string ID columns — never a foreign key
into another module's tables.

## AI stack

SetFit for gap-pattern classification, XGBoost for misalignment prediction,
Prophet for trend forecasting, active learning for labeling efficiency, LLM for
report prose generated from computed numbers.

## Privacy — read this before building anything here

This module is where privacy is easiest to break, because aggregation is exactly
what a surveillance feature looks like. `/docs/architecture/privacy.md`
section 3 is binding.

- **Speaking ratio goes to the speaker and nobody else** — not a manager, not
  the organizer, not an admin, not an export.
- **Do not store per-person speaking ratios.** Compute, deliver by DM, discard.
  There is no speaking-ratio table and there will not be one.
- **Speaking ratio never enters `IntelligenceSnapshot`** or any other contract.
- **No small-group distributions.** A distribution over a four-person meeting
  identifies everyone; anonymization does not help.
- `/api/intelligence/me/speaking-ratio` authorizes on
  `requester_id == subject_id`, with no admin override.
- **The influence map (Phase 2) goes to the person themselves and nobody
  else — decided on #28.** Same delivery as speaking ratio (subject-only,
  by DM); it must never land on the shared dashboard, and no admin override.

## Do not do here

- Recompute B's, C's, or D's work. Consume their contracts.
- Add a per-person speech-volume metric to any shared surface.

## Metric

Prediction calibration, reported by the owner.

```bash
uv run --package autune-intelligence python -m autune_intelligence.eval
```
