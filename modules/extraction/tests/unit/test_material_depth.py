"""A zipped material's parts may nest only so deep (#1252).

A streamed part is not held as a tree, but an element that is still open is
held, with every element it sits inside: a part of nothing but elements that
open is held whole however it is read. One nested 2.4 million deep, inside
the limit on what is parsed, took 800 MB. ``material_formats.MAX_DEPTH`` is
the most elements an element may sit inside; a part with one deeper is
refused as ``too_deep`` when that element opens.

Up to the limit a file reads as it did: the readers as they were
(``test_material_stream``) are asked the same files here.

Every value here is invented.
"""

from __future__ import annotations

import tracemalloc
import zipfile
from pathlib import Path

import pytest

from autune_extraction import material_formats
from autune_extraction.material_formats import MAX_DEPTH, MAX_PARSED, FileText
from autune_extraction.material_reading import read_file

from .test_material_formats import POI, A, P, S, W, package, word, workbook
from .test_material_stream import READERS, Parts, outcome, reason, same

WORKBOOK = workbook(("가", "visible"))
SAID = "바닥에 적힌 글"


def nested(top: list[str], around: list[str], bottom: list[str], depth: int) -> str:
    """Elements one inside another and ``SAID`` in the last, which sits inside
    exactly ``depth`` others: ``top``, then ``around`` over and over, then
    ``bottom``."""
    filling = depth + 1 - len(top) - len(bottom)
    tags = [*top, *(around[n % len(around)] for n in range(filling)), *bottom]
    assert filling >= 0 and len(tags) == depth + 1
    closed = "".join(f"</{tag.split()[0]}>" for tag in reversed(tags))
    return "".join(f"<{tag}>" for tag in tags) + SAID + closed


def tables(depth: int) -> Parts:
    """A document of tables inside tables."""
    body = nested(
        [f"w:document {W}", "w:body"], ["w:tbl", "w:tr", "w:tc"], ["w:p", "w:r", "w:t"], depth
    )
    return {"word/document.xml": body}


def groups(depth: int) -> Parts:
    """A slide of groups inside groups."""
    body = nested(
        [f"p:sld {P} {A}", "p:cSld", "p:spTree"],
        ["p:grpSp"],
        ["p:sp", "p:txBody", "a:p", "a:r", "a:t"],
        depth,
    )
    return {"ppt/slides/slide1.xml": body}


def cell(depth: int) -> Parts:
    """A sheet whose one cell holds its text under elements no reader names."""
    body = nested(
        [f"worksheet {S}", "sheetData", "row", 'c t="inlineStr"', "is"], ["x"], ["t"], depth
    )
    return {"xl/workbook.xml": WORKBOOK, "xl/worksheets/sheet1.xml": body}


DEEP = {"a.docx": tables, "a.pptx": groups, "a.xlsx": cell}


@pytest.mark.parametrize("name", DEEP)
def test_a_file_nested_as_deep_as_is_taken_reads_as_it_did(name: str) -> None:
    data = package(DEEP[name](MAX_DEPTH))

    found = same(Path(name).suffix, data)

    assert isinstance(found, FileText) and SAID in found.text
    assert read_file(name, data).text == found.text


@pytest.mark.parametrize("name", DEEP)
def test_a_file_nested_one_deeper_is_refused(name: str) -> None:
    data = package(DEEP[name](MAX_DEPTH + 1))
    tree, stream = READERS[Path(name).suffix]

    was = outcome(tree, data)
    assert isinstance(was, FileText) and SAID in was.text, "a tree reader took it"
    assert outcome(stream, data) == "too_deep"
    assert reason(name, data) == "too_deep"


def pit(depth: int, *, closed: bool = True) -> str:
    """Elements one inside another, the last inside ``depth`` others."""
    return "<x>" * (depth + 1) + "</x>" * (depth + 1) * closed


