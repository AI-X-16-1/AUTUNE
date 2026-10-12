"""The readers of the six document formats an uploaded material may be (#817).

``.docx`` ``.xlsx`` ``.pptx`` are zipped XML and are read with the standard
library. Three small pure-Python packages read the rest, each a line in this
module's ``pyproject.toml``: ``pypdf`` (``.pdf``), ``xlrd`` (``.xls``) and
``olefile`` (``.ppt``). No converter, no office suite, no OCR.
``material_reading.read_file`` is the one way in; nothing else calls a reader.

**Where text hides, and what is done with it** (``FileText``):

- read and kept, masked by the caller (``text``): body text, tables, headers
  and footers, footnotes, comments, tracked deletions, speaker notes, hidden
  slides, hidden sheets, rows and columns, sheet names, cell values (the
  value, not the formula), text boxes, PDF annotations and form fields.
- read for the marking check only, never kept (``marks``): document
  properties (title, subject, keywords, category, custom properties such as a
  sensitivity label), slide masters and layouts, a sheet's print header and
  footer.
- never read: who wrote it -- author, last editor, comment authors, company.
  The one exception is a cell note's author in an Excel file: Excel opens a
  note's own text with that name, so it is read to drop that line and kept
  nowhere (``_without_writers`` has the exact rule).
- not read, and said so by kind (``not_read``): pictures, charts, files
  embedded in the file. A marking that exists only as a picture is not seen.

**The head and the foot** are where a marking is looked for besides the
file's name and ``marks`` (``material_marking``). The head is the header the
format has and the first ``HEAD_LINES`` lines of what the file opens on:

- ``.docx``: its page headers and the first lines of the body; the foot is
  its page footers.
- ``.xlsx`` ``.xls``: the first rows of the first sheet that has any.
- ``.pptx`` ``.ppt``: the first lines of the first slide that has any (for
  ``.ppt``, of the text in the order the file stores it); the foot of a
  ``.pptx`` is each slide's footer placeholder.
- ``.pdf``: the first lines of the first page that has any; the foot is the
  last ``FOOT_LINES`` lines of every page -- a PDF has no footer of its own,
  only what is printed lowest.

An ``.xls`` print footer and an old ``.ppt``'s slide footers are not read by
these readers.

**A PDF with a page that is only a picture is refused whole**: what such a
page says can be neither checked for a marking nor masked. A page with a
picture and fewer than ``MIN_PAGE_CHARS`` characters beside it -- a scan that
carries only its page number -- is such a page.

**What a reader raises.** ``RefusedError`` for what it recognises, by reason
code. Anything else -- its own or its library's -- is a damaged file, and
``material_reading.read_file`` is the one place that says so: a library's
error can quote the file, so none is passed on, chained or logged, and the
libraries' own warnings are not printed.
"""

from __future__ import annotations

import functools
import io
import logging
import re
import struct
import zipfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import IO, Any, Literal, cast
from xml.etree import ElementTree

from .material_marking import head_of

MAX_PDF_PAGES = 300
"""The most pages a PDF may have."""

MAX_UNPACKED = 80 * 1024 * 1024
"""The most a zipped file may unpack to: a small archive can unpack huge."""

MAX_PARSED = 16 * 1024 * 1024
"""The most the parts read from a zipped file may hold between them, unpacked.
A file of a few hundred kilobytes can unpack to tens of megabytes of XML, and
a part is unpacked and gone through to its end, an element at a time, even
when it says nothing. It is not held as a tree while that is done
(``_Package.events``), so this limit bounds the time a read takes and the
little a reader keeps of a part that says nothing, not a tree many times the
part's size. Pictures and other parts that are not read do not count. Text of
the length that is kept is a few megabytes of such parts; a file with more is
refused as ``too_long`` before the part that passes the limit is unpacked."""

MAX_TEXT_CHARS = 300_000
"""The most text a file may hold once read, what is read only for the marking
check counted with it. The masker has no limit of its own and was timed up to
this (#1199); a longer text is refused, not cut. ``material_reading.read_file``
holds every format to it once a file is read; the zipped formats' readers also
stop at it while they read (``_Package.counted``)."""

