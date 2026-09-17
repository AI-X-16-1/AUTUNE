"""FastAPI application — assembly only.

Routers are collected by iterating the module list. Nobody edits this file to
ship a feature: if you need custom behavior, put it in your module's router.
See docs/architecture/monorepo.md.
"""

from __future__ import annotations

from importlib import import_module

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from autune_contracts import MODULES
from autune_core import AutuneError, configure_logging, get_logger, get_settings

configure_logging()
log = get_logger(__name__)

app = FastAPI(
    title="Autune API",
    version="0.1.0",
    description="Meeting intelligence platform. Module routers are registered automatically.",
)


def _cors_origins(raw: str) -> list[str]:
    """Parse ``Settings.cors_allowed_origins``. Blank entries are dropped."""
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


if _origins := _cors_origins(get_settings().cors_allowed_origins):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    log.info("cors_enabled", origins=_origins)


@app.exception_handler(AutuneError)
async def _autune_error_handler(_: Request, exc: AutuneError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.get("/health", tags=["meta"])
def health() -> dict[str, object]:
    return {"status": "ok", "env": get_settings().env, "modules": list(MODULES)}


for _name in MODULES:
    _router = import_module(f"autune_{_name}.router").router
    app.include_router(_router, prefix=f"/api/{_name}", tags=[_name])
    log.info("router_registered", module=_name, prefix=f"/api/{_name}")