TOO_DEEP = pit(MAX_DEPTH + 1)
PLACES: dict[str, tuple[str, Parts]] = {
    "a document's body": ("a.docx", {"word/document.xml": TOO_DEEP}),
    "a document's header": (
        "a.docx",
        {"word/document.xml": word("글"), "word/header1.xml": TOO_DEEP},
    ),
    "a document's comments": (
        "a.docx",
        {"word/document.xml": word("글"), "word/comments.xml": TOO_DEEP},
    ),
    "a file's labels": ("a.docx", {"word/document.xml": word("글"), "docProps/core.xml": TOO_DEEP}),
    "a file's own properties": (
        "a.docx",
        {"word/document.xml": word("글"), "docProps/custom.xml": TOO_DEEP},
    ),
    "a workbook's list of sheets": ("a.xlsx", {"xl/workbook.xml": TOO_DEEP}),
    "a workbook's shared strings": (
        "a.xlsx",
        {
            "xl/workbook.xml": WORKBOOK,
            "xl/sharedStrings.xml": TOO_DEEP,
            "xl/worksheets/sheet1.xml": f"<worksheet {S}><sheetData/></worksheet>",
        },
    ),
    "a sheet": ("a.xlsx", {"xl/workbook.xml": WORKBOOK, "xl/worksheets/sheet1.xml": TOO_DEEP}),
    "a slide": ("a.pptx", {"ppt/slides/slide1.xml": TOO_DEEP}),
}


@pytest.mark.parametrize("place", PLACES)
def test_a_part_nested_too_deep_is_refused_wherever_it_is(place: str) -> None:
    """The limit is on every part a reader parses, whatever its elements are
    called: the count is of elements, not of tables or groups."""
    name, parts = PLACES[place]

    assert reason(name, package(parts)) == "too_deep"


def test_how_deep_an_element_sits_is_what_is_counted() -> None:
    """Not how many elements a part has, nor how many open and close on the
    way: a part of many shallow elements beside one as deep as is taken."""
    beside = "<x><x><x/></x></x>" * (4 * MAX_DEPTH)
    part = f"<x>{beside}{pit(MAX_DEPTH - 1)}{beside}</x>"
    opened = material_formats._Package(package({"a.xml": part}))

    deepest = max(depth for *_, depth in opened.events("a.xml", opening=True))

    assert deepest == MAX_DEPTH


def test_the_read_ends_where_the_element_opens() -> None:
    """Elements one inside another, many more in the last of them, and the
    part breaks off several pieces on. With the many one too deep, a reader
    that went on to the break would call the part damaged, as it calls the
    one whose many are as deep as is taken."""
    filler = "<y/>" * 100_000

    def broken(depth: int) -> bytes:
        return package(
            {
                "xl/workbook.xml": WORKBOOK,
                "xl/worksheets/sheet1.xml": pit(depth, closed=False) + filler,
            }
        )

    assert reason("a.xlsx", broken(MAX_DEPTH)) == "too_deep"
    assert reason("a.xlsx", broken(MAX_DEPTH - 1)) == "damaged"


def held(depth: int) -> tuple[int, int]:
    """A sheet nested ``depth`` deep, read: its length unpacked, and the most
    memory taken on the way to refusing it."""
    part = pit(depth)
    data = package({"xl/workbook.xml": WORKBOOK, "xl/worksheets/sheet1.xml": part})
    tracemalloc.start()
    try:
        assert outcome(material_formats._xlsx, data) == "too_deep"
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return len(part), peak


def test_a_part_nested_without_end_is_not_held() -> None:
    """What the refusal costs is what the parser made of the one piece the
    element opened in -- some eight megabytes when the piece is nothing but
    elements that open -- however deep the part goes on. Read to its end, the
    longer part here would be held whole, every element of it open at once:
    a hundred and forty megabytes."""
    _, short = held(100_000)
    length, long = held(400_000)

    assert length < MAX_PARSED, "inside the limit on what is parsed"
    assert long < 1.5 * short, "a part four times as deep takes no more"
    assert long < 16 * 1024 * 1024


def deepest(file: Path) -> int:
    """How deep the deepest element of any of a file's parts sits."""
    found = 0
    with zipfile.ZipFile(file) as archive:
        names = [name for name in archive.namelist() if name.endswith((".xml", ".rels"))]
    opened = material_formats._Package(file.read_bytes())
    for name in names:
        found = max(found, *(depth for *_, depth in opened.events(name, opening=True)))
    return found


def test_the_limit_is_far_from_what_office_writes() -> None:
    """The files a real program wrote nest a tenth as deep at most, in every
    part of them and not only the parts that are read."""
    depths = {
        name: deepest(POI / name) for name in ("SampleShow.pptx", "SampleSS.xlsx", "comments.xlsx")
    }

    assert max(depths.values()) == 10, depths
    assert 10 * max(depths.values()) <= MAX_DEPTH
