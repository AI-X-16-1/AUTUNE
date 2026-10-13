"""A zipped material that names a part the readers do not find is refused (#1252).

The readers find a sheet, a slide and a header by its usual name
(``xl/worksheets/sheet1.xml``). A program that opens the file finds it by the
relationships of the part above it, which may give it any name. One under
another name was left unread and nothing said so: its text was in nothing the
file was said to hold, and a marking on it stopped nothing. Such a file is
now refused whole as ``damaged`` (``_Package.named_were_read``). It is not
read: only noticed.

What is asked here: the files that were taken and are refused; that a file
whose parts have their usual names reads as it did, a chart sheet and the
files Office wrote among them; and that a relationship part is read as
carefully as any other part.

Every value here is invented.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from autune_extraction import material_formats
from autune_extraction.material_reading import UnreadableFileError, read_file
from autune_extraction.materials import ConfidentialFileError

from .test_material_formats import (
    AUTHOR,
    FIXTURES,
    PHONE,
    POI,
    P,
    S,
    W,
    alarms,
    package,
    put_up,
    read,
    rows,
    session,  # noqa: F401 -- the fixture
    slide,
    stored,
    word,
    workbook,
)
from .test_material_stream import Parts, reason

KINDS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
STRICT = "http://purl.oclc.org/ooxml/officeDocument/relationships/"
MARKING = "대외비"

BOOK_NAMES = "xl/_rels/workbook.xml.rels"
SHOW_NAMES = "ppt/_rels/presentation.xml.rels"
WORD_NAMES = "word/_rels/document.xml.rels"


def names(*named: tuple[str, str], kinds: str = KINDS) -> str:
    """A relationship part: a kind and the part it names, for each."""
    each = "".join(
        f'<Relationship Id="rId{n}" Type="{kinds}{kind}" Target="{target}"/>'
        for n, (kind, target) in enumerate(named, 1)
    )
    return (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{each}</Relationships>"
    )


def paragraphs(top: str, *said: str) -> str:
    """A header, a footer or a part of notes: ``top`` is its first element."""
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in said)
    return f"<w:{top} {W}>{body}</w:{top}>"


def print_header(said: str) -> str:
    return rows("급여표").replace(
        "</worksheet>", f"<headerFooter><oddHeader>{said}</oddHeader></headerFooter></worksheet>"
    )


def footer_box(said: str) -> str:
    box = (
        '<p:sp><p:nvSpPr><p:nvPr><p:ph type="ftr"/></p:nvPr></p:nvSpPr>'
        f"<p:txBody><a:p><a:r><a:t>{said}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree>"
    )
    return slide("둘째 장").replace("</p:spTree>", box)


def book(*sheets: tuple[str, str]) -> Parts:
    """A workbook of the sheets given, a part's name and its body for each,
    named in the workbook's relationships as Excel names them."""
    parts: Parts = {
        "xl/workbook.xml": workbook(*((f"시트{n}", "visible") for n in range(len(sheets)))),
        BOOK_NAMES: names(*(("worksheet", f"worksheets/{name}") for name, _ in sheets)),
    }
    return parts | {f"xl/worksheets/{name}": body for name, body in sheets}


def show(*slides: tuple[str, str], masters: tuple[tuple[str, str], ...] = ()) -> Parts:
    named = [("slide", f"slides/{name}") for name, _ in slides]
    named += [("slideMaster", f"slideMasters/{name}") for name, _ in masters]
    parts: Parts = {"ppt/presentation.xml": f"<p:presentation {P}/>", SHOW_NAMES: names(*named)}
    parts |= {f"ppt/slides/{name}": body for name, body in slides}
    return parts | {f"ppt/slideMasters/{name}": body for name, body in masters}


def document(*beside: tuple[str, str, str]) -> Parts:
    """A document and the parts beside it: a kind, a name and a body for each."""
    parts: Parts = {
        "word/document.xml": word("평범한 본문입니다."),
        WORD_NAMES: names(*((kind, name) for kind, name, _ in beside)),
    }
    return parts | {f"word/{name}": body for _, name, body in beside}


