"""KorNLI fine-tuning for the in-house ``klue_kornli_*`` checkpoint.

See docs/modules/context.md, "Phased delivery" (Phase 3: "``KlueKorNliHttp``
and the KorNLI fine-tuning job") and ``autune_context.pipeline.nli``, which is
what actually serves the checkpoint this package produces.

Run with ``uv run --package autune-context --extra training python -m
autune_context.training <command>``. Needs the ``training`` extra (torch,
transformers, datasets, accelerate — not part of the base install, same as
``local-models``) and, practically, a GPU: klue/roberta-base on the ~950k-pair
KorNLI train split is not a CPU job. Not run by CI; on demand by the module
owner, same as ``autune_context.eval``.
"""

from __future__ import annotations