FOOT_LINES = 2
"""How many lines from the bottom of a PDF page are its footer: a marking and
a page number under it."""

MIN_PAGE_CHARS = 20
"""A PDF page with a picture and fewer characters than this beside it (spaces
not counted) is a picture-only page: "- 3 -", "Page 3 of 12"."""

_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
"""An OLE2 container: an old Office file -- or a password-protected new one,
which is wrapped in one."""

NotRead = Literal["pictures", "charts", "embedded_files"]

Reason = Literal[
    "unsupported_type",  # not a format read here (.doc and .hwp among them)
    "too_large",
    "encoding",  # a text file in neither UTF-8 nor CP949
    "empty",
    "too_long",
    "damaged",
    "protected",  # password-protected or encrypted
    "no_text_layer",  # a PDF of pictures: a scan
    "page_without_text",  # a PDF with text, and a page that is only a picture
]
"""Why a file is refused, as the screen is told (``materialUpload.ts``)."""


class RefusedError(Exception):
    """A file a reader will not read. It carries a reason code and nothing
    else, and it does not leave ``material_reading.read_file``, which answers
    with an error of its own."""

    def __init__(self, reason: Reason) -> None:
        super().__init__(reason)
        self.reason: Reason = reason


@dataclass(frozen=True)
class FileText:
    """What a file says. Every string here is unmasked: ``text`` is masked
    before anything else sees it, and ``head``, ``foot`` and ``marks`` go no
    further than the marking check."""

    text: str
    head: str = ""
    """A copy of the top of ``text``, with the header the format has."""
    foot: str = ""
    """A copy of the page footers in ``text``."""
    marks: str = ""
    """Read only to look for a marking and never stored: properties, masters,
    print headers and footers."""
    not_read: tuple[NotRead, ...] = ()
    """The kinds of content the file holds that were not read."""

    def edges(self) -> list[str]:
        """The lines a marking is looked for in: the head, the foot and the
        file's own label, each line on its own."""
        return [
            line
            for part in (self.head, self.foot, self.marks)
            for line in part.splitlines()
            if line.strip()
        ]


def top_of(text: str) -> str:
    """The first ``HEAD_LINES`` lines of ``text`` that say anything."""
    return "\n".join(head_of(text))


@dataclass
class _Found:
    text: list[str] = field(default_factory=list)
    marks: list[str] = field(default_factory=list)
    not_read: set[NotRead] = field(default_factory=set)
    head: list[str] = field(default_factory=list)
    foot: list[str] = field(default_factory=list)

    def opens_on(self, text: str) -> None:
        """The first page, sheet or slide that has text gives the head its lines."""
        if text.strip() and not any(part.strip() for part in self.head):
            self.head.append(top_of(text))

    def done(self) -> FileText:
        def joined(parts: list[str]) -> str:
            return "\n".join(part.strip("\n") for part in parts if part.strip())

        return FileText(
            text=joined(self.text),
            head=joined(self.head),
            foot=joined(self.foot),
            marks=joined(self.marks),
            not_read=tuple(sorted(self.not_read)),
        )


# --- the zipped XML formats: .docx .xlsx .pptx -------------------------------------


def _natural(name: str) -> list[object]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def _local(tag: str) -> str:
    """A tag without its namespace: the Strict flavour of these formats uses
    other namespace addresses for the same elements."""
    return tag.rsplit("}", 1)[-1]


