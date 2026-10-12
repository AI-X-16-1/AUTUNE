"""Read the text of a file a member uploaded to the team's 자료 (#817).

**Nine formats.** ``.txt``, ``.md`` and ``.csv`` are read whole, as UTF-8 (a
byte-order mark is dropped) or else CP949; any other encoding is refused.
``.docx``, ``.xlsx``, ``.pptx``, ``.pdf``, ``.xls`` and ``.ppt`` each have a
reader in ``material_formats``, which says what of a file is kept, what is
read for the marking check only and what is not read. ``.doc`` and ``.hwp``
are not read. The server's own list of endings (``SUFFIXES``) is what the
screen offers.

**A file whose text cannot be read is refused whole**, because text that was
not read can be neither checked for a marking nor masked: a password-protected
file, a PDF with a page that is only a picture, a damaged file. The refusal
names a reason code the screen turns into a sentence (``materialUpload.ts``)
and never a byte of the file.

**The refusal is raised here, in one place, with nothing behind it.** A
reader's library can quote the file in its own error -- a cell's value, a
part's name, a line of a broken stream -- so ``read_file`` takes the reason
code out of whatever a reader raised and raises ``UnreadableFileError`` after
that error is let go: the one that leaves has no cause and no context, and a
handler that logs an exception with its chain has nothing of the file to log.

The bytes are a parameter and the text a return value: neither is logged,
cached or put in an exception.
"""

from __future__ import annotations

from autune_core.errors import ValidationError

from .material_formats import (
    MAX_TEXT_CHARS,
    READERS,
    FileText,
    Reason,
    RefusedError,
    top_of,
)

TEXT_SUFFIXES = (".txt", ".md", ".csv")

SUFFIXES = (*TEXT_SUFFIXES, *READERS)
"""The file-name endings taken, with the dot, in lower case."""

MAX_BYTES = 10 * 1024 * 1024
"""Ten megabytes a file (#817; ``docs/architecture/privacy.md``)."""


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


def _decoded(data: bytes | bytearray) -> FileText:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        return FileText(text=text, head=top_of(text))
    raise RefusedError("encoding")


def _read(suffix: str, data: bytes | bytearray) -> FileText | Reason:
    """What the file says, or why it is refused. Whatever a reader raised
    ends here: its reason code is kept and the error is not."""
    reader = _decoded if suffix in TEXT_SUFFIXES else READERS[suffix]
    try:
        return reader(data)
    except RefusedError as refused:
        return refused.reason
    except ImportError:
        # A reader's package is missing from this deployment. That is not the
        # file's fault, and the error names a package, not the file.
        raise
    except Exception:  # noqa: BLE001 -- a broken file fails in a reader's depths, many ways
        return "damaged"


def read_file(file_name: str, data: bytes | bytearray) -> FileText:
    """What an uploaded file says, or ``UnreadableFileError``."""
    suffix = suffix_of(file_name)
    if suffix not in SUFFIXES:
        raise UnreadableFileError("unsupported_type")
    if len(data) > MAX_BYTES:
        raise UnreadableFileError("too_large")
    if not data:
        raise UnreadableFileError("empty")
    found = _read(suffix, data)
    if not isinstance(found, FileText):
        raise UnreadableFileError(found)
    # A NUL or another control character is a binary file under a text name,
    # or text a reader took out of a file wrongly; CP949 decodes much of
    # anything, so the decode alone does not tell.
    if any(char in _CONTROL for char in found.text):
        raise UnreadableFileError("damaged")
    if not found.text.strip():
        raise UnreadableFileError("empty")
    if len(found.text) + len(found.marks) > MAX_TEXT_CHARS:
        raise UnreadableFileError("too_long")
    return found
