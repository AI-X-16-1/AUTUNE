"""A Drive file the person picked, read with their own grant and shown to
them (provisional, #817; the user, 2026-10-05).

The rules under test: only a PDF, a Google document or a Google presentation
is read, and anything else is refused before a byte of it is; every read has
a ceiling; nothing about the file but its kind and size is logged; the token
and the file are the caller's own and the answers are never cached; a file the
grant cannot reach is 404 whoever owns it; and none of it exists where the
deployment did not turn it on.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from autune_core import Base, Meeting, Team, TeamMember, User, get_session
from autune_core.errors import AutuneError
from autune_extraction import drive_preview
from autune_extraction.config import ExtractionSettings
from autune_extraction.drive_preview import (
    DriveFileTooLargeError,
    DriveNotConnectedError,
    Preview,
    UnsupportedDriveFileError,
)
from autune_extraction.router import router
from autune_integrations.calendar import ReconnectRequiredError
from autune_integrations.drive import PDF
from autune_integrations.fakes import FakeDrive

from .conftest import READER, sign_in

PREFIX = "/api/extraction"
FILE = "1AbC_dEf-GhIjKlMnOpQrStUvWxYz012345"
NOT_PICKED = "1NotPicked_NotPicked_NotPicked_00"
DOCUMENT = "application/vnd.google-apps.document"
PRESENTATION = "application/vnd.google-apps.presentation"
SHEET = "application/vnd.google-apps.spreadsheet"


# --- reading -------------------------------------------------------------------------


def test_a_pdf_is_passed_on_as_it_is() -> None:
    drive = FakeDrive(files={FILE: (PDF, b"%PDF-1.7 the file")})

    preview = drive_preview.read(drive, FILE)

    assert preview == Preview(pdf=b"%PDF-1.7 the file", exported=False)
    assert drive.read == [("info", FILE), ("download", FILE)]


@pytest.mark.parametrize("kind", [DOCUMENT, PRESENTATION])
def test_a_google_document_is_rendered_to_pdf_by_drive(kind: str) -> None:
    drive = FakeDrive(files={FILE: (kind, b"%PDF-1.7 rendered")})

    preview = drive_preview.read(drive, FILE)

    assert preview == Preview(pdf=b"%PDF-1.7 rendered", exported=True)
    assert drive.read == [("info", FILE), ("export_pdf", FILE)]


@pytest.mark.parametrize(
    "kind",
    [SHEET, "image/png", "application/zip", "text/html", "application/vnd.google-apps.folder", ""],
)
def test_anything_else_is_refused_before_a_byte_of_it_is_read(kind: str) -> None:
    drive = FakeDrive(files={FILE: (kind, b"the bytes")})

    with pytest.raises(UnsupportedDriveFileError):
        drive_preview.read(drive, FILE)

    assert drive.read == [("info", FILE)], "the type was asked, the file was not read"


def test_a_file_that_says_it_is_too_large_is_not_read() -> None:
    drive = FakeDrive(files={FILE: (PDF, b"x")})
    drive.info = lambda file_id: SimpleNamespace(  # type: ignore[method-assign, assignment]
        id=file_id, mime_type=PDF, size=drive_preview.MAX_BYTES + 1
    )

    with pytest.raises(DriveFileTooLargeError):
        drive_preview.read(drive, FILE)

    assert drive.read == []


def test_a_file_that_turns_out_too_large_is_given_up_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Google document reports no size; the ceiling is met while reading."""
    monkeypatch.setattr(drive_preview, "MAX_BYTES", 10)
    drive = FakeDrive(files={FILE: (DOCUMENT, b"x" * 11)})

    with pytest.raises(DriveFileTooLargeError):
        drive_preview.read(drive, FILE)


def test_what_is_logged_is_the_kind_and_the_size_and_never_the_file() -> None:
    drive = FakeDrive(files={FILE: (DOCUMENT, b"%PDF-1.7 salaries of everyone")})

    with capture_logs() as logs:
        preview = drive_preview.read(drive, FILE)

    assert logs == [
        {
            "event": "extraction_drive_preview_served",
            "log_level": "info",
            "exported": True,
            "bytes": len(preview.pdf),
        }
    ]
    assert FILE not in repr(logs) and "salaries" not in repr(logs)
    assert "salaries" not in repr(preview), "a preview does not print its bytes"


# --- the person's own grant ------------------------------------------------------------