class _Package:
    """A zipped file's parts, each read as a stream and never held as a tree.

    A sheet of a few megabytes unpacks to tens, and a tree of it takes ten to
    twenty times that in memory (#1252). ``events`` hands a part over an
    element at a time and lets each go once it has closed, so what a read
    holds is the text it keeps and little else. The readers below were written
    as walks down a tree, top first; a streamed element is whole only when it
    closes, bottom first. Where the two orders differ a reader keeps a place
    when an element opens and fills it when the element closes, so what it
    returns is what the walk returned.
    """

    def __init__(self, data: bytes | bytearray) -> None:
        if data.startswith(_OLE):
            raise RefusedError("protected")
        self.archive = zipfile.ZipFile(io.BytesIO(data))
        self.names = sorted(self.archive.namelist(), key=_natural)
        if sum(info.file_size for info in self.archive.infolist()) > MAX_UNPACKED:
            raise RefusedError("too_large")
        self.parsed: dict[str, int] = {}  # each part read, and its unpacked size
        self.looked: set[str] = set()  # each part looked through before it was parsed
        self.ink = 0  # characters read so far that are text or marks and not white space

    def matching(self, pattern: str) -> list[str]:
        return [name for name in self.names if re.fullmatch(pattern, name)]

    def open(self, name: str) -> IO[bytes]:
        """A part, to be parsed as it is unpacked. Before a parser is given
        any of it the part is unpacked once from end to end and looked
        through, a piece at a time."""
        self.parsed[name] = self.archive.getinfo(name).file_size
        if sum(self.parsed.values()) > MAX_PARSED:
            raise RefusedError("too_long")
        if name not in self.looked:
            # These formats declare no entities, and a part that does is
            # refused before a parser expands them. The two words are looked
            # for as UTF-8, which is how Office writes a part; a NUL is a part
            # in a wider encoding, where they would not be found. The end of
            # each piece is looked at once more with the start of the next: a
            # word can lie across the two.
            carried = b""
            with self.archive.open(name) as part:
                while piece := part.read(_PIECE):
                    seam = carried + piece[:_CARRIED]
                    if b"\x00" in piece or any(word in piece or word in seam for word in _DECLARED):
                        raise RefusedError("damaged")
                    carried = (carried + piece[-_CARRIED:])[-_CARRIED:]
            self.looked.add(name)
        return self.archive.open(name)

    def events(
        self, name: str, *, opening: bool = False
    ) -> Iterator[tuple[str, str, ElementTree.Element, int]]:
        """A part's elements in the order the part writes them: each as it
        closes (``"end"``: its attributes and its own text) and, with
        ``opening``, as it opens too (``"start"``: its attributes only). With
        each, its tag without the namespace and how many elements it sits
        inside.

        An element that has closed is taken out of the one above it when the
        caller comes back for the next, so the part is never in memory as a
        tree. **Whatever a caller needs of an element it takes then**: its
        children are gone by the time it closes, and it is gone after."""
        # Entities are refused in ``open``. Only elements are asked for, so an
        # element is all an event carries.
        parser: ElementTree.XMLPullParser[ElementTree.Element] = ElementTree.XMLPullParser(
            events=("start", "end")
        )
        above: list[ElementTree.Element] = []
        with self.open(name) as part:
            while True:
                piece = part.read(_PIECE)
                if piece:
                    parser.feed(piece)
                else:
                    parser.close()  # a part that breaks off is an error here, as it was
                for event, element in cast("Iterator[tuple[str, Any]]", parser.read_events()):
                    tag = _short(element.tag)
                    if event == "start":
                        if opening:
                            yield event, tag, element, len(above)
                        above.append(element)
                        continue
                    above.pop()
                    yield event, tag, element, len(above)
                    if above:
                        # Everything under the element above this one: this
                        # element, and any after it the parser has already
                        # made, which the caller is handed all the same.
                        del above[-1][:]
                if not piece:
                    return

    def counted(self, said: str) -> str:
        """``said``, which is going into the file's text or its marks, counted
        toward ``MAX_TEXT_CHARS``: a file already past it is refused here and
        not read to its end. Only what is not white space is counted, since
        blank lines at the edges of a part are dropped later and nothing else
        is -- so this never refuses a file the length check after the read
        (``material_reading.read_file``) would take, and that check stays."""
        self.ink += len("".join(said.split()))
        if self.ink > MAX_TEXT_CHARS:
            raise RefusedError("too_long")
        return said

    def runs(self, name: str, *, text: frozenset[str] = frozenset({"t"})) -> str:
        """A part's text in document order: every element named in ``text``,
        with a line break after each paragraph, row or line break."""
        said = io.StringIO()
        for _, tag, element, _ in self.events(name):
            if tag in text:
                if element.text:
                    said.write(self.counted(element.text))
            elif tag in ("p", "br", "tr", "row", "comment", "cm"):
                said.write("\n")
            elif tag in ("tab", "tc"):
                said.write("\t")
        return said.getvalue()

    def texts(self, name: str, tags: frozenset[str]) -> list[str]:
        """The text of every element named in ``tags``, in the order they open."""
        found: list[str] = []
        places: dict[int, int] = {}  # how deep an open one sits -> its place in ``found``
        for event, tag, element, depth in self.events(name, opening=True):
            if tag not in tags:
                continue
            if event == "start":
                places[depth] = len(found)
                found.append("")
            else:
                found[places.pop(depth)] = element.text or ""
        return found

    def boxes(
        self,
        name: str,
        box: str,
        *,
        holding: Callable[[str, ElementTree.Element], bool] | None = None,
    ) -> list[str]:
        """What each ``box`` element says: the text of the ``t`` elements in
        it, in the order they open. One string a box, in the order the boxes
        open. With ``holding``, only the boxes that have an element it takes
        in them."""
        found: list[str | None] = []
        said: dict[int, list[str]] = {}  # how deep an open box sits -> its text so far
        places: dict[int, int] = {}  # the same -> its place in ``found``
        held: set[int] = set()  # the open boxes that hold such an element
        spots: dict[int, list[tuple[list[str], int]]] = {}  # an open ``t`` -> its place in each
        for event, tag, element, depth in self.events(name, opening=True):
            if event == "start":
                if tag == box:
                    said[depth], places[depth] = [], len(found)
                    found.append(None)
                if tag == "t" and said:
                    spots[depth] = [(pieces, len(pieces)) for pieces in said.values()]
                    for pieces in said.values():
                        pieces.append("")
                if holding is not None and said and holding(tag, element):
                    held.update(said)
                continue
            if tag == "t" and depth in spots:
                for pieces, spot in spots.pop(depth):
                    pieces[spot] = element.text or ""
            if tag == box:
                pieces, place = said.pop(depth), places.pop(depth)
                if holding is None or depth in held:
                    found[place] = "".join(pieces)
                held.discard(depth)
        return [text for text in found if text is not None]

    def properties(self) -> str:
        """Title, subject, keywords, category, description and custom
        properties (a sensitivity label is one). Not the author."""
        found: list[str] = []
        if "docProps/core.xml" in self.names:
            found += self.texts("docProps/core.xml", _LABELS)
        if "docProps/custom.xml" in self.names:
            # A property is an element straight under the part's first: its
            # name, then what each element straight under it says.
            for event, _, element, depth in self.events("docProps/custom.xml", opening=True):
                if event == "start" and depth == 1:
                    found.append(element.get("name", ""))
                elif event == "end" and depth == 2:
                    found.append(element.text or "")
        return "\n".join(self.counted(text) for text in found)

    def footer_boxes(self, name: str) -> str:
        """The text of a slide's footer placeholder."""
        return "\n".join(self.boxes(name, "sp", holding=_footer_placeholder))

    def besides(self, folder: str, found: _Found) -> None:
        """What sits in the package beside its text and is not read."""
        kinds: tuple[tuple[NotRead, str], ...] = (
            ("pictures", "media"),
            ("charts", "charts"),
            ("embedded_files", "embeddings"),
        )
        for kind, part in kinds:
            if any(name.startswith(f"{folder}/{part}/") for name in self.names):
                found.not_read.add(kind)


