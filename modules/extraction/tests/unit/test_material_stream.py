"""A zipped material is read as a stream, and reads as it did as a tree (#1252).

``.docx`` ``.xlsx`` ``.pptx`` were read by parsing each part into a tree and
walking it. They are now read an element at a time, with nothing of a part
kept but its text (``material_formats._Package.events``). What a file says
must not change by a character, so the readers as they were are kept here --
``TreePackage`` and the three ``tree_`` readers, word for word but for their
names -- and every file below is read by both and the two compared: what is
returned, or how the reader ends.

One thing differs, and only for a file refused either way: a streamed reader
stops at the length limit as soon as the text passes it, where a tree reader
went to the end of the part first. So a file that is too long *and* broken
further on is refused for its length, not as damaged.

Every value here is invented.
"""

from __future__ import annotations

import io
import random
import re
import tracemalloc
import zipfile
from collections.abc import Callable, Iterable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree

import pytest

from autune_extraction import material_formats
from autune_extraction.material_formats import (
    _OLE,
    MAX_PARSED,
    MAX_UNPACKED,
    FileText,
    NotRead,
    RefusedError,
    _Found,
    _local,
    _natural,
)
from autune_extraction.material_reading import MAX_TEXT_CHARS, UnreadableFileError, read_file

from .test_material_formats import (
    AUTHOR,
    POI,
    A,
    P,
    S,
    W,
    core,
    deck_parts,
    noted,
    package,
    sheet,
    slide,
    word,
    workbook,
    workbook_parts,
)

Parts = dict[str, str | bytes]
Reader = Callable[[bytes], FileText]

# --- the readers as they were: each part parsed into a tree -----------------------------


class TreePackage:
    def __init__(self, data: bytes | bytearray) -> None:
        if data.startswith(_OLE):
            raise RefusedError("protected")
        self.archive = zipfile.ZipFile(io.BytesIO(data))
        self.names = sorted(self.archive.namelist(), key=_natural)
        if sum(info.file_size for info in self.archive.infolist()) > MAX_UNPACKED:
            raise RefusedError("too_large")
        self.parsed: dict[str, int] = {}  # each part read, and its unpacked size

    def matching(self, pattern: str) -> list[str]:
        return [name for name in self.names if re.fullmatch(pattern, name)]

    def read(self, name: str) -> bytes:
        self.parsed[name] = self.archive.getinfo(name).file_size
        if sum(self.parsed.values()) > MAX_PARSED:
            raise RefusedError("too_long")
        part = self.archive.read(name)
        # These formats declare no entities, and a part that does is refused
        # before a parser expands them. The two words are looked for as
        # UTF-8, which is how Office writes a part; a NUL is a part in a wider
        # encoding, where they would not be found.
        if b"\x00" in part or b"<!DOCTYPE" in part or b"<!ENTITY" in part:
            raise RefusedError("damaged")
        return part

    def tree(self, name: str) -> ElementTree.Element:
        return ElementTree.fromstring(self.read(name))  # noqa: S314 -- entities refused above

    def runs(self, name: str, *, text: frozenset[str] = frozenset({"t"})) -> str:
        """A part's text in document order: every element named in ``text``,
        with a line break after each paragraph, row or line break."""
        pieces: list[str] = []
        for _, element in ElementTree.iterparse(io.BytesIO(self.read(name))):  # noqa: S314
            tag = _local(element.tag)
            if tag in text:
                pieces.append(element.text or "")
            elif tag in ("p", "br", "tr", "row", "comment", "cm"):
                pieces.append("\n")
            elif tag in ("tab", "tc"):
                pieces.append("\t")
        return "".join(pieces)

    def properties(self) -> str:
        """Title, subject, keywords, category, description and custom
        properties (a sensitivity label is one). Not the author."""
        found: list[str] = []
        if "docProps/core.xml" in self.names:
            for element in self.tree("docProps/core.xml").iter():
                if _local(element.tag) in (
                    "title",
                    "subject",
                    "keywords",
                    "category",
                    "description",
                    "contentStatus",
                ):
                    found.append(element.text or "")
        if "docProps/custom.xml" in self.names:
            for prop in self.tree("docProps/custom.xml"):
                found.append(prop.get("name", ""))
                found.extend(value.text or "" for value in prop)
        return "\n".join(found)

    def footer_boxes(self, name: str) -> str:
        """The text of a slide's footer placeholder."""
        boxes: list[str] = []
        for shape in self.tree(name).iter():
            if _local(shape.tag) == "sp" and any(
                _local(part.tag) == "ph" and part.get("type") == "ftr" for part in shape.iter()
            ):
                boxes.append("".join(t.text or "" for t in shape.iter() if _local(t.tag) == "t"))
        return "\n".join(boxes)

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


