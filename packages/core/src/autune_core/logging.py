"""Structured logging.

Log ids, counts, durations, model versions and scores.

Never log transcript text, audio paths, or personal data — at any level,
including DEBUG. See docs/architecture/privacy.md section 2.
"""

from __future__ import annotations

import logging
import sys

import structlog

from .settings import get_settings

_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return

    settings = get_settings()
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=settings.log_level)

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    # Human-readable locally, JSON everywhere a log aggregator reads it.
    processors.append(
        structlog.dev.ConsoleRenderer()
        if settings.env == "local"
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    configure_logging()
    return structlog.get_logger(name)