def unnamed(parts: Parts) -> Parts:
    """The same file without its relationship parts."""
    return {name: body for name, body in parts.items() if not name.endswith(".rels")}


# --- what was taken with a part unread is refused -----------------------------------


@pytest.mark.parametrize("name", ["data.xml", "sheet.xml", "Sheet1.xml", "sheets/sheet1.xml"])
def test_a_workbook_whose_sheet_has_another_name_is_refused(name: str) -> None:
    assert reason("a.xlsx", package(book((name, rows("출시 일정"))))) == "damaged"
    assert reason("a.xlsx", package(book(("sheet1.xml", rows("일정")), (name, rows("급여"))))) == (
        "damaged"
    )


@pytest.mark.parametrize("name", ["intro.xml", "slide.xml", "Slide2.xml"])
def test_a_deck_whose_slide_has_another_name_is_refused(name: str) -> None:
    parts = show(("slide1.xml", slide("첫 장")), (name, slide("둘째 장")))

    assert reason("a.pptx", package(parts)) == "damaged"


@pytest.mark.parametrize(
    ("kind", "folder"),
    [
        ("slideMaster", "slideMasters"),
        ("notesMaster", "notesMasters"),
        ("handoutMaster", "handoutMasters"),
    ],
)
def test_a_deck_whose_master_has_another_name_is_refused(kind: str, folder: str) -> None:
    parts = show(("slide1.xml", slide("첫 장")))
    parts[SHOW_NAMES] = names(("slide", "slides/slide1.xml"), (kind, f"{folder}/master.xml"))
    parts[f"ppt/{folder}/master.xml"] = slide("바탕에 적힌 글")

    assert reason("a.pptx", package(parts)) == "damaged"


@pytest.mark.parametrize(
    ("kind", "name", "top"),
    [
        ("header", "hdr.xml", "hdr"),
        ("footer", "foot.xml", "ftr"),
        ("footnotes", "notes.xml", "footnotes"),
        ("endnotes", "endnotes2.xml", "endnotes"),
        ("comments", "comments1.xml", "comments"),
    ],
)
def test_a_document_whose_part_has_another_name_is_refused(kind: str, name: str, top: str) -> None:
    parts = document((kind, name, paragraphs(top, "곁에 적힌 글")))

    assert reason("a.docx", package(parts)) == "damaged"


MARKED: dict[str, tuple[str, Parts, Parts]] = {
    "the-only-sheet": (
        "a.xlsx",
        book(("sheet1.xml", rows(MARKING, "급여표"))),
        book(("data.xml", rows(MARKING, "급여표"))),
    ),
    "a-later-sheets-print-header": (
        "a.xlsx",
        book(("sheet1.xml", rows("일정")), ("sheet2.xml", print_header(MARKING))),
        book(("sheet1.xml", rows("일정")), ("data.xml", print_header(MARKING))),
    ),
    "a-later-slides-footer-box": (
        "a.pptx",
        show(("slide1.xml", slide("첫 장")), ("slide2.xml", footer_box(MARKING))),
        show(("slide1.xml", slide("첫 장")), ("intro.xml", footer_box(MARKING))),
    ),
    "the-slide-master": (
        "a.pptx",
        show(("slide1.xml", slide("첫 장")), masters=(("slideMaster1.xml", slide(MARKING)),)),
        show(("slide1.xml", slide("첫 장")), masters=(("master.xml", slide(MARKING)),)),
    ),
    "a-header": (
        "a.docx",
        document(("header", "header1.xml", paragraphs("hdr", MARKING))),
        document(("header", "hdr.xml", paragraphs("hdr", MARKING))),
    ),
}