_WORD_TEXT = frozenset({"t", "delText"})


def tree_docx(data: bytes | bytearray) -> FileText:
    package, found = TreePackage(data), _Found()
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


def tree_shared_strings(package: TreePackage) -> list[str]:
    if "xl/sharedStrings.xml" not in package.names:
        return []
    strings: list[str] = []
    for item in package.tree("xl/sharedStrings.xml"):
        pieces: list[str] = []
        for child in item:
            tag = _local(child.tag)
            if tag == "t":
                pieces.append(child.text or "")
            elif tag == "r":  # a formatted run; ``rPh`` (a reading aid) is skipped
                pieces.extend(t.text or "" for t in child if _local(t.tag) == "t")
        strings.append("".join(pieces))
    return strings


_PRINT_EDGES = frozenset(
    {"oddHeader", "oddFooter", "evenHeader", "evenFooter", "firstHeader", "firstFooter"}
)


def tree_xlsx(data: bytes | bytearray) -> FileText:
    package, found = TreePackage(data), _Found()
    if "xl/workbook.xml" not in package.names:
        raise RefusedError("damaged")
    # Every sheet's name, hidden ones too.
    found.text.append(
        "\n".join(
            element.get("name", "")
            for element in package.tree("xl/workbook.xml").iter()
            if _local(element.tag) == "sheet"
        )
    )
    shared = tree_shared_strings(package)
    for part in package.matching(r"xl/worksheets/sheet\d+\.xml"):
        rows: list[str] = []
        for element in package.tree(part).iter():
            tag = _local(element.tag)
            if tag == "row":  # hidden rows and columns are rows and cells like any other
                cells: list[str] = []
                for cell in element:
                    kind = cell.get("t")
                    value = next((v.text or "" for v in cell if _local(v.tag) == "v"), "")
                    if kind == "s":
                        cells.append(shared[int(value)])
                    elif kind == "inlineStr":
                        cells.append(
                            "".join(t.text or "" for t in cell.iter() if _local(t.tag) == "t")
                        )
                    elif value:
                        cells.append(_number(value) if kind in (None, "n") else value)
                if cells:
                    rows.append("\t".join(cells))
            elif tag in _PRINT_EDGES:
                found.marks.append(element.text or "")
        found.text.append("\n".join(rows))
        found.opens_on("\n".join(rows))
    notes = [package.tree(part) for part in package.matching(r"xl/comments\d*\.xml")]
    found.text += _without_writers(
        [
            "".join(t.text or "" for t in comment.iter() if _local(t.tag) == "t")
            for tree in notes
            for comment in tree.iter()
            if _local(comment.tag) == "comment"
        ],
        (
            author.text or ""
            for tree in notes
            for author in tree.iter()
            if _local(author.tag) == "author"
        ),
    )
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


def tree_pptx(data: bytes | bytearray) -> FileText:
    package, found = TreePackage(data), _Found()
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


READERS: dict[str, tuple[Reader, Reader]] = {
    ".docx": (tree_docx, material_formats._docx),
    ".xlsx": (tree_xlsx, material_formats._xlsx),
    ".pptx": (tree_pptx, material_formats._pptx),
}


def outcome(reader: Reader, data: bytes) -> FileText | str:
    """What a reader returns, or how it ends: a refusal's reason, or the
    name of anything else it raises (``read_file`` calls all of those a
    damaged file)."""
    try:
        return reader(data)
    except RefusedError as refused:
        return refused.reason
    except Exception as error:  # noqa: BLE001 -- how it ends is what is compared
        return type(error).__name__


def same(suffix: str, data: bytes) -> FileText | str:
    """The streamed reader's outcome, having checked it is the tree reader's."""
    tree, stream = READERS[suffix]
    was, now = outcome(tree, data), outcome(stream, data)
    assert now == was
    return now


# --- files a real program wrote, and the files the other tests build -----------------


@pytest.mark.parametrize("name", ["SampleShow.pptx", "SampleSS.xlsx", "comments.xlsx"])
def test_a_file_office_wrote_reads_as_it_did(name: str) -> None:
    found = same(Path(name).suffix, (POI / name).read_bytes())

    assert isinstance(found, FileText) and found.text


