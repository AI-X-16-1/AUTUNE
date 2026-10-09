"""Every route of module E takes the session that commits before the response.

``autune_core.get_session`` commits after the response has been sent (#1041);
``autune_core.SessionDep`` commits as the route returns. What that dependency
does is tested in ``packages/core``. This file holds the other half: no route
here, and no route added later, takes ``get_session`` directly.

The route it mattered for: ``PUT /weekly-report-schedule/{team_id}`` only
flushes, so its change was committed after the person was told it was saved.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from autune_core import committed_session, get_session
from autune_intelligence.router import router


def _routes() -> list[APIRoute]:
    return [route for route in router.routes if isinstance(route, APIRoute)]


def _name(route: APIRoute) -> str:
    return f"{sorted(route.methods)[0]} {route.path}"


def test_no_route_takes_the_session_that_commits_after_the_response() -> None:
    late = [
        _name(route)
        for route in _routes()
        if any(dep.call is get_session for dep in route.dependant.dependencies)
    ]

    assert late == []


def test_the_routes_take_the_session_committed_as_they_return() -> None:
    """The check above passes on a router with no session at all; this one says
    the routes are there, the schedule change among them, and that the commit
    is tied to the route function."""
    taken = {
        _name(route): dep
        for route in _routes()
        for dep in route.dependant.dependencies
        if dep.call is committed_session
    }

    assert len(taken) >= 12
    assert "PUT /weekly-report-schedule/{team_id}" in taken
    assert {dep.scope for dep in taken.values()} == {"function"}