_PIECE = 64 * 1024
"""How much of a part is unpacked at a time, to be looked through and then to
be parsed."""

_DECLARED = (b"<!DOCTYPE", b"<!ENTITY")
_CARRIED = max(len(word) for word in _DECLARED) - 1
"""How much of the end of a piece is looked at again with the next."""

_short = functools.lru_cache(maxsize=256)(_local)
"""``_local``, with the last few hundred answers remembered: these formats use
a few dozen tags and a part can hold millions of elements. A part made of
other names cannot make the list longer than that."""

_LABELS = frozenset({"title", "subject", "keywords", "category", "description", "contentStatus"})


def _footer_placeholder(tag: str, element: ElementTree.Element) -> bool:
    return tag == "ph" and element.get("type") == "ftr"


_WORD_TEXT = frozenset({"t", "delText"})


def _docx(data: bytes | bytearray) -> FileText:
    package, found = _Package(data), _Found()
    if "word/document.xml" not in package.names:
        raise RefusedError("damaged")
    body = package.runs("word/document.xml", text=_WORD_TEXT)
    headers = [
        package.runs(part, text=_WORD_TEXT) for part in package.matching(r"word/header\d*\.xml")
    ]
    footers = [
        package.runs(part, text=_WORD_TEXT) for part in package.matching(r"word/footer\d*\.xml")
    ]
    parts = package.matching(r"word/(footnotes|endnotes|comments)\.xml")
    found.text = [
        body,
        *headers,
        *footers,
        *(package.runs(part, text=_WORD_TEXT) for part in parts),
    ]
    found.opens_on(body)
    found.head += headers
    found.foot += footers
    found.marks.append(package.properties())
    package.besides("word", found)
    return found.done()