ODD_STRINGS = (
    f"<sst {S}><si><t>첫 문자열</t></si>"
    "<si><r><t>굵은</t></r><r><rPr/><t> 글</t></r><rPh><t>읽기</t></rPh><phoneticPr/></si>"
    "<si><t/></si><si><x><t>깊은</t></x></si><t>항목이 아닌 것</t></sst>"
)
ODD_SHEET = (
    f"<worksheet {S}><sheetData>"
    '<row><c t="s"><v>0</v></c><c t="s"><v>1</v></c><c><v>1.0</v></c>'
    '<c t="n"><v>1.023456789E9</v></c><c t="s"><v>4</v></c></row>'
    '<row><c t="str"><f>A1</f><v>수식 값</v></c><c t="b"><v>1</v></c><c><v></v></c><c/></row>'
    '<row hidden="1"><c t="inlineStr"><is><r><t>굵은 </t></r><r><t>글</t></r>'
    "<rPh><t>읽기</t></rPh></is></c></row>"
    '<row><c><v>첫째</v><v>둘째</v></c><c t="inlineStr"><is><t>밖</t>'
    '<row><c t="inlineStr"><is><t>안</t></is></c><c><v>7</v></c><t t="inlineStr">셀인 t</t></row>'
    "<t>뒤</t></is></c><oddHeader>행 안의 머리</oddHeader></row>"
    '<row/><row><c t="s"><v>2</v></c></row><row><c t="s"><v>3</v></c><x><v>9</v></x></row>'
    "</sheetData><headerFooter><oddHeader>&amp;C머리</oddHeader><oddFooter>바닥</oddFooter>"
    "<evenHeader/><firstFooter>첫 쪽</firstFooter></headerFooter>"
    "<extLst><row><c><v>밖의 행</v></c></row></extLst></worksheet>"
)
ODD_NOTES = (
    f"<comments {S}><authors><author>{AUTHOR}</author><author> 김 </author><author/></authors>"
    f'<commentList><comment ref="A1"><text><r><t>{AUTHOR}:</t></r><r><t>\n메모</t></r></text>'
    "</comment><comment><text/></comment>"
    "<comment><text><t>김:\n둘째</t><comment><text><t>안쪽</t></text></comment><t>끝</t></text>"
    "</comment></commentList><author>목록 밖의 이름</author></comments>"
)
ODD_PROPERTIES = (
    '<Properties xmlns="urn:x"><property name="등급"><lpwstr>사내용</lpwstr><x>둘째 값</x>'
    "</property><property><lpwstr><deep>깊은 값</deep></lpwstr></property>"
    '<property name="빈 것"/></Properties>'
)
ODD_CORE = core(title="제목", subject="주제", keywords="열쇠말", description="설명").replace(
    "</cp:coreProperties>", "<title>안에 <title>또</title></title></cp:coreProperties>"
)


def odd_workbook() -> Parts:
    return {
        "xl/workbook.xml": workbook(("가", "visible"), ("나", "hidden"), ("", "veryHidden")),
        "xl/sharedStrings.xml": ODD_STRINGS,
        "xl/worksheets/sheet1.xml": ODD_SHEET,
        "xl/worksheets/sheet2.xml": sheet(),
        "xl/worksheets/sheet10.xml": sheet("<row><c><v>열째 시트</v></c></row>"),
        "xl/comments1.xml": ODD_NOTES,
        "xl/drawings/drawing1.xml": (
            f"<xdr:wsDr xmlns:xdr='urn:d' {A}><xdr:sp><xdr:txBody><a:p><a:r><a:t>상자</a:t></a:r>"
            "<a:br/><a:r><a:t>둘째 줄</a:t></a:r></a:p></xdr:txBody></xdr:sp></xdr:wsDr>"
        ),
        "xl/threadedComments/threadedComment1.xml": (
            f"<ThreadedComments {S}><threadedComment><text>댓글</text></threadedComment>"
            "<threadedComment><text/></threadedComment></ThreadedComments>"
        ),
        "xl/media/image1.png": b"\x89PNG",
        "docProps/core.xml": ODD_CORE,
        "docProps/custom.xml": ODD_PROPERTIES,
    }


