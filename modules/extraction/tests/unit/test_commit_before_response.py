"""Every route of module B takes the session that commits before the response.

``autune_core.get_session`` commits after the response has been sent (#1041);
``autune_core.SessionDep`` commits as the route returns. What that dependency
does is tested in ``packages/core``. This file holds the other half: no route
here, and no route added later, takes ``get_session`` directly.
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute

from autune_core import committed_session, get_session
from autune_extraction.dev import routes as dev_routes
from autune_extraction.router import router

ROUTERS = {"router": router, "dev routes": dev_routes.router}


def _routes(which: APIRouter) -> list[APIRoute]:
    return [route for route in which.routes if isinstance(route, APIRoute)]


def _name(route: APIRoute) -> str:
    return f"{sorted(route.methods)[0]} {route.path}"


@pytest.mark.parametrize("which", list(ROUTERS))
def test_no_route_takes_the_session_that_commits_after_the_response(which: str) -> None:
    late = [
        _name(route)
        for route in _routes(ROUTERS[which])
        if any(dep.call is get_session for dep in route.dependant.dependencies)
    ]

    assert late == []


@pytest.mark.parametrize(("which", "at_least"), [("router", 40), ("dev routes", 2)])
def test_the_routes_take_the_session_committed_as_they_return(which: str, at_least: int) -> None:
    """The check above passes on a router with no session at all; this one says
    the routes are there and that the commit is tied to the route function."""
    taken = [
        dep
        for route in _routes(ROUTERS[which])
        for dep in route.dependant.dependencies
        if dep.call is committed_session
    ]

    assert len(taken) >= at_least
    assert {dep.scope for dep in taken} == {"function"}
