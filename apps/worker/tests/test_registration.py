"""Every module's tasks must register, and land on the right queue."""

from __future__ import annotations

import pytest

from autune_contracts import MODULES
from autune_worker import celery_app


@pytest.fixture(scope="module")
def task_names() -> set[str]:
    celery_app.loader.import_default_modules()
    return {name for name in celery_app.tasks if name.startswith("autune.")}


@pytest.mark.parametrize("module", MODULES)
def test_module_has_at_least_one_task(module: str, task_names: set[str]) -> None:
    assert any(name.startswith(f"autune.{module}.") for name in task_names), (
        f"{module} registered no tasks"
    )


def test_audio_runs_on_the_gpu_queue(task_names: set[str]) -> None:
    """STT belongs on the GPU worker; a short task there wastes it."""
    routes = celery_app.conf.task_routes
    assert routes["autune.audio.*"]["queue"] == "gpu"


def test_intelligence_consumes_all_three_upstream_modules(task_names: set[str]) -> None:
    for upstream in ("extraction", "gap", "context"):
        assert f"autune.intelligence.on_{upstream}_completed" in task_names
