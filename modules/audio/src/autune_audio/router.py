"""HTTP entry point for module A.

Routes parse, delegate to ``service``, and format the result. No business logic
here — it cannot be reused by ``tasks.py`` if it lives in a route.

The prefix ``/api/audio`` is applied by apps/api; declare paths relative to it.
"""

from __future__ import annotations

from fastapi import APIRouter

from autune_core import get_settings as get_settings_core

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"module": "audio", "status": "ok"}


# Development only. The dev surface transcribes synchronously and returns
# unmasked text, so it is registered only on a developer's own machine — never
# in staging or production. See autune_audio.dev.
if get_settings_core().env == "local":
    from .dev.routes import router as _dev_router

    router.include_router(_dev_router, prefix="/dev")