def _number(value: str) -> str:
    """A cell's stored number as its digits: ``1.023456789E9`` is how a phone
    number typed into a number cell is written down, and no detector reads
    that."""
    if not re.fullmatch(r"-?\d+(\.\d+)?[eE][+-]?\d+", value) and not value.endswith(".0"):
        return value
    try:
        number = Decimal(value)
    except InvalidOperation:
        return value
    return str(int(number)) if number == number.to_integral_value() else format(number, "f")


def _shared_strings(package: _Package) -> list[str]:
    """The workbook's table of strings: an item is an element straight under
    the part's first, and says what its ``t`` elements say -- the ones
    straight under it and the ones straight under a run (``r``) of its."""
    if "xl/sharedStrings.xml" not in package.names:
        return []
    strings: list[str] = []
    pieces: list[str] = []
    in_run = False
    for event, tag, element, depth in package.events("xl/sharedStrings.xml", opening=True):
        if event == "start":
            if depth == 2:  # a formatted run; ``rPh`` (a reading aid) is skipped
                in_run = tag == "r"
        elif tag == "t" and (depth == 2 or (depth == 3 and in_run)):
            pieces.append(element.text or "")
        elif depth == 1:
            strings.append("".join(pieces))
            pieces = []
    return strings


_PRINT_EDGES = frozenset(
    {"oddHeader", "oddFooter", "evenHeader", "evenFooter", "firstHeader", "firstFooter"}
)


def _sheet(package: _Package, name: str, shared: list[str]) -> tuple[str, list[str]]:
    """A sheet: its rows, a line each with a tab between cells, and what its
    print headers and footers say.

    A cell is any element straight under a row, and says what its first ``v``
    says -- or, when it keeps its text in itself (``inlineStr``), what the
    ``t`` elements in it say. Rows, cells and the rest are found however deep
    they sit and whatever they sit in, as a walk of the whole tree found them.
    """
    rows: list[tuple[int, str]] = []  # a row that has cells: when it opened, and its line
    opened = 0  # rows opened so far
    edges: list[str] = []
    places: dict[int, int] = {}  # an open row -> when it opened; a print edge -> its place
    cells: dict[int, list[str]] = {}  # how deep an open row sits -> its cells so far
    values: dict[int, str] = {}  # an open cell -> what its first ``v`` says
    inline: dict[int, list[str]] = {}  # an open cell with its text in itself -> that text
    spots: dict[int, list[tuple[list[str], int]]] = {}  # an open ``t`` -> its place in each
    for event, tag, element, depth in package.events(name, opening=True):
        if event == "start":
            if depth - 1 in cells and element.get("t") == "inlineStr":
                inline[depth] = []
            if tag == "row":  # hidden rows and columns are rows and cells like any other
                cells[depth], places[depth] = [], opened
                opened += 1
            elif tag in _PRINT_EDGES:
                places[depth] = len(edges)
                edges.append("")
            elif tag == "t" and inline:
                spots[depth] = [(pieces, len(pieces)) for pieces in inline.values()]
                for pieces in inline.values():
                    pieces.append("")
            continue
        if tag == "t" and depth in spots:
            for pieces, spot in spots.pop(depth):
                pieces[spot] = element.text or ""
        elif tag == "v" and depth - 2 in cells:
            values.setdefault(depth - 1, element.text or "")
        if depth - 1 in cells:
            kind, value = element.get("t"), values.pop(depth, "")
            row = cells[depth - 1]
            if kind == "s":
                row.append(package.counted(shared[int(value)]))
            elif kind == "inlineStr":
                row.append(package.counted("".join(inline.pop(depth))))
            elif value:
                row.append(package.counted(_number(value) if kind in (None, "n") else value))
        if tag == "row":
            mine, place = cells.pop(depth), places.pop(depth)
            if mine:
                rows.append((place, "\t".join(mine)))
        elif tag in _PRINT_EDGES:
            edges[places.pop(depth)] = package.counted(element.text or "")
    # A row inside a row closes before it: the lines go in the order the rows opened.
    rows.sort()
    return "\n".join(line for _, line in rows), edges