@pytest.fixture
def grant(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A deployment with a Google client, and one stored Drive grant to vary."""
    state: dict[str, Any] = {
        "row": SimpleNamespace(secret="1//drive", config={"client_id": "the-client"}),
        "credentials": ("the-client", "the-secret"),
        "refreshed": [],
    }

    def load(_session: object, user_id: str, service: str) -> object:
        assert (user_id, service) == (READER, "drive"), "only the asker's own Drive grant"
        return state["row"]

    def refresh(*, client_id: str, client_secret: str, refresh_token: str) -> str:
        state["refreshed"].append((client_id, refresh_token))
        return "ya29.fresh"

    monkeypatch.setattr(drive_preview, "load_user_integration", load)
    monkeypatch.setattr(drive_preview, "refresh_access_token", refresh)
    monkeypatch.setattr(
        drive_preview,
        "get_core_settings",
        lambda: SimpleNamespace(google_integration_credentials=state["credentials"]),
    )
    return state


def test_a_token_is_minted_from_the_askers_own_grant(grant: dict[str, Any]) -> None:
    assert drive_preview.access_token(None, READER) == "ya29.fresh"  # type: ignore[arg-type]
    assert grant["refreshed"] == [("the-client", "1//drive")]


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"row": None}, id="never-connected"),
        pytest.param(
            # A grant from before the client was recorded: nothing else stops it.
            {"credentials": ("", ""), "row": SimpleNamespace(secret="1//drive", config={})},
            id="no-google-client-here",
        ),
        pytest.param(
            {"row": SimpleNamespace(secret="1//drive", config={"client_id": "another-client"})},
            id="issued-to-another-client",
        ),
        pytest.param(
            {
                "row": SimpleNamespace(
                    secret="1//drive", config={"client_id": "the-client", "grant_revoked": True}
                )
            },
            id="revoked-with-another-grant",
        ),
    ],
)
def test_a_grant_that_is_known_to_be_gone_is_not_asked_of_google(
    grant: dict[str, Any], change: dict[str, Any]
) -> None:
    grant.update(change)

    with pytest.raises(DriveNotConnectedError):
        drive_preview.access_token(None, READER)  # type: ignore[arg-type]

    assert grant["refreshed"] == []


def test_a_grant_google_refuses_is_a_connection_to_make_again(
    grant: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(**_: object) -> str:
        raise ReconnectRequiredError("the refresh token 1//drive was refused")

    monkeypatch.setattr(drive_preview, "refresh_access_token", refused)

    with pytest.raises(DriveNotConnectedError) as caught:
        drive_preview.access_token(None, READER)  # type: ignore[arg-type]

    assert "1//drive" not in str(caught.value)


# --- the two routes ------------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Meeting, User, Team, TeamMember)}
    Base.metadata.create_all(
        engine, tables=[t for name, t in Base.metadata.tables.items() if name in shared]
    )
    with Session(engine) as s:
        s.add(Team(id="team_1", name="팀"))
        s.flush()
        yield s


@pytest.fixture
def world(session: Session, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The app with the preview on, a picker configured, and the asker's Drive
    as a fake that knows one picked file."""
    made = SimpleNamespace(
        settings={
            "drive_preview": True,
            "drive_picker_api_key": "AIza-browser-key",
            "drive_app_id": "123456789012",
        },
        drive=FakeDrive(files={FILE: (PDF, b"%PDF-1.7 the file")}),
        connected=True,
        asked_for=[],
    )

    def token(_session: Session, user_id: str) -> str:
        made.asked_for.append(user_id)
        if not made.connected:
            raise DriveNotConnectedError("connect Google Drive first")
        return "ya29.fresh"

    @contextmanager
    def drive_for(_session: Session, user_id: str) -> Iterator[FakeDrive]:
        token(_session, user_id)
        yield made.drive

    monkeypatch.setattr(drive_preview, "access_token", token)
    monkeypatch.setattr(drive_preview, "drive_for", drive_for)
    monkeypatch.setattr(
        "autune_extraction.router.get_settings",
        lambda: ExtractionSettings(_env_file=None, **made.settings),  # type: ignore[call-arg]
    )

    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    made.app = app
    made.anonymous = TestClient(app)
    sign_in(app, session, team_id="team_1")
    made.client = TestClient(app)
    return made


def test_neither_route_exists_where_the_deployment_did_not_turn_it_on(
    world: SimpleNamespace,
) -> None:
    world.settings["drive_preview"] = False

    assert world.client.get(f"{PREFIX}/me/drive/picker").status_code == 404
    assert world.client.get(f"{PREFIX}/me/drive/files/{FILE}").status_code == 404
    assert world.asked_for == [], "no grant is touched for a feature that is off"
    assert ExtractionSettings(_env_file=None).drive_preview is False  # type: ignore[call-arg]


def test_the_picker_is_handed_the_callers_own_token_and_is_never_cached(
    world: SimpleNamespace,
) -> None:
    answer = world.client.get(f"{PREFIX}/me/drive/picker")

    assert answer.json() == {
        "available": True,
        "connected": True,
        "access_token": "ya29.fresh",
        "api_key": "AIza-browser-key",
        "app_id": "123456789012",
    }
    assert answer.headers["cache-control"] == "no-store"
    assert world.asked_for == [READER], "the signed-in person's grant, and nobody else's"


def test_somebody_not_connected_gets_no_token_and_no_error(world: SimpleNamespace) -> None:
    world.connected = False

    answer = world.client.get(f"{PREFIX}/me/drive/picker")

    assert answer.status_code == 200
    assert answer.json() == {
        "available": True,
        "connected": False,
        "access_token": None,
        "api_key": "AIza-browser-key",
        "app_id": "123456789012",
    }


@pytest.mark.parametrize("missing", ["drive_picker_api_key", "drive_app_id"])
def test_no_picker_is_offered_without_the_key_and_the_project_number(
    world: SimpleNamespace, missing: str
) -> None:
    world.settings[missing] = ""

    answer = world.client.get(f"{PREFIX}/me/drive/picker")

    assert answer.json() == {
        "available": False,
        "connected": False,
        "access_token": None,
        "api_key": None,
        "app_id": None,
    }
    assert world.asked_for == [], "no token is minted for a picker that cannot open"


def test_a_picked_file_comes_back_as_a_pdf_that_no_browser_keeps(world: SimpleNamespace) -> None:
    answer = world.client.get(f"{PREFIX}/me/drive/files/{FILE}")

    assert answer.status_code == 200
    assert answer.content == b"%PDF-1.7 the file"
    assert answer.headers["content-type"] == "application/pdf"
    assert answer.headers["cache-control"] == "no-store"
    assert answer.headers["x-content-type-options"] == "nosniff"
    assert answer.headers["content-disposition"] == "inline"
    assert world.asked_for == [READER]


def test_a_file_the_grant_cannot_reach_is_not_found_whoever_owns_it(
    world: SimpleNamespace,
) -> None:
    """With ``drive.file`` that is every file the person did not pick. The
    same answer as a file that does not exist: the route does not say which
    ids are files."""
    answer = world.client.get(f"{PREFIX}/me/drive/files/{NOT_PICKED}")

    assert answer.status_code == 404
    assert answer.json()["error"]["code"] == "not_found"


def test_a_file_that_is_not_a_document_is_refused_as_that(world: SimpleNamespace) -> None:
    world.drive.files[FILE] = ("image/png", b"\x89PNG")

    answer = world.client.get(f"{PREFIX}/me/drive/files/{FILE}")

    assert answer.status_code == 422
    assert answer.json()["error"]["code"] == "drive_file_unsupported"
    assert b"PNG" not in answer.content


def test_somebody_not_connected_is_told_to_connect(world: SimpleNamespace) -> None:
    world.connected = False

    answer = world.client.get(f"{PREFIX}/me/drive/files/{FILE}")

    assert answer.status_code == 409
    assert answer.json()["error"]["code"] == "drive_not_connected"
    assert world.drive.read == []


@pytest.mark.parametrize("bad", ["abc", "..%2F..%2Fetc%2Fpasswd", f"{FILE}%3Falt%3Dmedia"])
def test_what_is_not_a_file_id_never_reaches_drive(world: SimpleNamespace, bad: str) -> None:
    answer = world.client.get(f"{PREFIX}/me/drive/files/{bad}")

    assert answer.status_code in (404, 422)
    assert world.drive.read == [] and world.asked_for == []


def test_nobody_signed_in_gets_neither(world: SimpleNamespace) -> None:
    world.app.dependency_overrides = {
        get_session: world.app.dependency_overrides[get_session],
    }
    anonymous = TestClient(world.app)

    assert anonymous.get(f"{PREFIX}/me/drive/picker").status_code in (401, 403)
    assert anonymous.get(f"{PREFIX}/me/drive/files/{FILE}").status_code in (401, 403)
    assert world.asked_for == [] and world.drive.read == []
