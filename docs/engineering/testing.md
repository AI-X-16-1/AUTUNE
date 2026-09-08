# Testing

A six-week project cannot afford exhaustive tests, and it cannot afford
regressions in shared code either. Test where a break is expensive.

## Layers

| Layer | Where | Speed | What it covers |
| --- | --- | --- | --- |
| Unit | `modules/<name>/tests/unit/` | ms | Pure logic, scoring, parsing, masking rules |
| Contract | `packages/contracts/tests/` | ms | Payloads validate; example fixtures round-trip |
| Integration | `modules/<name>/tests/integration/` | seconds | Router + service + database against real Postgres |
| Pipeline | `modules/<name>/tests/pipeline/` | slow, marked | Model inference on small fixtures |
| Evaluation | `modules/<name>/eval/` | slow, manual | The module's product KPI |

Unit and contract tests run on every push. Integration runs in CI. Pipeline
tests are marked and excluded from the default run. Evaluation is run on demand
by the module owner.

## Required tests

These are not optional; a PR without them does not merge.

1. **Contract conformance.** Whatever your module publishes validates against the
   contract model, and whatever it consumes is parsed through
   `model_validate` — never read as a raw dict.
2. **Idempotency.** Running your Celery task twice with the same payload leaves
   the same state. See `../architecture/async-pipeline.md`.
3. **Deletion.** Deleting a meeting removes everything your module stored about
   it — Postgres rows, Neo4j nodes, Chroma embeddings, cached files. See
   `../architecture/privacy.md`.
4. **Privacy, for module A.** The raw file is gone after the task, including on
   failure. Masking applies before the first write. Unmasked text appears in no
   log line and no exception.
5. **Partial-input handling, for module E.** Aggregation works when one of B, C,
   or D is missing.

## Fixtures

Shared fixtures live in `packages/contracts/fixtures/` so all five modules test
against the same payloads:

```
fixtures/
├── transcript_ready.short.json      # 3 utterances, 2 speakers
├── transcript_ready.typical.json    # 45 minutes, 4 speakers
├── transcript_ready.unidentified.json  # speaker_id null throughout
├── extraction_result.json
├── gap_report.json
└── context_links.json
```

`transcript_ready.unidentified.json` exists because every consumer must handle
a null `speaker_id`, and this is the case people forget.

Fixtures contain synthetic text only. Never commit a real meeting transcript,
even masked.

## Databases in tests

Integration tests run against a real PostgreSQL from `docker-compose`, not
SQLite — the schema uses JSONB and PostgreSQL-specific constraints.

Each test gets a transactional rollback via the `db_session` fixture from
`autune_core.testing`. Neo4j and Chroma tests use a per-test namespace and clean
up afterwards.

## Model-dependent tests

Model inference is slow and needs weights that are not in git. Mark those tests:

```python
@pytest.mark.model
def test_diarization_separates_two_speakers(): ...
```

```bash
uv run pytest                    # skips model tests
uv run pytest -m model           # runs them
```

Test the code around the model without the model: mock the inference call and
assert on the handling of its output. Whether Whisper transcribes correctly is
an evaluation question, not a unit test.

## Evaluation

Each module owner maintains an evaluation script reporting their KPI from
`../product/prd.md` section 12:

| Module | Metric | Target (6 weeks) |
| --- | --- | --- |
| A | Diarization DER | ≤ 15% |
| A | PII masking recall | 0.95+ |
| B | Action item extraction F1 | 0.80+ |
| C | Gap detection precision | 0.70+ |
| D | Topic linking accuracy | 0.75+ |
| E | Prediction calibration | reported |

```bash
uv run --package autune-gap python -m autune_gap.eval
```

The evaluation set is small and hand-labeled. Keep it in the module and version
it — a metric that moves because the eval set changed is not a metric.

## CI gates

Every pull request runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run lint-imports              # module independence
uv run pytest -m "not model"
uv run alembic -c infra/alembic.ini upgrade heads
pnpm run lint
pnpm run typecheck
pnpm run gen:contracts --check   # generated TS types are current
```

All of them must pass. Fixing a failure by relaxing the configuration is not
fixing it.

## Writing tests

- Name the behavior, in English:
  `test_masks_phone_number_before_persisting_utterance`.
- One assertion subject per test.
- No sleeps. Await, poll with a timeout, or run the task synchronously with
  `task_always_eager`.
- No network calls. Mock external integrations at the `packages/integrations`
  boundary.
