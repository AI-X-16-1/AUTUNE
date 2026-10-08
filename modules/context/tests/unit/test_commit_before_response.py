"""Every /api/context route takes the session that commits before the response.

``autune_core.get_session`` commits after the response has been sent (#1041);
``autune_core.SessionDep`` commits as the route returns. What that dependency
does is tested in ``packages/core``. This file holds the other half: no route
here, and no route added later, takes ``get_session`` directly.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from autune_context.router import router
from autune_core import committed_session, get_session

ROUTES = [route for route in router.routes if isinstance(route, APIRoute)]


def test_no_route_takes_the_session_that_commits_after_the_response() -> None:
    assert ROUTES, "no routes found -- the router moved"
    late = sorted(
        route.path
        for route in ROUTES
        if any(dep.call is get_session for dep in route.dependant.dependencies)
    )

    assert late == []


def test_every_route_with_a_session_takes_the_one_committed_as_it_returns() -> None:
    """The check above passes on a router with no session at all; this one says
    the session routes are there and that the commit is tied to the route
    function. ``/health`` takes no session."""
    taken = [
        dep
        for route in ROUTES
        for dep in route.dependant.dependencies
        if dep.call is committed_session
    ]

    assert len(taken) == len(ROUTES) - 1
    assert {dep.scope for dep in taken} == {"function"}
