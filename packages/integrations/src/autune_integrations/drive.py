"""Google Drive: reading a file a person picked, as that person (#817).

One caller today: module B's provisional preview route, which shows a picked
file to the person who picked it. The grant is ``drive.file`` -- the files the
person chose in Google's own file picker and nothing else in their Drive -- so
every call here is about one file id that person handed over.

**What is read is the person's own document, not meeting content -- and it is
not Autune's to keep.** The bytes come back to the caller in memory. Nothing
here stores them, and nothing here puts a file's name or a byte of it into a
log line or an exception message: an error says what Google's status was and
no more, because a name is the person's and a body may be anything.

**Not through ``HttpClient.request`` for the bytes.** That method parses JSON
and refuses raw content going OUT, which is the right default for meeting
content; a download is raw content coming IN. The requests carry a file id
and constant parameters only -- nothing from a meeting -- and are made on a
plain client with the same timeouts and the same split of errors.

**Every read has a ceiling.** A response is read as it arrives and given up
on past ``max_bytes``: the size Drive reports is not trusted to be the size it
sends, and a Google document has no size to report at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .base import DEFAULT_TIMEOUT
from .errors import PermanentIntegrationError, TransientIntegrationError

SERVICE = "google_drive"
BASE_URL = "https://www.googleapis.com/drive/v3"

PDF = "application/pdf"

FILE_ID = re.compile(r"[A-Za-z0-9_-]{10,200}")
"""A Drive file id. Checked before it is put in a path: an id is the only part
of an address here that came from outside."""


class FileTooLargeError(PermanentIntegrationError):
    """The file is longer than the caller said it would read."""

    code = "drive_file_too_large"


@dataclass(frozen=True)
class DriveFileInfo:
    """What kind of file an id is. Its type and nothing that describes its
    contents: the name is requested by nobody here, so it cannot be logged."""

    id: str
    mime_type: str
    size: int | None
    """Bytes, as Drive reports it; ``None`` for a Google document, which has
    no stored size."""


class DriveFiles(Protocol):
    """What a module depends on. ``FakeDrive`` implements it for tests."""

    def info(self, file_id: str) -> DriveFileInfo: ...

    def download(self, file_id: str, *, max_bytes: int) -> bytes: ...

    def export_pdf(self, file_id: str, *, max_bytes: int) -> bytes: ...


def _checked(file_id: str) -> str:
    if not FILE_ID.fullmatch(file_id):
        raise PermanentIntegrationError(f"{SERVICE}: that is not a file id")
    return file_id


class DriveClient:
    """Reads with one person's own access token (``drive.file``)."""

    def __init__(self, access_token: str, *, http: httpx.Client | None = None) -> None:
        self._client = http or httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=False)
        self._headers = {"Authorization": f"Bearer {access_token}"}

    def info(self, file_id: str) -> DriveFileInfo:
        """The file's type and stored size. ``fields`` asks for those and the
        id only: no name, no owner, no sharing."""
        status, body = self._get(
            f"/files/{_checked(file_id)}", {"fields": "id,mimeType,size"}, max_bytes=10_000
        )
        raw = _json(status, body)
        size = raw.get("size")
        return DriveFileInfo(
            id=str(raw.get("id", file_id)),
            mime_type=str(raw.get("mimeType", "")),
            size=int(size) if isinstance(size, str | int) and str(size).isdigit() else None,
        )

    def download(self, file_id: str, *, max_bytes: int) -> bytes:
        """The stored bytes of a file that has them (a PDF, an upload)."""
        status, body = self._get(
            f"/files/{_checked(file_id)}", {"alt": "media"}, max_bytes=max_bytes
        )
        _raise_for(status)
        return body

    def export_pdf(self, file_id: str, *, max_bytes: int) -> bytes:
        """A Google document, presentation or sheet as Drive renders it to PDF."""
        status, body = self._get(
            f"/files/{_checked(file_id)}/export", {"mimeType": PDF}, max_bytes=max_bytes
        )
        _raise_for(status)
        return body

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict[str, str], *, max_bytes: int) -> tuple[int, bytes]:
        try:
            with self._client.stream(
                "GET", BASE_URL + path, params=params, headers=self._headers
            ) as response:
                if response.status_code != 200:
                    # The body may name the file or echo the request: not read.
                    return response.status_code, b""
                body = bytearray()
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > max_bytes:
                        raise FileTooLargeError(f"{SERVICE}: the file is larger than can be shown")
                return 200, bytes(body)
        except httpx.TimeoutException as exc:
            raise TransientIntegrationError(f"{SERVICE} timed out") from exc
        except httpx.TransportError as exc:
            raise TransientIntegrationError(f"{SERVICE} is unreachable") from exc


def _raise_for(status: int) -> None:
    if status == 200:
        return
    if status == 429 or status >= 500:
        raise TransientIntegrationError(f"{SERVICE} returned {status}")
    # 403 and 404 are the ordinary answers for a file this grant cannot reach
    # -- with ``drive.file``, any file the person did not pick.
    raise PermanentIntegrationError(
        f"{SERVICE} rejected the request with {status}", upstream_status=status
    )


def _json(status: int, body: bytes) -> dict[str, Any]:
    _raise_for(status)
    try:
        parsed = httpx.Response(200, content=body).json()
    except ValueError as exc:
        raise PermanentIntegrationError(f"{SERVICE} answered with something unreadable") from exc
    if not isinstance(parsed, dict):
        raise PermanentIntegrationError(f"{SERVICE} answered with something unreadable")
    return parsed
