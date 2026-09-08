# Dependencies

## Python — uv workspace

```toml
# root pyproject.toml
[tool.uv.workspace]
members = ["packages/*", "modules/*", "apps/api", "apps/worker"]
```

Each package and module owns its own `pyproject.toml`. The root manifest
declares the workspace and shared dev tooling — nothing else.

### Adding a dependency

```bash
uv add --package autune-gap spacy
```

Or edit `modules/gap/pyproject.toml` and run `uv sync`.

**Add to your own module. Never to the root.** The root manifest is shared by
five people; a dependency there is a merge conflict and a dependency everyone
pays for. Module A pulls in torch, whisper, and pyannote — several gigabytes.
Module C should not.

### Declaring what you depend on

```toml
# modules/gap/pyproject.toml
[project]
name = "autune-gap"
requires-python = ">=3.12"
dependencies = [
    "autune-contracts",
    "autune-core",
    "spacy>=3.7",
    "neo4j>=5.0",
]

[tool.uv.sources]
autune-contracts = { workspace = true }
autune-core = { workspace = true }
```

Never declare a dependency on another module. The workspace would resolve it,
and import-linter would then fail in CI — but the manifest should not express
the intent in the first place.

### Pinning

- Application code (`modules/*`, `apps/*`): floor constraints (`>=3.7`) plus a
  lockfile.
- ML libraries with breaking minor releases (torch, transformers, spacy models):
  pin an exact version. Reproducibility matters more than freshness.
- Model weights are pinned by name and revision in code, not in the manifest.

## JavaScript — pnpm workspace

```yaml
# pnpm-workspace.yaml
packages:
  - "apps/web"
  - "apps/bot"
  - "packages/contracts/ts"
```

```bash
pnpm --filter @autune/web add recharts
```

Same rule: install into the package that uses it, never into the root.

## Lockfiles

`uv.lock` and `pnpm-lock.yaml` are committed. They will conflict — several
people adding dependencies in the same week guarantees it.

**Never hand-merge a lockfile.** Regenerate:

```bash
git checkout --theirs uv.lock && uv lock
git checkout --theirs pnpm-lock.yaml && pnpm install
```

Then commit the regenerated file. A hand-merged lockfile produces a dependency
tree nobody has ever installed.

## Runtime versions

- Python **3.12** — best-supported combination for torch, whisper, pyannote, and
  spaCy at project start
- Node **22 LTS**
- pnpm **9.15.4**, provided by corepack
- uv — latest

If `corepack enable pnpm` fails with `Cannot find matching keyid`, the corepack
bundled with your Node is too old for npm's current signing keys. Fix it with
`npm i -g corepack@latest`, then enable pnpm again.

Pinned in `.python-version`, `.nvmrc`, and `packageManager` in the root
`package.json`.

## Before adding a dependency

1. Does `packages/core` or `packages/integrations` already provide it?
2. Does another module already depend on it? If so, use the same version to keep
   resolution simple.
3. How large is it? A gigabyte of CUDA wheels in `apps/api` slows every deploy.
4. Is it maintained? A six-week project cannot absorb an abandoned library.
5. Does it phone home or log payloads? See `../architecture/privacy.md`.

## Common commands

```bash
uv sync                          # install everything in the workspace
uv sync --package autune-gap     # install one module
uv run --package autune-gap pytest
uv lock --upgrade-package spacy  # bump one package
pnpm install
pnpm --filter @autune/web dev
```
