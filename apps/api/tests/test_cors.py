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

from autune_api.main import _cors_origins, create_app


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


def test_disabled_by_default_when_no_origins_are_configured() -> None:
    """``origins=[]`` explicitly, not ambient settings — a developer whose own
    .env sets AUTUNE_CORS_ALLOWED_ORIGINS (as our own docs tell them to for
    local dev) must not change what this test proves."""
    client = TestClient(create_app(origins=[]))

    response = client.get("/health", headers={"Origin": "http://localhost:3000"})

    assert "access-control-allow-origin" not in response.headers


def test_allowed_origin_receives_the_cors_header() -> None:
    client = TestClient(create_app(origins=["http://localhost:3000"]))

    response = client.get("/health", headers={"Origin": "http://localhost:3000"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_origin_outside_the_allowlist_receives_no_cors_header() -> None:
    client = TestClient(create_app(origins=["http://localhost:3000"]))

    response = client.get("/health", headers={"Origin": "http://evil.example"})

    assert "access-control-allow-origin" not in response.headers
