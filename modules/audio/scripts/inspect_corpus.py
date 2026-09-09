"""Report what a speech corpus can and cannot measure.

Point it at a downloaded corpus and it answers one question: which of module A's
metrics are computable from these labels. Written before the corpus arrived, so
it looks for the fields by meaning rather than assuming one schema.

    uv run python modules/audio/scripts/inspect_corpus.py <corpus-root>

Prints JSON on stdout, like the extraction scripts.

It reads structure and never prints label content: a corpus of meeting speech is
other people's conversation, and this output may end up pasted into an issue.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# Field names by what they mean. AI Hub, Kaldi and Whisper-style manifests all
# use different words for the same three things.
SPEAKER = {"speaker", "speaker_id", "speakerid", "spk", "spk_id", "화자", "화자id"}
START = {"start", "start_time", "starttime", "begin", "from", "시작시간", "startsec"}
END = {"end", "end_time", "endtime", "to", "종료시간", "endsec"}
TEXT = {"text", "transcript", "transcription", "form", "sentence", "전사", "발화"}
MASKED_PAIR = {"form", "original_form"}


def keys_in(node: Any, seen: Counter[str], depth: int = 0) -> None:
    """Every key anywhere in the document, with how often it appears."""
    if depth > 12:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            seen[key.lower()] += 1
            keys_in(value, seen, depth + 1)
    elif isinstance(node, list):
        for item in node[:50]:
            keys_in(item, seen, depth + 1)


def main(root: Path) -> int:
    labels = [p for p in root.rglob("*.json") if p.stat().st_size < 50_000_000]
    if not labels:
        print(
            json.dumps({"error": "no .json label files found under that path"}, ensure_ascii=False)
        )
        return 1

    seen: Counter[str] = Counter()
    read = 0
    for path in labels[:200]:
        try:
            keys_in(json.loads(path.read_text(encoding="utf-8")), seen)
            read += 1
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

    found = set(seen)
    has_speaker = bool(found & SPEAKER)
    has_times = bool(found & START) and bool(found & END)
    has_text = bool(found & TEXT)
    has_masked_pair = found >= MASKED_PAIR

    report = {
        "label_files_found": len(labels),
        "label_files_read": read,
        "fields": {
            "speaker": sorted(found & SPEAKER),
            "start": sorted(found & START),
            "end": sorted(found & END),
            "text": sorted(found & TEXT),
        },
        "measurable": {
            "WER (speech recognition)": has_text,
            "DER (speaker diarization)": has_speaker and has_times,
            "PII masking recall": has_masked_pair,
        },
        "audio_files": Counter(
            p.suffix.lower()
            for p in root.rglob("*")
            if p.suffix.lower() in {".wav", ".mp3", ".m4a", ".flac", ".pcm"}
        ),
        "most_common_keys": [k for k, _ in seen.most_common(40)],
    }

    if not has_speaker:
        report["note_speaker"] = (
            "No speaker field found. DER cannot be computed from these labels; "
            "either another corpus or hand-labelled turns would be needed."
        )
    if not has_times:
        report["note_times"] = "No start/end pair found. DER needs turn boundaries, not just text."
    if has_masked_pair:
        report["note_masking"] = (
            "form and original_form are both present, so PII masking recall is "
            "measurable directly. original_form is unmasked personal data: read "
            "it in memory for scoring and never write it anywhere."
        )

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: inspect_corpus.py <corpus-root>")
    raise SystemExit(main(Path(sys.argv[1])))