def odd_document() -> Parts:
    body = (
        "<w:p><w:r><w:t>첫 문단</w:t><w:tab/><w:t>탭 뒤</w:t><w:br/><w:t/></w:r></w:p>"
        "<w:p><w:del><w:r><w:delText>지운 글</w:delText></w:r></w:del>"
        "<w:ins><w:r><w:t>넣은 글</w:t></w:r></w:ins></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>칸 1</w:t></w:r></w:p></w:tc><w:tc><w:tbl><w:tr><w:tc>"
        "<w:p><w:r><w:t>안쪽 표</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:tc></w:tr></w:tbl>"
        "<w:p/><w:p><w:r><w:t xml:space='preserve'>  앞뒤 빈칸  </w:t></w:r></w:p>"
    )
    return {
        "word/document.xml": f"<w:document {W}><w:body>{body}</w:body></w:document>",
        "word/header1.xml": f"<w:hdr {W}><w:p><w:r><w:t>머리말</w:t></w:r></w:p></w:hdr>",
        "word/header2.xml": f"<w:hdr {W}><w:p/></w:hdr>",
        "word/footer1.xml": f"<w:ftr {W}><w:p><w:r><w:t>꼬리말</w:t></w:r></w:p></w:ftr>",
        "word/footnotes.xml": f"<w:footnotes {W}><w:footnote><w:p><w:r><w:t>각주</w:t></w:r></w:p>"
        "</w:footnote></w:footnotes>",
        "word/endnotes.xml": f"<w:endnotes {W}/>",
        "word/comments.xml": f"<w:comments {W}><w:comment><w:p><w:r><w:t>검토 의견</w:t></w:r>"
        "</w:p></w:comment></w:comments>",
        "word/embeddings/sheet.bin": b"\x00\x01",
        "docProps/core.xml": ODD_CORE,
        "docProps/custom.xml": ODD_PROPERTIES,
    }


def box(text: str, kind: str | None = None, inside: str = "") -> str:
    """A shape; with ``kind`` a placeholder of that kind."""
    holder = f'<p:nvSpPr><p:nvPr><p:ph type="{kind}"/></p:nvPr></p:nvSpPr>' if kind else ""
    return (
        f"<p:sp>{holder}<p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody>{inside}</p:sp>"
    )


def odd_deck() -> Parts:
    shapes = (
        box("제목", "title")
        + box("바닥글", "ftr")
        + "<p:grpSp>"
        + box("묶음 안 바닥글", "ftr", inside=box("상자 안의 상자"))
        + "</p:grpSp>"
        + box("바깥", inside=box("안쪽 바닥글", "ftr"))
        + box("", "ftr")
        + "<a:t>상자 밖의 글</a:t>"
    )
    first = f"<p:sld {P} {A}><p:cSld><p:spTree>{shapes}</p:spTree></p:cSld></p:sld>"
    return {
        "ppt/slides/slide1.xml": first,
        "ppt/slides/slide2.xml": slide("둘째 장", "둘째 줄"),
        "ppt/slides/slide10.xml": slide(),
        "ppt/notesSlides/notesSlide1.xml": slide("발표자 노트"),
        "ppt/comments/comment1.xml": f"<p:cmLst {P}><p:cm><p:text>슬라이드 댓글</p:text></p:cm>"
        "</p:cmLst>",
        "ppt/diagrams/data1.xml": f"<dgm:dataModel xmlns:dgm='urn:g' {A}><a:t>도식</a:t>"
        "</dgm:dataModel>",
        "ppt/slideMasters/slideMaster1.xml": slide("마스터 글"),
        "ppt/slideLayouts/slideLayout1.xml": slide("레이아웃 글"),
        "ppt/charts/chart1.xml": "<c/>",
        "docProps/core.xml": ODD_CORE,
        "docProps/custom.xml": ODD_PROPERTIES,
    }


def many_rows() -> Parts:
    """Parts several times the piece a parser is fed at once (16 KiB)."""
    rows = "".join(
        f'<row><c><v>{n}</v></c><c t="inlineStr"><is><t>글 {n}</t></is></c>'
        f'<c t="s"><v>{n % 3}</v></c></row>'
        for n in range(4000)
    )
    strings = "".join(f"<si><t>공유 {n}</t></si>" for n in range(3))
    notes = "".join(
        f"<comment><text><t>{AUTHOR}:\n메모 {n}</t></text></comment>" for n in range(2000)
    )
    return {
        "xl/workbook.xml": workbook(("가", "visible")),
        "xl/sharedStrings.xml": f"<sst {S}>{strings}</sst>",
        "xl/worksheets/sheet1.xml": sheet(rows),
        "xl/comments1.xml": f"<comments {S}><authors><author>{AUTHOR}</author></authors>"
        f"<commentList>{notes}</commentList></comments>",
    }


BUILT: list[tuple[str, str, Callable[[], Parts]]] = [
    ("the other tests' workbook", ".xlsx", workbook_parts),
    (
        "the other tests' notes",
        ".xlsx",
        lambda: noted(f"{AUTHOR}:\n검토 바랍니다.", "이름 없는 메모"),
    ),
    ("the other tests' deck", ".pptx", deck_parts),
    ("a workbook with text in odd places", ".xlsx", odd_workbook),
    ("a document with text in odd places", ".docx", odd_document),
    ("a deck with boxes in boxes", ".pptx", odd_deck),
    ("a workbook of four thousand rows", ".xlsx", many_rows),
    (
        "a document of four thousand paragraphs",
        ".docx",
        lambda: {"word/document.xml": word(*(f"문단 {n}" for n in range(4000)))},
    ),
    (
        "a slide of two thousand boxes",
        ".pptx",
        lambda: {
            "ppt/slides/slide1.xml": f"<p:sld {P} {A}>"
            + "".join(box(f"상자 {n}", "ftr" if n % 7 == 0 else None) for n in range(2000))
            + "</p:sld>"
        },
    ),
]


