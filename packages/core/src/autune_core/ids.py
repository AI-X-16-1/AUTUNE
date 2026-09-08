"""Prefixed identifiers.

IDs are prefixed strings rather than bare UUIDs so a value is self-describing in
a log line, a Slack message, and a bug report. Generated here, never per module.
"""

from __future__ import annotations

import uuid
from typing import Final

MEETING: Final = "mtg"
UTTERANCE: Final = "utt"
USER: Final = "user"
TEAM: Final = "team"
PARTICIPANT: Final = "prt"
ACTION_ITEM: Final = "act"
GAP: Final = "gap"
TOPIC: Final = "topic"
DECISION: Final = "dec"
DECISION_THREAD: Final = "thr"  # D's lineage identity, spanning meetings
JOB: Final = "job"


def new_id(prefix: str) -> str:
    """Return a fresh id such as ``mtg_9f2c1a...``."""
    return f"{prefix}_{uuid.uuid4().hex}"


def has_prefix(value: str, prefix: str) -> bool:
    return value.startswith(f"{prefix}_")
