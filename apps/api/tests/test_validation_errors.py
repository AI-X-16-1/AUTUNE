"""A schema refusal does not send the refused value back (#1225).

FastAPI's default 422 put every failing field's ``input`` -- a typed RRN or
phone number included -- in the response. The app renders it in the shared
error shape instead, naming the field and never the value.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from autune_api.main import create_app

RRN = "900101-1234567"
PHONE = "010-1234-5678"


class _Body(BaseModel):
    title: str = Field(max_length=10)
    count: int


def _client() -> TestClient:
    app = create_app(origins=[])

    # A throwaway route on the real app: the handler is app-wide, so any
    # route's schema refusal goes through it.
    @app.post("/test/echo")
    def echo(body: _Body, page: Annotated[int, Query()] = 1) -> dict[str, str]:
        return {"title": body.title}

    return TestClient(app)


def test_a_refused_body_does_not_echo_the_value() -> None:
    response = _client().post("/test/echo", json={"title": f"{RRN} {PHONE}", "count": PHONE})

    assert response.status_code == 422
    assert RRN not in response.text
    assert PHONE not in response.text
    assert "input" not in response.text
    assert "ctx" not in response.text


def test_the_refusal_keeps_the_error_shape_and_field_paths() -> None:
    response = _client().post("/test/echo", json={"title": "x" * 11, "count": "many"})

    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == "validation_error"
    errors = body["error"]["details"]["errors"]
    assert [(e["loc"], e["type"]) for e in errors] == [
        (["body", "title"], "string_too_long"),
        (["body", "count"], "int_parsing"),
    ]
    assert all(set(e) == {"loc", "type", "msg"} for e in errors)
    assert errors[0]["msg"] == "String should have at most 10 characters"


def test_a_missing_field_and_a_bad_query_name_where_they_are() -> None:
    response = _client().post("/test/echo?page=" + PHONE, json={"title": "ok"})

    assert response.status_code == 422
    assert PHONE not in response.text
    locs = [e["loc"] for e in response.json()["error"]["details"]["errors"]]
    assert ["query", "page"] in locs
    assert ["body", "count"] in locs


def test_a_body_that_is_not_json_is_refused_without_its_text() -> None:
    response = _client().post(
        "/test/echo", content=f'{{"title": "{RRN}"', headers={"content-type": "application/json"}
    )

    assert response.status_code == 422
    assert RRN not in response.text
    assert response.json()["error"]["code"] == "validation_error"
