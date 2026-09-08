"""HTTP entry point for module E.

Routes parse, delegate to ``service``, and format the result. No business logic
here — it cannot be reused by ``tasks.py`` if it lives in a route.

The prefix ``/api/intelligence`` is applied by apps/api; declare paths relative to it.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"module": "intelligence", "status": "ok"}