@pytest.mark.parametrize(
    ("suffix", "parts"), [(s, p) for _, s, p in BUILT], ids=[n for n, _, _ in BUILT]
)
def test_a_file_built_part_by_part_reads_as_it_did(suffix: str, parts: Callable[[], Parts]) -> None:
    found = same(suffix, package(parts()))

    assert isinstance(found, FileText) and found.text


def test_the_odd_places_are_read() -> None:
    """The comparison above would pass on two readers that both read
    nothing: a few of the odd places, by name."""
    found = material_formats._xlsx(package(odd_workbook()))

    lines = found.text.splitlines()
    assert lines[:2] == ["가", "나"], "the sheets' names; the third has none"
    assert lines[2] == "첫 문자열\t굵은 글\t1\t1023456789\t", (
        "a string's reading aid is not the string"
    )
    assert "첫째\t밖안셀인 t뒤" in lines, "the first value of a cell; all of an inline cell's text"
    assert "메모" in lines and f"{AUTHOR}:" not in found.text
    assert "머리" in found.marks and "사내용" in found.marks and found.not_read == ("pictures",)


# --- trees no program would write -----------------------------------------------------

WORDS = (
    "",
    "0",
    "1",
    "2",
    "3",
    "5",
    "1.0",
    "1.5E3",
    "가나",
    "대외비",
    f"{AUTHOR}:",
    " ",
    "줄\n바꿈",
    "<&>",
)
KINDS = ("s", "inlineStr", "n", "str", "b")


def grown(
    rng: random.Random, tags: tuple[str, ...], space: str, *, top: int, deep: int, level: int = 0
) -> ElementTree.Element:
    """A tree of the given tags, any of them anywhere."""
    element = ElementTree.Element(space + rng.choice(tags))
    if rng.random() < 0.3:
        element.set("t", rng.choice(KINDS))
    if rng.random() < 0.3:
        element.set("type", rng.choice(("ftr", "title")))
    if rng.random() < 0.3:
        element.set("name", rng.choice(WORDS))
    if rng.random() < 0.7:
        element.text = rng.choice(WORDS)
    if element.get("t") == "s" and rng.random() < 0.9:
        # Most cells that point at a shared string point at one that is there.
        ElementTree.SubElement(element, space + "v").text = rng.choice("012345")
    if level < deep:
        for _ in range(top if level == 0 else rng.randrange(4)):
            child = grown(rng, tags, space, top=top, deep=deep, level=level + 1)
            if rng.random() < 0.2:
                child.tail = rng.choice(WORDS)
            element.append(child)
    return element


def part(rng: random.Random, tags: str, *, top: int = 6, deep: int = 6) -> bytes:
    space = rng.choice(("", "{urn:any}"))
    tree = grown(rng, tuple(tags.split()), space, top=top, deep=deep)
    return ElementTree.tostring(tree, encoding="utf-8")


SHEET = "sheetData row row c c c v v is t r rPh f oddHeader oddFooter x"
SHAPES = "spTree sp sp grpSp nvSpPr nvPr ph ph txBody p r t t br x"
WORD = "body p p r t t delText br tab tbl tr tc ins comment x"
PROPERTIES = {
    "docProps/core.xml": "title subject creator keywords description contentStatus x",
    "docProps/custom.xml": "property lpwstr x",
}


def any_file(rng: random.Random, suffix: str, top: int, deep: int) -> Parts:
    made: Parts = {name: part(rng, tags) for name, tags in PROPERTIES.items() if rng.random() < 0.5}
    if suffix == ".xlsx":
        made |= {
            "xl/workbook.xml": part(rng, "sheets sheet sheet x"),
            "xl/sharedStrings.xml": part(rng, "si si t r rPh x"),
            "xl/worksheets/sheet1.xml": part(rng, SHEET, top=top, deep=deep),
            "xl/worksheets/sheet2.xml": part(rng, SHEET),
            "xl/comments1.xml": part(rng, "authors author commentList comment comment text r t x"),
            "xl/drawings/drawing1.xml": part(rng, SHAPES),
            "xl/threadedComments/t1.xml": part(rng, "threadedComment text x"),
        }
    elif suffix == ".docx":
        made |= {
            "word/document.xml": part(rng, WORD, top=top, deep=deep),
            "word/header1.xml": part(rng, WORD),
            "word/footer1.xml": part(rng, WORD),
            "word/footnotes.xml": part(rng, WORD),
            "word/comments.xml": part(rng, WORD),
        }
    else:
        made |= {
            "ppt/slides/slide1.xml": part(rng, SHAPES, top=top, deep=deep),
            "ppt/slides/slide2.xml": part(rng, SHAPES),
            "ppt/notesSlides/notesSlide1.xml": part(rng, SHAPES + " text"),
            "ppt/comments/c1.xml": part(rng, "cm cm text x"),
            "ppt/slideMasters/slideMaster1.xml": part(rng, SHAPES),
        }
    return made


