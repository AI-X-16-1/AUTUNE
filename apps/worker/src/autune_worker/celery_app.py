"""Celery application — assembly only.

Tasks are collected by iterating the module list; modules declare tasks with
``celery.shared_task`` so they never import this app. The app itself is built
by ``autune_core.celery_app`` so the API process can build the same one and
send to the same broker with the same routes (#258).
See docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

from autune_core import configure_logging
from autune_core.celery_app import make_celery_app

configure_logging()

celery_app = make_celery_app(include_tasks=True)