@pytest.mark.parametrize("case", MARKED)
def test_a_marking_on_a_part_with_another_name_no_longer_gets_the_file_in(
    session: Session,  # noqa: F811 -- the fixture
    case: str,
) -> None:
    """Under its usual name the part is read and its marking stops the file,
    with an alarm for the team's approvers. Under another name the marking is
    not read, so no alarm can be written: the file is refused as one that
    cannot be read, and nothing of it is kept."""
    name, usual, other = MARKED[case]

    with pytest.raises(ConfidentialFileError):
        put_up(session, name, package(usual))
    told = alarms(session)
    assert len(told) == 1 and stored(session) == ""

    with pytest.raises(UnreadableFileError) as refused:
        put_up(session, name, package(other))
    assert refused.value.details["reason"] == "damaged"
    assert alarms(session) == told and stored(session) == ""


def test_a_part_that_is_named_and_is_not_in_the_file_is_refused() -> None:
    parts = book(("sheet1.xml", rows("일정")))
    parts[BOOK_NAMES] = names(("worksheet", "worksheets/sheet1.xml"), ("worksheet", "sheet2.xml"))

    assert reason("a.xlsx", package(parts)) == "damaged"


def test_the_strict_flavours_kinds_are_the_same_kinds() -> None:
    parts = book(("data.xml", rows("일정")))
    parts[BOOK_NAMES] = names(("worksheet", "worksheets/data.xml"), kinds=STRICT)

    assert reason("a.xlsx", package(parts)) == "damaged"


def test_a_relationship_part_is_found_whatever_the_case_of_its_name() -> None:
    parts = book(("data.xml", rows("일정")))
    parts["xl/_RELS/Workbook.xml.rels"] = parts.pop(BOOK_NAMES)

    assert reason("a.xlsx", package(parts)) == "damaged"


# --- a file whose parts have their usual names reads as it did -----------------------


@pytest.mark.parametrize("name", ["SampleShow.pptx", "SampleSS.xlsx", "comments.xlsx"])
def test_a_file_office_wrote_names_its_parts_and_is_read(name: str) -> None:
    data = (POI / name).read_bytes()
    part = SHOW_NAMES if name.endswith(".pptx") else BOOK_NAMES

    assert part in material_formats._Package(data).names
    assert read_file(name, data).text


def test_a_workbook_with_a_chart_sheet_is_read() -> None:
    """A chart sheet is a sheet in the workbook's list and a relationship of
    its own kind, and it is not a part these readers read."""
    parts: Parts = {
        "xl/workbook.xml": workbook(("표", "visible"), ("그림표", "visible")),
        BOOK_NAMES: names(
            ("worksheet", "worksheets/sheet1.xml"),
            ("chartsheet", "chartsheets/sheet1.xml"),
            ("styles", "styles.xml"),
            ("theme", "theme/theme1.xml"),
        ),
        "xl/worksheets/sheet1.xml": rows("출시 일정"),
        "xl/chartsheets/sheet1.xml": f"<chartsheet {S}/>",
        "xl/charts/chart1.xml": "<c:chartSpace xmlns:c='urn:c'/>",
    }

    found = read("a.xlsx", parts)

    assert found.text == "표\n그림표\n출시 일정"
    assert found.not_read == ("charts",)


def test_a_document_laid_out_as_word_lays_one_out_is_read() -> None:
    """No file Word wrote is among the samples: this one is built with the
    parts and the relationships Word writes, the ones not read here too."""
    parts = document(
        ("styles", "styles.xml", f"<w:styles {W}/>"),
        ("header", "header1.xml", paragraphs("hdr", "머리글")),
        ("header", "header2.xml", paragraphs("hdr", "첫 쪽 머리글")),
        ("footer", "footer1.xml", paragraphs("ftr", "바닥글")),
        ("footnotes", "footnotes.xml", paragraphs("footnotes", "각주")),
        ("endnotes", "endnotes.xml", paragraphs("endnotes", "미주")),
        ("comments", "comments.xml", paragraphs("comments", "메모")),
        ("commentsExtended", "commentsExtended.xml", f"<w:commentsEx {W}/>"),
        ("image", "media/image1.png", "그림"),
    )
    parts[WORD_NAMES] = str(parts[WORD_NAMES]).replace(
        "</Relationships>",
        f'<Relationship Id="rId99" Type="{KINDS}hyperlink" Target="https://example.com/header.xml"'
        ' TargetMode="External"/></Relationships>',
    )

    found = read("a.docx", parts)

    assert found.text.split("\n") == [
        "평범한 본문입니다.",
        "머리글",
        "첫 쪽 머리글",
        "바닥글",
        "메모",
        "미주",
        "각주",
    ]
    assert found.not_read == ("pictures",)


