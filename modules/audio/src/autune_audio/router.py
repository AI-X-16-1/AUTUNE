"""HTTP entry point for module A.

Routes parse, delegate to ``service``, and format the result. No business logic
here — it cannot be reused by ``tasks.py`` if it lives in a route.

The prefix ``/api/audio`` is applied by apps/api; declare paths relative to it.
"""

from __future__ import annotations

from fastapi import APIRouter

from autune_core.settings import get_settings as get_core_settings

router = APIRouter()

# A local-only page for putting a recording through the pipeline by hand.
# It has no auth, so it is mounted nowhere but a developer's machine.
if get_core_settings().env == "local":
    from .dev import router as dev_router

    router.include_router(dev_router, prefix="/dev")


@router.get("/health")
def health() -> dict[str, str]:
    return {"module": "audio", "status": "ok"}
