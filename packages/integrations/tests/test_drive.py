"""The Drive client: one picked file, read as the person who picked it.

It asks for a file's type and nothing that describes its contents, reads with
a ceiling, and puts neither a name nor a byte into an error.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from autune_integrations.drive import PDF, DriveClient, DriveFileInfo, FileTooLargeError
from autune_integrations.errors import PermanentIntegrationError, TransientIntegrationError
from autune_integrations.fakes import FakeDrive

FILE = "1AbC_dEf-GhIjKlMnOpQrStUvWxYz012345"
GOOGLE_DOC = "application/vnd.google-apps.document"


def client(handler: Callable[[httpx.Request], httpx.Response]) -> DriveClient:
    return DriveClient("ya29.token", http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_info_asks_for_the_type_and_size_and_never_the_name() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"id": FILE, "mimeType": PDF, "size": "2048", "name": "3분기 인사 평가.pdf"},
        )

    info = client(handler).info(FILE)

    (request,) = seen
    assert request.url.path == f"/drive/v3/files/{FILE}"
    assert request.url.params["fields"] == "id,mimeType,size"
    assert request.headers["authorization"] == "Bearer ya29.token"
    assert info == DriveFileInfo(id=FILE, mime_type=PDF, size=2048)
    assert "인사 평가" not in repr(info), "a name Google sent anyway is not kept"


def test_a_google_document_has_no_stored_size() -> None:
    answer = httpx.Response(200, json={"id": FILE, "mimeType": GOOGLE_DOC})

    assert client(lambda r: answer).info(FILE).size is None


def test_download_reads_the_stored_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/drive/v3/files/{FILE}"
        assert request.url.params["alt"] == "media"
        return httpx.Response(200, content=b"%PDF-1.7 the file")

    assert client(handler).download(FILE, max_bytes=1000) == b"%PDF-1.7 the file"


def test_export_asks_drive_to_render_a_pdf() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/drive/v3/files/{FILE}/export"
        assert request.url.params["mimeType"] == PDF
        return httpx.Response(200, content=b"%PDF-1.7 rendered")

    assert client(handler).export_pdf(FILE, max_bytes=1000) == b"%PDF-1.7 rendered"


@pytest.mark.parametrize("read", ["download", "export_pdf"])
def test_a_read_is_given_up_on_past_its_ceiling(read: str) -> None:
    """The size Drive reports is not trusted to be the size it sends."""
    big = httpx.Response(200, content=b"x" * 1001)

    with pytest.raises(FileTooLargeError):
        getattr(client(lambda r: big), read)(FILE, max_bytes=1000)
    exact = httpx.Response(200, content=b"x" * 1000)
    assert len(getattr(client(lambda r: exact), read)(FILE, max_bytes=1000)) == 1000


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (404, PermanentIntegrationError),
        (403, PermanentIntegrationError),
        (401, PermanentIntegrationError),
        (429, TransientIntegrationError),
        (503, TransientIntegrationError),
    ],
)
def test_a_refusal_says_its_status_and_nothing_google_wrote(
    status: int, error: type[Exception]
) -> None:
    answer = httpx.Response(
        status, json={"error": {"message": "File 3분기 인사 평가.pdf not found"}}
    )

    for read in ("info", "download", "export_pdf"):
        with pytest.raises(error) as caught:
            if read == "info":
                client(lambda r: answer).info(FILE)
            else:
                getattr(client(lambda r: answer), read)(FILE, max_bytes=1000)
        assert "인사 평가" not in str(caught.value)
        assert "ya29" not in str(caught.value)
    if error is PermanentIntegrationError:
        assert caught.value.details["upstream_status"] == status  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "bad", ["", "abc", "../../etc/passwd", f"{FILE}/export", f"{FILE}?alt=media"]
)
def test_what_is_not_a_file_id_is_never_put_in_an_address(bad: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"asked Google for {request.url}")

    with pytest.raises(PermanentIntegrationError):
        client(handler).info(bad)
    with pytest.raises(PermanentIntegrationError):
        client(handler).download(bad, max_bytes=10)
    with pytest.raises(PermanentIntegrationError):
        client(handler).export_pdf(bad, max_bytes=10)


def test_a_timeout_is_an_outage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(TransientIntegrationError):
        client(handler).download(FILE, max_bytes=10)


def test_an_answer_that_is_not_json_is_a_refusal() -> None:
    with pytest.raises(PermanentIntegrationError):
        client(lambda r: httpx.Response(200, content=b"<html>")).info(FILE)


# --- the fake answers the way a drive.file grant is answered ---------------------------


def test_the_fake_knows_only_picked_files() -> None:
    drive = FakeDrive(files={FILE: (PDF, b"%PDF")})

    assert drive.info(FILE) == DriveFileInfo(id=FILE, mime_type=PDF, size=4)
    assert drive.download(FILE, max_bytes=10) == b"%PDF"
    with pytest.raises(PermanentIntegrationError) as caught:
        drive.info("1NotPicked_NotPicked_NotPicked")
    assert caught.value.details["upstream_status"] == 404


def test_the_fake_refuses_to_download_a_google_document_and_caps_a_read() -> None:
    drive = FakeDrive(files={FILE: (GOOGLE_DOC, b"x" * 20)})

    assert drive.info(FILE).size is None
    with pytest.raises(PermanentIntegrationError):
        drive.download(FILE, max_bytes=100)
    with pytest.raises(FileTooLargeError):
        drive.export_pdf(FILE, max_bytes=10)
    assert drive.read == [("info", FILE), ("download", FILE), ("export_pdf", FILE)]