def test_a_deck_laid_out_as_powerpoint_lays_one_out_is_read() -> None:
    parts = show(
        ("slide1.xml", slide("첫 장")),
        ("slide2.xml", slide("둘째 장")),
        masters=(("slideMaster1.xml", slide("바탕에 적힌 글")),),
    )
    parts[SHOW_NAMES] = names(
        ("slide", "slides/slide1.xml"),
        ("slide", "slides/slide2.xml"),
        ("slideMaster", "slideMasters/slideMaster1.xml"),
        ("notesMaster", "notesMasters/notesMaster1.xml"),
        ("handoutMaster", "handoutMasters/handoutMaster1.xml"),
        ("presProps", "presProps.xml"),
        ("theme", "theme/theme1.xml"),
    )
    parts["ppt/notesMasters/notesMaster1.xml"] = slide("메모 바탕")
    parts["ppt/handoutMasters/handoutMaster1.xml"] = slide("유인물 바탕")

    found = read("a.pptx", parts)

    assert found.text == "첫 장\n둘째 장"
    assert found.marks == "바탕에 적힌 글\n메모 바탕\n유인물 바탕"


@pytest.mark.parametrize(
    "target",
    ["worksheets/sheet1.xml", "/xl/worksheets/sheet1.xml", "../xl/./worksheets/sheet1.xml"],
    ids=["beside-the-workbook", "from-the-top", "the-long-way-round"],
)
def test_a_sheet_is_the_same_part_however_the_way_to_it_is_written(target: str) -> None:
    """A relationship names a part from where the workbook is or from the top
    of the file."""
    parts = book(("sheet1.xml", rows("출시 일정")))
    parts[BOOK_NAMES] = names(("worksheet", target))

    assert read("a.xlsx", parts).text == "시트0\n출시 일정"


def test_a_sheet_named_in_other_letters_than_the_file_holds_it_under_is_refused() -> None:
    """A part that was read is one named letter for letter. Two parts whose
    names differ only in case would otherwise pass for one: the one that was
    read, and the one that was not."""
    parts = book(("sheet1.xml", rows("출시 일정")))
    parts[BOOK_NAMES] = names(("worksheet", "worksheets/Sheet1.xml"))

    assert reason("a.xlsx", package(parts)) == "damaged"


@pytest.mark.parametrize(
    ("name", "parts"),
    [
        ("a.xlsx", unnamed(book(("sheet1.xml", rows("일정"))))),
        ("a.pptx", unnamed(show(("slide1.xml", slide("일정"))))),
        ("a.docx", unnamed(document(("header", "header1.xml", paragraphs("hdr", "일정 위"))))),
    ],
    ids=["workbook", "deck", "document"],
)
def test_a_file_without_the_relationship_part_is_read_by_name_as_before(
    name: str, parts: Parts
) -> None:
    """Nothing in such a file names a part under another name. A document
    with no header needs no relationship part, so this cannot be a refusal."""
    assert "일정" in read(name, parts).text


# --- a relationship part is read as carefully as any other ---------------------------


def test_a_relationship_part_that_declares_an_entity_is_refused() -> None:
    parts = book(("sheet1.xml", rows("일정")))
    parts[BOOK_NAMES] = '<!DOCTYPE r [<!ENTITY e "worksheet">]>' + str(parts[BOOK_NAMES])

    assert reason("a.xlsx", package(parts)) == "damaged"


