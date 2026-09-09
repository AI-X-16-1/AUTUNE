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


def test_auth_router_is_mounted() -> None:
    """The hand-mounted /api/auth router: /me with no session is 403, not 404."""
    assert client.get("/api/auth/me").status_code == 403


def test_application_errors_render_consistently() -> None:
    from autune_core import NotFoundError

    body = NotFoundError("meeting", "mtg_001").to_dict()
    assert body["error"]["code"] == "not_found"
    assert set(body["error"]) == {"code", "message", "details"}