@pytest.mark.parametrize("suffix", [".docx", ".xlsx", ".pptx"])
def test_any_tree_reads_as_it_did(suffix: str) -> None:
    """Two hundred files a format: its own tags, any of them anywhere, with
    any text. The last few have a first part of some thousands of elements,
    so that a parser has made many of them before the reader is handed the
    first. A reader may refuse one or fail on it; it does so as it did."""
    read = 0
    for seed in range(200):
        rng = random.Random(f"{suffix} {seed}")
        top, deep = (3000, 2) if seed >= 195 else (6, 6)
        found = same(suffix, package(any_file(rng, suffix, top, deep)))
        read += isinstance(found, FileText)

    assert read >= 100, "most of these have to be read for the comparison to say much"


# --- a part is not held as a tree ------------------------------------------------------


def test_what_has_closed_is_gone_from_the_element_above_it() -> None:
    opened = material_formats._Package(
        package({"a.xml": b"<a xmlns='urn:made-up'><r><c>x</c></r><r><c>y</c></r><r/></a>"})
    )
    first: list[ElementTree.Element] = []
    closed = []
    for event, tag, element, depth in opened.events("a.xml", opening=True):
        if event == "start" and depth == 0:
            first.append(element)
        if event == "end":
            closed.append((tag, element.text, depth, len(element)))

    assert closed == [
        ("c", "x", 2, 0),
        ("r", None, 1, 0),
        ("c", "y", 2, 0),
        ("r", None, 1, 0),
        ("r", None, 1, 0),
        ("a", None, 0, 0),
    ]
    assert len(first[0]) == 0


def held(rows: int) -> tuple[int, int, int]:
    """A part of ``rows`` rows gone through: its length, how many times an
    element was handed over, and the most memory taken on the way."""
    body = b"<a>" + b"<r><c>x</c><c>y</c></r>" * rows + b"</a>"
    opened = material_formats._Package(package({"a.xml": body}))
    tracemalloc.start()
    try:
        events = sum(1 for _ in opened.events("a.xml", opening=True))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return len(body), events, peak


def test_a_part_is_gone_through_without_being_held() -> None:
    """What a read holds is what the parser made of one piece of the part,
    some two megabytes, however long the part is. As a tree a part takes
    fourteen times its length: eight megabytes for the shorter one here,
    thirty-two for the longer."""
    _, events, short = held(25_000)
    length, _, long = held(100_000)

    assert events == 2 * (1 + 3 * 25_000)
    assert long < 1.5 * short, "a part four times as long takes no more"
    assert long < 2 * length