def _xlsx(data: bytes | bytearray) -> FileText:
    package, found = _Package(data), _Found()
    if "xl/workbook.xml" not in package.names:
        raise RefusedError("damaged")
    # Every sheet's name, hidden ones too.
    found.text.append(
        "\n".join(
            package.counted(element.get("name", ""))
            for event, tag, element, _ in package.events("xl/workbook.xml", opening=True)
            if event == "start" and tag == "sheet"
        )
    )
    shared = _shared_strings(package)
    for part in package.matching(r"xl/worksheets/sheet\d+\.xml"):
        rows, edges = _sheet(package, part, shared)
        found.marks += edges
        found.text.append(rows)
        found.opens_on(rows)
    notes: list[str] = []
    authors: list[str] = []
    for part in package.matching(r"xl/comments\d*\.xml"):
        notes += package.boxes(part, "comment")
        authors += package.texts(part, frozenset({"author"}))
    # Counted after the writers' lines are out: what is dropped is not text.
    found.text += [package.counted(note) for note in _without_writers(notes, authors)]
    for part in package.matching(r"xl/drawings/drawing\d+\.xml"):
        found.text.append(package.runs(part))
    for part in package.matching(r"xl/threadedComments/[^/]+\.xml"):
        found.text.append(package.runs(part, text=frozenset({"text"})))
    found.marks.append(package.properties())
    package.besides("xl", found)
    return found.done()


def _without_writers(notes: list[str], authors: Iterable[str]) -> list[str]:
    """Cell notes without the line Excel opens a new note with: its writer's
    name and a colon, which stays in the note's own text unless the writer
    deletes it. A note's first line is dropped only when it is exactly one of
    the file's note authors and a colon. A name anywhere else in a note -- on
    the same line as its text, or of somebody the file does not list as a
    note's author -- is the note's text and stays, like a name in a cell. The
    authors are compared here and go nowhere else."""
    opening = {f"{author.strip()}:" for author in authors if author.strip()}
    kept: list[str] = []
    for note in notes:
        first, _, rest = note.lstrip().partition("\n")
        kept.append(rest if first.strip() in opening else note)
    return kept


_SLIDE_TEXT = frozenset({"t", "text"})


