"""Celery application — assembly only.

Tasks are collected by iterating the module list; modules declare tasks with
``celery.shared_task`` so they never import this app.
See docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

from celery import Celery

from autune_contracts import MODULES
from autune_core import configure_logging, get_settings

configure_logging()
settings = get_settings()

celery_app = Celery(
    "autune",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[f"autune_{name}.tasks" for name in MODULES],
)

celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # A long task on `default` blocks Slack notifications; a short one on `gpu`
    # wastes an expensive worker.
    task_routes={
        "autune.audio.*": {"queue": "gpu"},
        "autune.extraction.*": {"queue": "cpu_heavy"},
        "autune.gap.*": {"queue": "cpu_heavy"},
        "autune.context.*": {"queue": "cpu_heavy"},
        "autune.intelligence.aggregate": {"queue": "cpu_heavy"},
        "autune.intelligence.*": {"queue": "default"},
    },
)
