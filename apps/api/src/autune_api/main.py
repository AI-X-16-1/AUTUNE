"""FastAPI application — assembly only.

Routers are collected by iterating the module list. Nobody edits this file to
ship a feature: if you need custom behavior, put it in your module's router.
See docs/architecture/monorepo.md.

The one hand-mounted router is ``/api/auth``: sign-in is cross-cutting, owned by
the whole team, and belongs to no module, so it is registered by name.
"""

from __future__ import annotations

from importlib import import_module

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from autune_contracts import MODULES
from autune_core import AutuneError, configure_logging, get_logger, get_settings
from autune_core.auth_router import router as auth_router
from autune_core.celery_app import make_celery_app

configure_logging()
log = get_logger(__name__)

# A client, not a worker: routes send by task name through ``celery.current_app``,
# and without an app of our own that is Celery's built-in default with a broker
# nobody runs (#258). No task module is imported here.
celery_app = make_celery_app(include_tasks=False)


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

    # Sign-in is not a module. It belongs to no owner in the map and every
    # module's routes depend on it, so it is registered here by name rather
    # than discovered — one of the two exceptions invariant 6 allows itself
    # (monorepo.md section 1), and the reason it is written out instead of
    # appended to the loop below.
    app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
    log.info("router_registered", module="auth", prefix="/api/auth")

    for name in MODULES:
        router = import_module(f"autune_{name}.router").router
        app.include_router(router, prefix=f"/api/{name}", tags=[name])
        log.info("router_registered", module=name, prefix=f"/api/{name}")

    # The other exception: the agent layer is not a module and is not in
    # MODULES (ADR 0010). One layer, one router; its subagents are collected
    # inside it, so a new one never lands here.
    app.include_router(
        import_module("autune_agent.router").router, prefix="/api/agent", tags=["agent"]
    )
    log.info("router_registered", module="agent", prefix="/api/agent")

    # Slack's Request URL, for a click on a button in Slack (#585): the Bolt app
    # and every module's handlers are apps/bot's; this only mounts it, and only
    # where a signing secret is configured -- Bolt refuses any request whose
    # signature does not match.
    if get_settings().slack_signing_secret:
        from slack_bolt.adapter.fastapi import SlackRequestHandler

        from autune_bot import build_app as build_slack_app

        slack = SlackRequestHandler(build_slack_app())

        @app.post("/api/slack/events", include_in_schema=False)
        async def slack_events(request: Request) -> Response:
            return await slack.handle(request)

        log.info("router_registered", module="slack", prefix="/api/slack/events")

    return app


app = create_app()