def _pptx(data: bytes | bytearray) -> FileText:
    package, found = _Package(data), _Found()
    slides = package.matching(r"ppt/slides/slide\d+\.xml")
    if not slides:
        raise RefusedError("damaged")
    for part in slides:  # hidden slides are slides like any other
        found.text.append(package.runs(part))
        found.opens_on(found.text[-1])
        found.foot.append(package.footer_boxes(part))
    for pattern in (
        r"ppt/notesSlides/notesSlide\d+\.xml",  # speaker notes
        r"ppt/comments/[^/]+\.xml",
        r"ppt/diagrams/data\d+\.xml",  # SmartArt
    ):
        found.text += [package.runs(part, text=_SLIDE_TEXT) for part in package.matching(pattern)]
    for pattern in (
        r"ppt/slideMasters/slideMaster\d+\.xml",
        r"ppt/slideLayouts/slideLayout\d+\.xml",
        r"ppt/notesMasters/notesMaster\d+\.xml",
        r"ppt/handoutMasters/handoutMaster\d+\.xml",
    ):
        found.marks += [package.runs(part) for part in package.matching(pattern)]
    found.marks.append(package.properties())
    package.besides("ppt", found)
    return found.done()


# --- PDF --------------------------------------------------------------------------


def _pdf(data: bytes | bytearray) -> FileText:
    from pypdf import PdfReader

    # The reader narrates a broken file as it goes; none of that is ours to print.
    # Stopped at the source: its loggers take their level from this one.
    logging.getLogger("pypdf").setLevel(logging.CRITICAL + 1)
    found = _Found()
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        # Many PDFs are locked for editing, not for reading.
        opened: int
        try:
            opened = reader.decrypt("")
        except Exception:  # noqa: BLE001 -- an algorithm this build cannot open
            opened = 0
        if not opened:
            raise RefusedError("protected")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise RefusedError("too_long")
    with_text = only_a_picture = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        pictured = _pdf_pictured(page)
        if pictured and len("".join(text.split())) < MIN_PAGE_CHARS:
            only_a_picture += 1  # nothing beside the picture, or only a page number
        elif text.strip():
            with_text += 1
        if pictured:
            found.not_read.add("pictures")
        found.text.append(text)
        found.opens_on(text)
        found.foot += [line for line in text.splitlines() if line.strip()][-FOOT_LINES:]
        for note in page.get("/Annots") or []:
            found.text.append(str(note.get_object().get("/Contents") or ""))
    for value in (reader.get_fields() or {}).values():
        found.text.append(str(value.get("/V") or ""))
    info: Mapping[str, Any] = reader.metadata or {}
    found.marks += [str(info.get(key) or "") for key in ("/Title", "/Subject", "/Keywords")]
    root = cast("Mapping[str, Any]", reader.trailer["/Root"])
    if "/EmbeddedFiles" in (root.get("/Names") or {}):
        found.not_read.add("embedded_files")
    if only_a_picture:  # what such a page says can be neither checked nor masked
        raise RefusedError("page_without_text" if with_text else "no_text_layer")
    return found.done()


def _pdf_pictured(page: Any) -> bool:
    """Whether a page holds a picture: an image object in its resources or in
    a form object those hold, however deep, or an image written inline into
    its content or into such a form's. A scanned page is often one of the
    last two. Nothing is decoded, so no imaging package is needed. Not looked
    at: a picture in an annotation's appearance or in a pattern."""
    from pypdf.generic import ContentStream, DictionaryObject, StreamObject

    def held(owner: Any, key: str) -> Any:
        value = owner.get(key)
        value = value.get_object() if value is not None else None
        return value if isinstance(value, DictionaryObject) else {}

    def inline(content: Any) -> bool:
        return content is not None and any(
            operator == b"INLINE IMAGE" for _, operator in content.operations
        )

    forms: dict[int, Any] = {}  # by identity: a form may hold itself
    waiting: list[Any] = [page]
    while waiting:
        for thing in held(held(waiting.pop(), "/Resources"), "/XObject").values():
            thing = thing.get_object()
            if not isinstance(thing, StreamObject):
                continue
            if thing.get("/Subtype") == "/Image":
                return True
            if thing.get("/Subtype") == "/Form" and id(thing) not in forms:
                forms[id(thing)] = thing
                waiting.append(thing)
    return inline(page.get_contents()) or any(
        inline(ContentStream(form, page.pdf)) for form in forms.values()
    )


# --- the old binary formats: .xls .ppt ---------------------------------------------