def test_a_relationship_part_nested_too_deep_is_refused() -> None:
    parts = book(("sheet1.xml", rows("일정")))
    deep = material_formats.MAX_DEPTH + 2
    parts[BOOK_NAMES] = "<a>" * deep + "</a>" * deep

    assert reason("a.xlsx", package(parts)) == "too_deep"


def test_a_relationship_part_counts_toward_what_may_be_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parts = book(("sheet1.xml", rows("일정")))
    read_before = sum(len(str(body).encode()) for body in unnamed(parts).values())
    monkeypatch.setattr(material_formats, "MAX_PARSED", read_before)
    assert read("a.xlsx", unnamed(parts)).text

    assert reason("a.xlsx", package(parts)) == "too_long"


def test_a_relationship_part_that_breaks_off_is_a_damaged_file() -> None:
    parts = book(("sheet1.xml", rows("일정")))
    parts[BOOK_NAMES] = str(parts[BOOK_NAMES])[:-20]

    assert reason("a.xlsx", package(parts)) == "damaged"


# --- a sheet's notes, under the two names writers give them ---------------------------

OPENPYXL = FIXTURES / "openpyxl"


def notes(*said: str) -> str:
    """A sheet's notes, each written by ``AUTHOR``."""
    each = "".join(
        f'<comment ref="A{n}" authorId="0"><text><t>{text}</t></text></comment>'
        for n, text in enumerate(said, 1)
    )
    return (
        f"<comments {S}><authors><author>{AUTHOR}</author></authors>"
        f"<commentList>{each}</commentList></comments>"
    )


def noted(name: str, *said: str) -> Parts:
    """A workbook of one sheet that names its notes, kept under ``name``."""
    beside = {
        "xl/worksheets/_rels/sheet1.xml.rels": names(("comments", f"/{name}")),
        name: notes(*said),
    }
    return book(("sheet1.xml", rows("일정"))) | beside


def test_the_notes_of_a_workbook_openpyxl_wrote_are_read() -> None:
    """Excel names a sheet's notes ``xl/comments1.xml`` and openpyxl names
    them ``xl/comments/comment1.xml``. Under the second name they were left
    unread without a word."""
    data = (OPENPYXL / "notes.xlsx").read_bytes()
    held = material_formats._Package(data).names
    assert "xl/comments/comment1.xml" in held and "xl/comments/comment2.xml" in held

    found = read_file("notes.xlsx", data)

    assert found.text.split("\n") == [
        "Plan",
        "Budget",
        "출시 일정",
        "예산표",
        "일정 확인 필요",
        "둘째 시트의 메모",
    ]


@pytest.mark.parametrize(
    "name", ["xl/comments1.xml", "xl/comments/comment1.xml", "xl/comments/comment12.xml"]
)
def test_a_note_is_read_the_same_under_either_name(name: str) -> None:
    """The line that names a note's writer is dropped under either name, and
    the writer is in nothing that is kept."""
    found = read("a.xlsx", noted(name, f"{AUTHOR}:\n검토가 필요합니다", "둘째 메모"))

    assert found.text == "시트0\n일정\n검토가 필요합니다\n둘째 메모"
    assert AUTHOR not in found.text + found.head + found.foot + found.marks


def test_a_note_under_openpyxls_name_is_masked_and_kept_like_any_other(
    session: Session,  # noqa: F811 -- the fixture
) -> None:
    put_up(session, "a.xlsx", package(noted("xl/comments/comment1.xml", f"연락처 {PHONE} 로")))

    assert "연락처" in stored(session) and PHONE not in stored(session)


# --- what this does not reach --------------------------------------------------------


def test_what_a_sheet_names_in_turn_is_not_looked_at() -> None:
    """A known limit, written down so that it is not taken for covered. Only
    what the workbook itself names is looked at. A sheet's notes are named by
    the sheet: under a name that is neither Excel's nor openpyxl's the
    workbook is taken, and the note is in nothing it is said to hold."""
    found = read("a.xlsx", noted("xl/notes/note1.xml", "메모 문구"))

    assert found.text == "시트0\n일정" and found.not_read == ()
