# Workflow

## Language rule

| Surface | Language |
| --- | --- |
| Documents, code comments, docstrings, commit messages, PR titles and bodies, ADRs, test names, log messages, error strings | **English** |
| Conversation with teammates and users — chat, Slack, review comments addressed to a person | **Korean (한국어)** |

Everyone follows this, including AI agents. The repository is permanent and
shared; conversation is ephemeral and belongs in the team's working language.

**One exception: `/README.md` is written in Korean.** It is the project's front
door for Korean readers and explains what Autune is rather than how to build it.
Do not translate it. Everything under `docs/`, every `CLAUDE.md`, and everything
inside the code stays English.

## Branches

```
<module>/<task>
```

Examples: `audio/pii-masking`, `gap/topic-graph`, `context/decision-lineage`,
`extraction/notion-sync`.

For shared code, use the layer name: `contracts/add-context-links`,
`core/auth-session`, `infra/docker-compose`.

- `main` is protected. No direct pushes.
- Branch from the latest `main`. Rebase rather than merge to stay current.
- One branch per task. A branch that touches three modules is three branches.

## Pull requests

Every change lands through a pull request.

**Requirements:**
- CI green: lint, type check, import-linter, tests.
- At least one approval. Changes to `packages/contracts` need approval from
  every affected module owner.
- Documents updated in the same PR when behavior they describe changed.

**Description template:**

```markdown
## What
One or two sentences.

## Why
The reason, or the issue link.

## Contract changes
None. / Added optional field `X` to `Y` (version 1.0 → 1.1).

## Privacy impact
None. / Touches transcript text — masking applied at <location>.

## How to verify
The commands or steps a reviewer runs.
```

Keep pull requests small. A PR that touches your module only should be
reviewable in ten minutes. A PR that touches `packages/` should be smaller
still.

## CODEOWNERS

GitHub handles below are placeholders — replace them with real handles when
the repository is created, and create a `@autune/core` team for the shared
paths. Ownership by person: A 김민경, B 강민구, C 박재경, D 문민재, E 이승환.

```
/modules/audio/          @audio-owner
/modules/extraction/     @extraction-owner
/modules/gap/            @gap-owner
/modules/context/        @context-owner
/modules/intelligence/   @intelligence-owner

/apps/web/src/features/transcript/  @audio-owner
/apps/web/src/features/actions/     @extraction-owner
/apps/web/src/features/gap/         @gap-owner
/apps/web/src/features/context/     @context-owner
/apps/web/src/features/dashboard/   @intelligence-owner

/packages/contracts/     @autune/core
/packages/core/          @autune/core
/packages/integrations/  @autune/core
/apps/api/               @autune/core
/apps/worker/            @autune/core
/apps/bot/               @autune/core
/infra/                  @autune/core
/docs/                   @autune/core
```

If a change you need lives outside your ownership, ask the owner. Do not edit
across a boundary because it is faster.

## Commits

```
<type>(<scope>): <summary in English, imperative>
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `perf`.
Scope: the module or package — `audio`, `gap`, `contracts`, `core`, `web`.

```
feat(audio): add regex+NER double detection for PII masking
fix(gap): handle meetings with a single participant in centrality
docs(contracts): document ContextLinks change_type values
```

Explain *why* in the body when the reason is not obvious from the diff.

## Reviews

- Review within a working day. A stalled review blocks a teammate for a week in
  a six-week project.
- Comment in Korean; the code and the commit stay in English.
- Reviewers check the privacy checklist in `../architecture/privacy.md` on any
  PR touching transcripts, audio, or speaker identity.
- Approving a contract change means you have checked your own module against it.

## Reporting status

Each module owner reports their product KPI (`../product/prd.md` section 12)
from a script in their module. "It seems better" is not a status report.

## Weekly rhythm

The six-week roadmap in `../product/prd.md` section 10 sets weekly milestones.
Module A is the critical path: B, C, and D cannot integrate until
`TranscriptReady` is real. A ships first.
