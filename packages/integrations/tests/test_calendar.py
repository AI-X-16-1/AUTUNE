"""``CalendarClient`` and the token refresh against Google's answers (#435),
over a mock transport -- no network calls."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest

from autune_core.errors import PrivacyViolationError
from autune_integrations.calendar import (
    CalendarClient,
    ReconnectRequiredError,
    refresh_access_token,
)
from autune_integrations.errors import PermanentIntegrationError, TransientIntegrationError
from autune_integrations.fakes import FakeCalendar

START = datetime(2026, 10, 1, tzinfo=UTC)
END = datetime(2026, 10, 8, tzinfo=UTC)


def client(handler: Callable[[httpx.Request], httpx.Response]) -> CalendarClient:
    c = CalendarClient("token")
    c._client = httpx.Client(
        base_url="https://www.googleapis.com/calendar/v3", transport=httpx.MockTransport(handler)
    )
    return c


# --- refresh ----------------------------------------------------------------------


def _token_endpoint(status: int, body: dict[str, Any]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://oauth2.googleapis.com/token"
        assert b"grant_type=refresh_token" in request.content
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_refresh_returns_the_new_access_token() -> None:
    http = _token_endpoint(200, {"access_token": "ya29.new", "expires_in": 3599})
    token = refresh_access_token(client_id="id", client_secret="s", refresh_token="r", http=http)
    assert token == "ya29.new"


def test_a_refused_refresh_token_means_reconnect_and_says_nothing_secret() -> None:
    http = _token_endpoint(400, {"error": "invalid_grant"})
    with pytest.raises(ReconnectRequiredError) as caught:
        refresh_access_token(
            client_id="id", client_secret="the-secret", refresh_token="the-refresh", http=http
        )
    assert caught.value.details["upstream_status"] == 400
    assert "the-secret" not in str(caught.value)
    assert "the-refresh" not in str(caught.value)


def test_a_token_endpoint_outage_is_transient() -> None:
    http = _token_endpoint(503, {})
    with pytest.raises(TransientIntegrationError):
        refresh_access_token(client_id="id", client_secret="s", refresh_token="r", http=http)


# --- reads ------------------------------------------------------------------------


def test_list_events_reads_timed_and_all_day_events_and_skips_cancelled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/calendar/v3/calendars/team_cal/events"
        assert request.url.params["singleEvents"] == "true"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "e1",
                        "summary": "주간 회의",
                        "start": {"dateTime": "2026-10-01T10:00:00+09:00"},
                        "end": {"dateTime": "2026-10-01T11:00:00+09:00"},
                        "attendees": [{"email": "a@example.com"}, {"self": True}],
                    },
                    {
                        "id": "e2",
                        "summary": "마감",
                        "start": {"date": "2026-10-02"},
                        "end": {"date": "2026-10-03"},
                    },
                    {"id": "e3", "status": "cancelled", "start": {}, "end": {}},
                ]
            },
        )

    events = client(handler).list_events("team_cal", START, END)

    assert [e.id for e in events] == ["e1", "e2"]
    assert events[0].attendees == ["a@example.com"]
    assert not events[0].all_day
    assert events[1].all_day
    assert events[1].start == date(2026, 10, 2)


def test_an_unreadable_calendar_is_unknown_not_free() -> None:
    """#59: Google answers an address it cannot read with an error and an empty
    busy list. Read as free, that person looks available all week."""

    def handler(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        assert sent["items"] == [{"id": "in@example.com"}, {"id": "out@else.com"}]
        return httpx.Response(
            200,
            json={
                "calendars": {
                    "in@example.com": {
                        "busy": [{"start": "2026-10-01T01:00:00Z", "end": "2026-10-01T02:00:00Z"}]
                    },
                    "out@else.com": {"errors": [{"reason": "notFound"}], "busy": []},
                }
            },
        )

    answer = client(handler).free_busy(["in@example.com", "out@else.com"], START, END)

    assert answer["in@example.com"] == [
        (datetime(2026, 10, 1, 1, tzinfo=UTC), datetime(2026, 10, 1, 2, tzinfo=UTC))
    ]
    assert answer["out@else.com"] is None


# --- writes -----------------------------------------------------------------------


def test_an_all_day_event_ends_the_next_day_and_invites_nobody() -> None:
    sent: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "evt_1"})

    event_id = client(handler).create_all_day_event(
        "team_cal", "[마감] 스펙 공유", date(2026, 10, 2)
    )

    assert event_id == "evt_1"
    assert sent["start"] == {"date": "2026-10-02"}
    assert sent["end"] == {"date": "2026-10-03"}
    assert "attendees" not in sent


@pytest.mark.parametrize("status", [404, 410])
def test_updating_a_deleted_event_says_so(status: int) -> None:
    c = client(lambda request: httpx.Response(status, json={}))
    assert c.update_all_day_event("team_cal", "evt_1", "제목", date(2026, 10, 2)) is False


def test_deleting_a_deleted_event_is_done() -> None:
    client(lambda request: httpx.Response(410, json={})).delete_event("team_cal", "evt_1")


def test_any_other_refusal_is_raised() -> None:
    c = client(lambda request: httpx.Response(403, json={}))
    with pytest.raises(PermanentIntegrationError):
        c.update_all_day_event("team_cal", "evt_1", "제목", date(2026, 10, 2))
    with pytest.raises(PermanentIntegrationError):
        c.delete_event("team_cal", "evt_1")


def test_an_unmasked_phone_number_never_reaches_the_calendar() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"id": "x"})

    with pytest.raises(PrivacyViolationError):
        client(handler).create_all_day_event("team_cal", "010-1234-5678로 연락", date(2026, 10, 2))
    assert calls == []


# --- the fake ---------------------------------------------------------------------


def test_the_fake_answers_like_the_client() -> None:
    fake = FakeCalendar(busy={"in@example.com": []})
    event_id = fake.create_all_day_event("team_cal", "마감", date(2026, 10, 2))

    assert fake.update_all_day_event("team_cal", event_id, "마감 변경", date(2026, 10, 3))
    assert fake.update_all_day_event("team_cal", "evt_gone", "x", date(2026, 10, 3)) is False
    fake.delete_event("team_cal", event_id)
    assert fake.deleted == [event_id]
    assert fake.free_busy(["in@example.com", "out@else.com"], START, END) == {
        "in@example.com": [],
        "out@else.com": None,
    }


# --- Autune's tag and the read-back (#435, per-person calendars) --------------------


def test_a_created_event_carries_autunes_private_tag() -> None:
    sent: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "evt_1"})

    client(handler).create_all_day_event(
        "primary", "[마감] 스펙", date(2026, 10, 2), private={"autune": "act_1"}
    )

    assert sent["extendedProperties"] == {"private": {"autune": "act_1"}}


def test_changed_events_asks_google_for_autunes_events_only_and_pages() -> None:
    """The tag filter is Google's, so the rest of a person's calendar is never
    returned -- this pins that it is actually sent."""
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        if "pageToken" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "evt_1",
                            "start": {"date": "2026-10-05"},
                            "end": {"date": "2026-10-06"},
                            "extendedProperties": {"private": {"autune_item": "act_1"}},
                        }
                    ],
                    "nextPageToken": "p2",
                },
            )
        return httpx.Response(200, json={"items": [{"id": "evt_2", "status": "cancelled"}]})

    events = client(handler).changed_events("primary", updated_min=START, tag=("autune", "1"))

    assert seen[0].params["privateExtendedProperty"] == "autune=1"
    assert seen[0].params["showDeleted"] == "true"
    assert seen[1].params["pageToken"] == "p2"
    assert events[0].start == date(2026, 10, 5)
    assert events[0].private == {"autune_item": "act_1"}
    assert events[1].cancelled
    assert events[1].start is None


def test_a_patch_to_an_event_deleted_by_hand_reports_it_gone() -> None:
    """Google answers a PATCH to a recently deleted event with 200 and the event
    still cancelled -- that is gone, not updated (review of #441)."""
    c = client(lambda request: httpx.Response(200, json={"id": "evt_1", "status": "cancelled"}))
    assert c.update_all_day_event("primary", "evt_1", "제목", date(2026, 10, 2)) is False


def test_a_patch_to_a_live_event_is_an_update() -> None:
    c = client(lambda request: httpx.Response(200, json={"id": "evt_1", "status": "confirmed"}))
    assert c.update_all_day_event("primary", "evt_1", "제목", date(2026, 10, 2)) is True