@pytest.mark.parametrize("size", [7, 64, 1000])
@pytest.mark.parametrize(
    ("suffix", "parts"), [(s, p) for _, s, p in BUILT[:6]], ids=[n for n, _, _ in BUILT[:6]]
)
def test_a_part_reads_the_same_whatever_piece_it_is_parsed_in(
    suffix: str, parts: Callable[[], Parts], size: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A part goes to the parser a piece at a time, and a piece ends anywhere:
    in a tag, in a word, in a character. With a small piece the parser has
    made nothing beyond the element it hands over; with a large one, all the
    part."""
    monkeypatch.setattr(material_formats, "_PIECE", size)

    assert isinstance(same(suffix, package(parts())), FileText)


def test_a_part_of_more_names_than_are_remembered_is_read_right() -> None:
    names = [f"n{number}" for number in range(1000)]
    body = "<a xmlns='urn:made-up'>" + "".join(f"<{name}/>" for name in names * 2) + "</a>"
    opened = material_formats._Package(package({"a.xml": body}))

    assert [tag for _, tag, _, _ in opened.events("a.xml")] == [*names, *names, "a"]


# --- a file past the length is not read to its end -------------------------------------


def long_rows(chars: int) -> str:
    cell = '<row><c t="inlineStr"><is><t>' + "가" * 1000 + "</t></is></c></row>"
    return cell * (chars // 1000)


def one_sheet(body: str) -> bytes:
    return package(
        {"xl/workbook.xml": workbook(("가", "visible")), "xl/worksheets/sheet1.xml": body}
    )


def reason(name: str, data: bytes) -> str:
    with pytest.raises(UnreadableFileError) as refused:
        read_file(name, data)
    return refused.value.reason


def test_a_sheet_past_the_length_is_refused_before_its_end() -> None:
    """The sheet breaks off after its text. A reader that went to its end
    met the break and called the file damaged; this one has refused it for
    its length before then. Refused either way: only the reason differs."""
    broken = one_sheet(f"<worksheet {S}><sheetData>" + long_rows(MAX_TEXT_CHARS + 1000))
    short = one_sheet(f"<worksheet {S}><sheetData>" + long_rows(1000))

    assert outcome(tree_xlsx, broken) == "ParseError"
    assert reason("a.xlsx", broken) == "too_long"
    assert reason("a.xlsx", short) == "damaged", "a short one is read to its break, as before"


LONG = MAX_TEXT_CHARS + 1000
WORKBOOK = workbook(("가", "visible"))
OPEN_SHEET = f"<worksheet {S}><sheetData>"  # breaks off: a reader that reaches its end fails there
PLACES: dict[str, tuple[str, Parts]] = {
    "a number in a cell": (
        "a.xlsx",
        {
            "xl/workbook.xml": WORKBOOK,
            "xl/worksheets/sheet1.xml": OPEN_SHEET
            + "<row><c><v>1234567890</v></c></row>" * (LONG // 10),
        },
    ),
    "a shared string a cell shows": (
        "a.xlsx",
        {
            "xl/workbook.xml": WORKBOOK,
            "xl/sharedStrings.xml": f"<sst {S}><si><t>{'가' * 1000}</t></si></sst>",
            "xl/worksheets/sheet1.xml": OPEN_SHEET
            + '<row><c t="s"><v>0</v></c></row>' * (LONG // 1000),
        },
    ),
    "a sheet's print header": (
        "a.xlsx",
        {
            "xl/workbook.xml": WORKBOOK,
            "xl/worksheets/sheet1.xml": f"<worksheet {S}><headerFooter><oddHeader>{'가' * LONG}"
            "</oddHeader></headerFooter><sheetData>",
        },
    ),
    "the sheets' names": (
        "a.xlsx",
        {
            "xl/workbook.xml": f"<workbook {S}><sheets>"
            + f'<sheet name="{"가" * 1000}"/>' * (LONG // 1000)
            + "</sheets></workbook>",
            "xl/worksheets/sheet1.xml": OPEN_SHEET,
        },
    ),
    "a sheet's notes": (
        "a.xlsx",
        {
            "xl/workbook.xml": WORKBOOK,
            "xl/worksheets/sheet1.xml": f"<worksheet {S}><sheetData/></worksheet>",
            "xl/comments1.xml": f"<comments {S}><commentList>"
            + f"<comment><text><t>{'가' * 1000}</t></text></comment>" * (LONG // 1000)
            + "</commentList></comments>",
            "xl/drawings/drawing1.xml": "<xdr:wsDr xmlns:xdr='urn:made-up'>",
        },
    ),
    "a document's paragraphs": (
        "a.docx",
        {
            "word/document.xml": f"<w:document {W}><w:body>"
            + f"<w:p><w:r><w:t>{'가' * 1000}</w:t></w:r></w:p>" * (LONG // 1000),
        },
    ),
}


@pytest.mark.parametrize("place", PLACES)
def test_text_past_the_length_is_refused_wherever_it_is(place: str) -> None:
    """Each of these breaks off after text that passes the length, in the
    same part or in one read later. A reader that went on met the break."""
    name, parts = PLACES[place]
    data = package(parts)

    assert outcome(READERS[Path(name).suffix][0], data) == "ParseError"
    assert reason(name, data) == "too_long"


def test_a_file_long_only_in_what_is_read_for_its_marking_is_refused_for_its_length() -> None:
    """Nothing in the body and a title past the length. Read to its end it
    was refused as empty, the check that comes first; now the title is
    counted as it is read. Refused either way: only the reason differs."""
    data = package(
        {
            "word/document.xml": f"<w:document {W}><w:body/></w:document>",
            "docProps/core.xml": "<cp:coreProperties xmlns:cp='urn:made-up' xmlns:dc='urn:other'>"
            f"<dc:title>{'가' * LONG}</dc:title></cp:coreProperties>",
        }
    )

    was = outcome(tree_docx, data)
    assert isinstance(was, FileText) and was.text == "" and len(was.marks) == LONG
    assert reason("a.docx", data) == "too_long"


def test_a_part_is_looked_through_whole_before_any_of_it_is_read() -> None:
    """The declaration is after the text that passes the length: it is found
    first all the same."""
    late = one_sheet(sheet(long_rows(MAX_TEXT_CHARS + 1000)) + "<!-- <!ENTITY -->")

    assert reason("a.xlsx", late) == "damaged"


def test_the_reader_stops_one_character_past_the_length() -> None:
    def cell(chars: int) -> bytes:
        return one_sheet(
            sheet('<row><c t="inlineStr"><is><t>' + "x" * chars + "</t></is></c></row>")
        )

    at = material_formats._xlsx(cell(MAX_TEXT_CHARS - 1))  # and the sheet's name, one character
    with pytest.raises(RefusedError) as refused:
        material_formats._xlsx(cell(MAX_TEXT_CHARS))

    assert len("".join(at.text.split())) == MAX_TEXT_CHARS
    assert refused.value.reason == "too_long"


def test_blank_lines_a_part_is_made_of_do_not_count() -> None:
    """A part that is only line breaks is dropped from the text, so a file
    at the length with such a part beside it is taken, as it was."""
    parts: Parts = {
        "word/document.xml": word("x" * (MAX_TEXT_CHARS - 100)),
        "word/footnotes.xml": word("\n" * 500),
    }

    found = same(".docx", package(parts))
    taken = read_file("a.docx", package(parts))

    assert found == taken and len(taken.text) == MAX_TEXT_CHARS - 100


def test_a_string_no_cell_shows_does_not_count() -> None:
    strings = "".join(f"<si><t>{'가' * 1000}</t></si>" for _ in range(400))
    parts: Parts = {
        "xl/workbook.xml": workbook(("가", "visible")),
        "xl/sharedStrings.xml": f"<sst {S}>{strings}</sst>",
        "xl/worksheets/sheet1.xml": sheet('<row><c t="s"><v>7</v></c></row>'),
    }

    found = same(".xlsx", package(parts))

    assert isinstance(found, FileText) and len(found.text) == 1002
    assert read_file("a.xlsx", package(parts)) == found


def test_the_line_that_names_a_notes_writer_does_not_count() -> None:
    """Forty thousand notes that each open with their writer's name: the
    names are 280,000 characters that are dropped, the notes 40,000 that are
    kept."""
    notes = "".join(f"<comment><text><t>{AUTHOR}:\n가</t></text></comment>" for _ in range(40_000))
    parts: Parts = {
        "xl/workbook.xml": workbook(("가", "visible")),
        "xl/worksheets/sheet1.xml": sheet("<row><c><v>1</v></c></row>"),
        "xl/comments1.xml": f"<comments {S}><authors><author>{AUTHOR}</author></authors>"
        f"<commentList>{notes}</commentList></comments>",
    }

    found = same(".xlsx", package(parts))

    assert isinstance(found, FileText) and AUTHOR not in found.text
    assert found.text.count("가") == 40_001


def test_a_slides_footer_is_counted_once() -> None:
    """A footer box is read twice, as the slide's text and as its foot."""
    parts: Parts = {
        "ppt/slides/slide1.xml": f"<p:sld {P} {A}>" + box("가" * 200_000, "ftr") + "</p:sld>"
    }

    found = same(".pptx", package(parts))

    assert isinstance(found, FileText) and len(found.text) == len(found.foot) == 200_000
    assert read_file("a.pptx", package(parts)) == found


# --- a part is looked through a piece at a time ------------------------------------------


@pytest.mark.parametrize("declared", [b"<!DOCTYPE x>", b"<!-- <!ENTITY -->"])
def test_a_declaration_lying_across_two_pieces_is_seen(
    monkeypatch: pytest.MonkeyPatch, declared: bytes
) -> None:
    """Wherever it starts in a piece. A parser takes each of these parts --
    one declares nothing, the other only names the word in a comment -- so it
    is the look that refuses them, as it did when it had the part whole."""
    monkeypatch.setattr(material_formats, "_PIECE", 16)
    body = word("본문입니다.").encode()
    ElementTree.fromstring(declared + body)  # noqa: S314 -- written here

    for pad in range(40):
        data = package({"word/document.xml": b" " * pad + declared + body})
        assert outcome(tree_docx, data) == "damaged"
        assert reason("a.docx", data) == "damaged", pad
    assert (
        read_file("a.docx", package({"word/document.xml": b" " * 39 + body})).text == "본문입니다."
    )


def test_a_nul_in_a_later_piece_is_seen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(material_formats, "_PIECE", 16)
    opened = material_formats._Package(package({"a.xml": b"<a>" + b"x" * 100 + b"\x00</a>"}))

    with pytest.raises(RefusedError) as refused:
        opened.open("a.xml")

    assert refused.value.reason == "damaged"
