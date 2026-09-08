"""Pipeline progress reporting.

Modules report progress through this helper rather than writing their own rows,
so one UI can show any module's stage. See docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

import json

import redis

from .logging import get_logger
from .settings import get_settings

log = get_logger(__name__)

_TTL_SECONDS = 60 * 60 * 6


def _key(meeting_id: str) -> str:
    return f"autune:progress:{meeting_id}"


def report(meeting_id: str, stage: str, percent: int, module: str | None = None) -> None:
    """Record where a meeting's processing has reached.

    Carries ids and stage names only — never transcript text or a path to audio.
    """
    payload = {"stage": stage, "percent": max(0, min(100, percent))}
    if module:
        payload["module"] = module
    client = redis.from_url(get_settings().redis_url)
    client.set(_key(meeting_id), json.dumps(payload), ex=_TTL_SECONDS)
    log.info("progress_reported", meeting_id=meeting_id, stage=stage, percent=percent)


def read(meeting_id: str) -> dict | None:
    client = redis.from_url(get_settings().redis_url)
    raw = client.get(_key(meeting_id))
    return json.loads(raw) if raw else None