def _xls(data: bytes | bytearray) -> FileText:
    import xlrd

    found = _Found()
    try:
        # The reader's own warnings can quote the file; they go nowhere.
        book = xlrd.open_workbook(file_contents=bytes(data), logfile=io.StringIO())
    except xlrd.XLRDError as exc:
        # Which of the reader's complaints this is, and nothing else of it.
        raise RefusedError("protected" if "ncrypt" in str(exc) else "damaged") from None
    found.text.append("\n".join(sheet.name for sheet in book.sheets()))  # hidden ones too
    authors = [note.author for sheet in book.sheets() for note in sheet.cell_note_map.values()]
    for sheet in book.sheets():
        rows: list[str] = []
        for number in range(sheet.nrows):
            cells = [
                _number(repr(value)) if isinstance(value, float) else str(value)
                for value in sheet.row_values(number)
                if value != ""
            ]
            if cells:
                rows.append("\t".join(cells))
        found.text.append("\n".join(rows))
        found.opens_on("\n".join(rows))
        found.text += _without_writers(
            [note.text for note in sheet.cell_note_map.values()], authors
        )
    return found.done()


_PPT_TEXT_CHARS, _PPT_TEXT_BYTES, _PPT_CSTRING = 0x0FA0, 0x0FA8, 0x0FBA
_PPT_MAIN_MASTER, _PPT_SLIDE_LIST, _PPT_COMMENT = 0x03F8, 0x0FF0, 0x2EE0
_PPT_ENCRYPTED = 0xF3D1C4DF


def _ppt(data: bytes | bytearray) -> FileText:
    """An old PowerPoint file: the text records of its one document stream.

    The stream keeps earlier saves, so text deleted from a slide can still be
    in the file and is read with the rest -- checked and masked like it.
    """
    import olefile

    found = _Found()
    ole = olefile.OleFileIO(io.BytesIO(data))
    if not ole.exists("PowerPoint Document"):
        raise RefusedError("damaged")
    if ole.exists("Current User"):
        user = ole.openstream("Current User").read()
        if len(user) >= 16 and struct.unpack_from("<I", user, 12)[0] == _PPT_ENCRYPTED:
            raise RefusedError("protected")
    stream = ole.openstream("PowerPoint Document").read()
    if ole.exists("Pictures"):
        found.not_read.add("pictures")
    if any(entry[0].startswith(("MBD", "\x01Ole")) for entry in ole.listdir()):
        found.not_read.add("embedded_files")

    seen: set[str] = set()

    def walk(start: int, end: int, *, master: bool, comment: bool) -> None:
        position = start
        while position + 8 <= end:
            version, kind, length = struct.unpack_from("<HHI", stream, position)
            body, after = position + 8, position + 8 + length
            if after > end:
                return  # a record that runs past its container: stop, keep what was read
            if version & 0xF == 0xF:  # a container: its records follow
                walk(
                    body,
                    after,
                    master=master
                    or kind == _PPT_MAIN_MASTER
                    or (kind == _PPT_SLIDE_LIST and version >> 4 == 1),
                    comment=kind == _PPT_COMMENT,
                )
            else:
                text = None
                if kind == _PPT_TEXT_CHARS:
                    text = stream[body:after].decode("utf-16-le", "replace")
                elif kind == _PPT_TEXT_BYTES:
                    text = stream[body:after].decode("latin-1")
                elif kind == _PPT_CSTRING and comment and version >> 4 == 1:
                    # The comment, not its author.
                    text = stream[body:after].decode("utf-16-le", "replace")
                if text and text not in seen:
                    seen.add(text)
                    clean = re.sub(r"[\r\x0b]", "\n", text)
                    (found.marks if master else found.text).append(clean)
            position = after

    walk(0, len(stream), master=False, comment=False)
    found.opens_on("\n".join(found.text))  # the file's own order, which need not be the slides'
    return found.done()


READERS: Mapping[str, Callable[[bytes | bytearray], FileText]] = {
    ".docx": _docx,
    ".xlsx": _xlsx,
    ".pptx": _pptx,
    ".pdf": _pdf,
    ".xls": _xls,
    ".ppt": _ppt,
}
"""The reader of each ending. Called only by ``material_reading.read_file``."""
