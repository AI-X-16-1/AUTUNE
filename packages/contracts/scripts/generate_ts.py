"""Generate TypeScript types from the Pydantic contracts.

The Pydantic models are the source of truth. Never hand-edit the output.
Run with `pnpm run gen:contracts`; CI fails if the committed output is stale.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pydantic.json_schema import models_json_schema

from autune_contracts import (
    CONTRACT_VERSION,
    ContextLinks,
    ExtractionResult,
    GapReport,
    IntelligenceSnapshot,
    TranscriptReady,
)

PAYLOADS = {
    "TranscriptReady": TranscriptReady,
    "ExtractionResult": ExtractionResult,
    "GapReport": GapReport,
    "ContextLinks": ContextLinks,
    "IntelligenceSnapshot": IntelligenceSnapshot,
}

TS_DIR = Path(__file__).resolve().parents[1] / "ts"
SCHEMA_PATH = TS_DIR / "schema.json"
OUT_PATH = TS_DIR / "index.d.ts"

BANNER = f"""/**
 * Generated from packages/contracts (contract version {CONTRACT_VERSION}).
 * Do not edit. Run `pnpm run gen:contracts` and commit the result.
 */
"""


def build_schema() -> dict:
    """One schema with a shared $defs, so every $ref resolves from the root.

    Generating each payload separately leaves per-model $defs that json2ts
    cannot follow.
    """
    _, combined = models_json_schema(
        [(model, "validation") for model in PAYLOADS.values()],
        ref_template="#/$defs/{model}",
    )
    defs = combined.get("$defs", {})
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "AutuneContracts",
        "type": "object",
        "properties": {name: {"$ref": f"#/$defs/{name}"} for name in PAYLOADS},
        "required": list(PAYLOADS),
        "additionalProperties": False,
        "$defs": defs,
    }


def main() -> int:
    TS_DIR.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(json.dumps(build_schema(), indent=2, ensure_ascii=False) + "\n")

    result = subprocess.run(
        [
            "pnpm",
            "exec",
            "json2ts",
            "--input",
            str(SCHEMA_PATH),
            "--output",
            str(OUT_PATH),
            "--bannerComment",
            BANNER,
            "--additionalProperties",
            "false",
            "--unreachableDefinitions",
        ],
        cwd=TS_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        return result.returncode
    print(f"wrote {SCHEMA_PATH.name} and {OUT_PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
