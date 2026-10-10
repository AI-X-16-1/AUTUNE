"""Take an uploaded file off the request, in memory only (#817).

``docs/architecture/privacy.md``, "Uploaded documents are masked before
storage, and the original is not kept": the uploaded bytes belong to the
upload request and end with it. So this reads the request's body itself:

- **No temporary file at any size.** FastAPI's ``UploadFile`` is a spooled
  temporary file that moves to disk past a megabyte; it is not used. The body
  is parsed as it arrives (``python_multipart``'s streaming parser, which only
  hands parts to callbacks) into ``bytearray`` buffers in this process.
- **The size cap holds as the body arrives**, whatever length the request
  declared: a declared length over the cap is refused before a byte is read,
  and reading stops once the file part passes ``MAX_BYTES``.
- **Exactly two parts, ``title`` and ``file``.** Anything else is refused.
- **Wiped on every path out.** ``Upload.wipe`` overwrites and empties the
  buffers; the route calls it in a ``finally``, and a refusal here wipes what
  it had read before it raises. What Python cannot overwrite -- the immutable
  ``bytes`` chunks the server handed over, the decoded ``str`` -- is dropped
  with the request and never referenced from anything that outlives it.

Nothing here logs, and no error carries a byte of the body, the file's name or
the title: a refusal names the rule.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header

from autune_core.errors import AutuneError, ValidationError

from .material_reading import MAX_BYTES, UnreadableFileError

MAX_TITLE_BYTES = 2048
"""A title part's bytes. ``materials.MAX_TITLE_CHARS`` is the real limit; this
only stops a title part from being a second file."""

FORM_OVERHEAD_BYTES = 64 * 1024
"""What a two-part form adds around the file: boundaries, part headers, the
title. A body may be this much larger than ``MAX_BYTES``."""

MAX_BODY_BYTES = MAX_BYTES + FORM_OVERHEAD_BYTES

PARTS = ("title", "file")


class UploadTooLargeError(AutuneError):
    """The body passed the cap. Answered 413, which the screen reads as "too
    large" (``materialUpload.ts``)."""

    code = "too_large"
    status_code = 413

    def __init__(self) -> None:
        super().__init__(f"a file is at most {MAX_BYTES} bytes", field="file", reason="too_large")


def _malformed() -> ValidationError:
    return ValidationError("an upload is a form of two parts, title and file", field="file")


def _wipe(buffer: bytearray) -> None:
    buffer[:] = bytes(len(buffer))
    buffer.clear()


@dataclass
class Upload:
    """What a member sent: the title as typed, the file's name -- read for its
    ending and the marking check, then dropped -- and the file's bytes."""

    title: str
    file_name: str
    data: bytearray = field(repr=False)

    def __repr__(self) -> str:
        # A traceback or a debugger prints this; nothing a member sent is in it.
        return f"Upload(bytes={len(self.data)})"

    def wipe(self) -> None:
        _wipe(self.data)


class _Part:
    def __init__(self) -> None:
        self.headers: dict[bytes, bytes] = {}
        self.data = bytearray()


class _FormReader:
    """The two parts of an upload form, as the body arrives."""

    def __init__(self, boundary: bytes) -> None:
        self.parts: list[_Part] = []
        self._field = bytearray()
        self._value = bytearray()
        self._parser = MultipartParser(
            boundary,
            {
                "on_part_begin": self._begin,
                "on_header_field": self._header_field,
                "on_header_value": self._header_value,
                "on_header_end": self._header_end,
                "on_part_data": self._data,
            },
            max_size=MAX_BODY_BYTES,
        )

    def _begin(self) -> None:
        if len(self.parts) == len(PARTS):
            raise _malformed()
        self.parts.append(_Part())

    def _header_field(self, data: bytes, start: int, end: int) -> None:
        self._field += data[start:end]

    def _header_value(self, data: bytes, start: int, end: int) -> None:
        self._value += data[start:end]

    def _header_end(self) -> None:
        self.parts[-1].headers[bytes(self._field).lower()] = bytes(self._value)
        self._field.clear()
        self._value.clear()

    def _data(self, data: bytes, start: int, end: int) -> None:
        part = self.parts[-1]
        part.data += data[start:end]
        name = _name(part)
        if name == "title" and len(part.data) > MAX_TITLE_BYTES:
            raise ValidationError("the title is too long", field="title")
        if name == "file" and len(part.data) > MAX_BYTES:
            raise UploadTooLargeError
        if name not in PARTS:
            raise _malformed()

    def feed(self, chunk: bytes) -> None:
        try:
            self._parser.write(chunk)
        except MultipartParseError as exc:
            raise _malformed() from exc

    def finish(self) -> Upload:
        try:
            self._parser.finalize()
        except MultipartParseError as exc:
            raise _malformed() from exc
        named = {_name(part): part for part in self.parts}
        if len(self.parts) != len(PARTS) or set(named) != set(PARTS):
            raise _malformed()
        try:
            title = named["title"].data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("the title is not text", field="title") from exc
        file_name = _file_name(named["file"])
        if not file_name:
            raise UnreadableFileError("unsupported_type")
        data = named["file"].data
        named["file"].data = bytearray()
        return Upload(title=title, file_name=file_name, data=data)

    def wipe(self) -> None:
        for part in self.parts:
            _wipe(part.data)


def _disposition(part: _Part) -> dict[bytes, bytes]:
    _, options = parse_options_header(part.headers.get(b"content-disposition", b""))
    return options


def _name(part: _Part) -> str | None:
    name = _disposition(part).get(b"name")
    return name.decode("utf-8", errors="replace") if name is not None else None


def _file_name(part: _Part) -> str:
    name = _disposition(part).get(b"filename")
    return name.decode("utf-8", errors="replace").strip() if name else ""


async def read_upload(
    content_type: str, declared_length: str | None, body: AsyncIterator[bytes]
) -> Upload:
    """The title and the file of an upload form, read from ``body`` into
    memory. The caller owns the result and wipes it in a ``finally``."""
    kind, options = parse_options_header(content_type)
    boundary = options.get(b"boundary")
    if kind != b"multipart/form-data" or not boundary:
        raise _malformed()
    declared = (declared_length or "").strip()
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise UploadTooLargeError
    reader = _FormReader(boundary)
    received = 0
    try:
        async for chunk in body:
            received += len(chunk)
            if received > MAX_BODY_BYTES:
                raise UploadTooLargeError
            reader.feed(chunk)
        return reader.finish()
    finally:
        # On success ``finish`` moved the file's buffer into the result;
        # what is left here is the title and, after a refusal, everything.
        reader.wipe()
