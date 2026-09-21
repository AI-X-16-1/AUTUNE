"""Every module's router must register itself. Nobody edits main.py to add one."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from autune_api.main import app
from autune_contracts import MODULES

client = TestClient(app)


def test_root_health_lists_every_module() -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["modules"] == list(MODULES)


@pytest.mark.parametrize("module", MODULES)
def test_module_router_is_registered(module: str) -> None:
    response = client.get(f"/api/{module}/health")
    assert response.status_code == 200, f"{module} router did not register"
    assert response.json() == {"module": module, "status": "ok"}


def test_application_errors_render_consistently() -> None:
    from autune_core import NotFoundError

    body = NotFoundError("meeting", "mtg_001").to_dict()
    assert body["error"]["code"] == "not_found"
    assert set(body["error"]) == {"code", "message", "details"}


def test_a_route_handler_sends_to_our_broker_not_celerys_default() -> None:
    """Sync routes run on a threadpool, and ``current_app`` is thread-local.

    The failure this guards is the one from #258: the API process enqueues
    into ``amqp://guest@localhost//`` because it never built an app. Checked
    from inside a request so the threadpool thread is the one asked.

    ``set_default`` is re-applied first because this pytest process also
    imports ``apps/worker``'s app; a real API process only ever builds this one.
    """
    from celery import current_app

    from autune_api import main
    from autune_core import get_settings

    main.celery_app.set_default()

    @app.get("/__test_broker")
    def _broker() -> dict[str, object]:
        return {"is_ours": current_app._get_current_object() is main.celery_app}

    assert client.get("/__test_broker").json() == {"is_ours": True}
    assert main.celery_app.conf.broker_url == get_settings().redis_url
    assert main.celery_app.conf.task_routes["autune.audio.*"]["queue"] == "gpu"
    assert list(main.celery_app.conf.include) == [], "the API process must not import task modules"
