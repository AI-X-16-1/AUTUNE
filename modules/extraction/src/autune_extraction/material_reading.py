"""Read the text of a file a member uploaded to the team's 자료 (#817).

**Plain text only for now: ``.txt``, ``.md``, ``.csv``.** Read whole, as
UTF-8 (a byte-order mark is dropped) or else CP949; any other encoding is
refused. The Office formats and PDF that ``docs/modules/extraction.md`` lists
need readers of their own -- and, for PDF and the old binary formats,
packages this module does not depend on yet -- so they come in a later
change, each with the per-format rules that document gives (what is kept,
what is read for the marking check only, what is refused). Until then the
server's own list of suffixes (``SUFFIXES``) is what the screen offers.

**A file whose text cannot be read is refused whole**, because text that was
not read can be neither checked for a marking nor masked. The refusal names a
reason code the screen turns into a sentence (``materialUpload.ts``) and never
a byte of the file.

The bytes are a parameter and the text a return value: neither is logged,
cached or put in an exception.
"""

from __future__ import annotations

from typing import Literal

from autune_core.errors import ValidationError

SUFFIXES = (".txt", ".md", ".csv")
"""The file-name endings taken, with the dot, in lower case."""

MAX_BYTES = 10 * 1024 * 1024
"""Ten megabytes a file (#817; ``docs/architecture/privacy.md``)."""

MAX_TEXT_CHARS = 300_000
"""The most text a file may hold once read. The masker has no limit of its own
and was timed up to this (#1199); a longer text is refused, not cut."""

Reason = Literal[
    "unsupported_type",
    "too_large",
    "encoding",
    "empty",
    "too_long",
    "damaged",
]


class UnreadableFileError(ValidationError):
    """A file refused before anything of it was kept. The message ends with
    the reason code, which the details carry too; neither holds the file."""

    def __init__(self, reason: Reason) -> None:
        super().__init__(f"the file cannot be read: {reason}", field="file")
        self.details["reason"] = reason
        self.reason = reason


def suffix_of(file_name: str) -> str:
    """The ending a file's type is read from: ``.txt``, in lower case; ``""``
    for none. A name that is only an ending (``.txt``) has none -- the rule
    ``suffixOf`` in ``materialUpload.ts`` follows."""
    dot = file_name.rfind(".")
    return "" if dot <= 0 else file_name[dot:].lower()


_CONTROL = frozenset(chr(code) for code in range(32)) - {"\t", "\n", "\r", "\f"}


def _decoded(data: bytes | bytearray) -> str:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnreadableFileError("encoding")


def read_text(file_name: str, data: bytes | bytearray) -> str:
    """The text of an uploaded file, or ``UnreadableFileError``."""
    if suffix_of(file_name) not in SUFFIXES:
        raise UnreadableFileError("unsupported_type")
    if len(data) > MAX_BYTES:
        raise UnreadableFileError("too_large")
    if not data:
        raise UnreadableFileError("empty")
    text = _decoded(data)
    # A NUL or another control character is a binary file under a text name;
    # CP949 decodes much of anything, so the decode alone does not tell.
    if any(char in _CONTROL for char in text):
        raise UnreadableFileError("damaged")
    if not text.strip():
        raise UnreadableFileError("empty")
    if len(text) > MAX_TEXT_CHARS:
        raise UnreadableFileError("too_long")
    return text
