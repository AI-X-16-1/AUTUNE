"""CORS is opt-in and off by default.

No browser-facing page existed until the S26 dashboard, so nobody had hit
this: apps/web (:3000) and apps/api (:8000) are different origins in local
dev, and a browser blocks the response unless the API explicitly allows it.
Default-off keeps staging/production behavior unchanged for anyone who
hasn't set AUTUNE_CORS_ALLOWED_ORIGINS.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from autune_api.main import _cors_origins, app


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        ("http://localhost:3000", ["http://localhost:3000"]),
        (
            "http://localhost:3000,http://localhost:3001",
            ["http://localhost:3000", "http://localhost:3001"],
        ),
        (
            " http://localhost:3000 , ,http://localhost:3001 ",
            ["http://localhost:3000", "http://localhost:3001"],
        ),
    ],
)
def test_cors_origins_parses_a_comma_separated_list(raw: str, expected: list[str]) -> None:
    assert _cors_origins(raw) == expected


def test_cors_is_disabled_by_default() -> None:
    """The test environment sets no AUTUNE_CORS_ALLOWED_ORIGINS, so the
    module-level app is built with CORS off — a browser request from any
    origin gets no allow header, same as before this feature existed."""
    client = TestClient(app)

    response = client.get("/health", headers={"Origin": "http://localhost:3000"})

    assert "access-control-allow-origin" not in response.headers
