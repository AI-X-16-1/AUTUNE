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


def _cors_origins(raw: str) -> list[str]:
    """Parse ``Settings.cors_allowed_origins``. Blank entries are dropped."""
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def create_app(*, origins: list[str] | None = None) -> FastAPI:
    """Build the API app.

    ``origins`` overrides ``Settings.cors_allowed_origins`` when given
    (``None`` reads settings, matching production; a list — including an
    empty one — is used as-is). Tests pass it explicitly so CORS behavior is
    provable independent of whatever a developer's own ``.env`` happens to
    hold, and so the enabled path (an allowed origin actually gets the
    header, a disallowed one does not) has something to construct against.
    """
    app = FastAPI(
        title="Autune API",
        version="0.1.0",
        description="Meeting intelligence platform. Module routers are registered automatically.",
    )

    resolved_origins = (
        _cors_origins(get_settings().cors_allowed_origins) if origins is None else origins
    )
    if resolved_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        log.info("cors_enabled", origins=resolved_origins)

    @app.exception_handler(AutuneError)
    async def _autune_error_handler(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, object]:
        return {"status": "ok", "env": get_settings().env, "modules": list(MODULES)}

    for name in MODULES:
        router = import_module(f"autune_{name}.router").router
        app.include_router(router, prefix=f"/api/{name}", tags=[name])
        log.info("router_registered", module=name, prefix=f"/api/{name}")

    return app


app = create_app()
