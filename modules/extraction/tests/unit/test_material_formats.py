"""Reading each format of an uploaded material (#817): what is read, what is
read only to look for a marking, what is never read, what is refused -- and
which part of a file is its head and its foot, the places besides its name
and its own label where a marking is looked for.

Three kinds of file are used. Files a real program wrote -- Microsoft Office
(Apache POI's test data, ``tests/fixtures/materials/poi``, Apache-2.0, with
its notice) and Chrome (``tests/fixtures/materials/pdf``) -- show the readers
work on the real thing. Three old workbooks written by another library
(``tests/fixtures/materials/xls``). And files built here by hand, part by
part, which put text in each of the places it hides.

Two things here no real program wrote. The Excel notes that open with their
writer's name are laid out as Excel lays a new note out, by hand: no file
saved by Excel was at hand, in either format. The old-format one is BIFF8
records written here into a workbook stream and read without its container.
And the PDF pages whose picture is inside a form object or inline are
written object by object.

Every value here and in the files made for these tests is invented.
"""

from __future__ import annotations

import io
import struct
import traceback
import zipfile
from collections.abc import Iterator
from pathlib import Path

import olefile
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import Base, Team, TeamMember, User
from autune_core.errors import AutuneError
from autune_extraction import material_formats, materials
from autune_extraction.material_formats import FileText
from autune_extraction.material_marking import file_marking
from autune_extraction.material_reading import SUFFIXES, UnreadableFileError, read_file
from autune_extraction.materials import ConfidentialFileError

TEAM = "team_1"

FIXTURES = Path(__file__).parent.parent / "fixtures" / "materials"
POI = FIXTURES / "poi"
PDF = FIXTURES / "pdf"
XLS = FIXTURES / "xls"

PHONE = "010-2345-6789"
EMAIL = "minsu.kim@example.com"
RRN = "900101-1234567"
CARD = "4111 1111 1111 1111"
ACCOUNT = "123456-01-234567"
SECRETS = (PHONE, EMAIL, RRN, CARD, ACCOUNT)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (User, Team, TeamMember)}
    owned = {
        "ext_materials",
        "ext_material_chunks",
        "ext_material_alarms",
        "ext_material_settings",
    }
    tables = [t for name, t in Base.metadata.tables.items() if name in shared | owned]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        yield s


def put_up(session: Session, name: str, data: bytes) -> materials.StoredUpload:
    return materials.store_upload(session, TEAM, title="자료", file_name=name, data=data)


def stored(session: Session) -> str:
    """Every piece of text the tables hold of any file, each file's pieces
    joined back as they were cut."""
    return "\n".join(
        "".join(materials.material_text(session, row.id))
        for row in materials.team_materials(session, TEAM)
    )


def alarms(session: Session) -> list[str]:
    return [alarm.marking for alarm in materials.pending_alarms(session, TEAM)]


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
CORE = (
    'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/"'
)
AUTHOR = "홍길동작성자"


def package(parts: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in parts.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def core(**fields: str) -> str:
    """Document properties; the author is always there, to show it is not read."""
    extra = "".join(f"<{tag}>{value}</{tag}>" for tag, value in fields.items())
    return (
        f"<cp:coreProperties {CORE}><dc:creator>{AUTHOR}</dc:creator>"
        f"<cp:lastModifiedBy>{AUTHOR}</cp:lastModifiedBy>{extra}</cp:coreProperties>"
    )


def word(*paragraphs: str) -> str:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    return f"<w:document {W}><w:body>{body}</w:body></w:document>"


def slide(*paragraphs: str) -> str:
    body = "".join(f"<a:p><a:r><a:t>{text}</a:t></a:r></a:p>" for text in paragraphs)
    return (
        f"<p:sld {P} {A}><p:cSld><p:spTree><p:sp><p:txBody>{body}"
        "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )


def sheet(*rows: str) -> str:
    return f"<worksheet {S}><sheetData>{''.join(rows)}</sheetData></worksheet>"


def workbook(*names: tuple[str, str]) -> str:
    sheets = "".join(
        f'<sheet name="{name}" sheetId="{n}" state="{state}"/>'
        for n, (name, state) in enumerate(names, 1)
    )
    return f"<workbook {S}><sheets>{sheets}</sheets></workbook>"


def read(name: str, parts: dict[str, str | bytes]) -> FileText:
    return read_file(name, package(parts))


# --- files a real program wrote ---------------------------------------------------


def test_a_deck_reads_the_same_from_old_and_new_powerpoint() -> None:
    old = read_file("SampleShow.ppt", (POI / "SampleShow.ppt").read_bytes())
    new = read_file("SampleShow.pptx", (POI / "SampleShow.pptx").read_bytes())

    for sentence in (
        "Title of the first slide",
        "This is the second slide",
        "It has bullet points on it",
        "Especially in a different font like Arial Black at 16 point!",
    ):
        assert sentence in old.text and sentence in new.text
    # A master's placeholder words are looked at for a marking and not kept.
    for found in (old, new):
        assert "Click to edit Master title style" in found.marks
        assert "Click to edit Master title style" in found.edges()
        assert "Click to edit Master" not in found.text


def test_speaker_notes_are_read_from_an_old_deck() -> None:
    found = read_file("basic.ppt", (POI / "basic_test_ppt_file.ppt").read_bytes())
    assert "This is a test title" in found.text
    assert "These are the notes for page 1" in found.text
    assert "These are the notes on page two" in found.text


def test_a_text_box_is_read_from_an_old_deck() -> None:
    found = read_file("boxed.ppt", (POI / "with_textbox.ppt").read_bytes())
    assert found.text.strip() and found.not_read == ()


def test_a_workbook_reads_the_same_from_old_and_new_excel() -> None:
    old = read_file("SampleSS.xls", (POI / "SampleSS.xls").read_bytes())
    new = read_file("SampleSS.xlsx", (POI / "SampleSS.xlsx").read_bytes())

    assert old.text == new.text
    assert old.text.startswith("First Sheet\nSheet Number 2\nSheet3\nTest spreadsheet")
    assert "1\t10\t2\t13" in old.text, "a number cell is its digits, a formula its value"


def test_cell_comments_are_read() -> None:
    found = read_file("comments.xlsx", (POI / "comments.xlsx").read_bytes())
    assert "comment top row1 (index0)" in found.text and "row3\trow3" in found.text


@pytest.mark.parametrize("name", ["password.xls", "Password_Protected-hello.ppt"])
def test_a_password_protected_office_file_is_refused(name: str) -> None:
    with pytest.raises(UnreadableFileError) as refused:
        read_file(name, (POI / name).read_bytes())
    assert refused.value.reason == "protected"


def test_a_korean_pdf_is_read_masked_and_stored(session: Session) -> None:
    found = read_file("korean.pdf", (PDF / "korean.pdf").read_bytes())
    assert "결제 모듈 이관은 9월 둘째 주까지 마칩니다" in found.text
    assert "둘째 쪽" in found.text and PHONE in found.text, "both pages, as written"

    put_up(session, "korean.pdf", (PDF / "korean.pdf").read_bytes())

    assert "결제 모듈 이관" in stored(session) and "4,500만 원" in stored(session)
    assert not any(secret in stored(session) for secret in SECRETS)


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("scan.pdf", "no_text_layer"),
        ("mixed.pdf", "page_without_text"),
        ("thin.pdf", "page_without_text"),
        ("thin19.pdf", "no_text_layer"),
        ("thin_spaced.pdf", "no_text_layer"),
    ],
    ids=[
        "every-page-a-picture",
        "one-page-a-picture",
        "a-picture-and-a-page-number",
        "a-picture-and-19-characters",
        "a-picture-and-12-characters-spaced-out",
    ],
)
def test_a_pdf_with_a_page_that_is_only_a_picture_is_refused_whole(
    session: Session, name: str, reason: str
) -> None:
    # mixed.pdf has a written first page; its second is a picture of a marking
    # and a phone number, which no reader here can check or mask. thin.pdf's
    # second page has the same picture and "- 2 -" under it.
    with pytest.raises(AutuneError) as refused:
        put_up(session, name, (PDF / name).read_bytes())
    assert str(refused.value).endswith(f": {reason}") and refused.value.status_code == 422
    assert stored(session) == "" and materials.team_materials(session, TEAM) == []
    assert alarms(session) == [], "unreadable is not confidential"


def test_a_pdf_with_a_picture_beside_its_text_is_taken_and_says_so(session: Session) -> None:
    kept = put_up(session, "pictured.pdf", (PDF / "pictured.pdf").read_bytes())
    assert kept.not_read == ("pictures",)
    # The picture's words -- a marking and a phone number -- were not read: not
    # stored, and not seen. This is the gap no OCR leaves.
    assert "런북" in stored(session) and "그림 안의" not in stored(session)


def test_a_page_with_a_picture_and_twenty_characters_is_a_page_with_text(session: Session) -> None:
    kept = put_up(session, "thin20.pdf", (PDF / "thin20.pdf").read_bytes())
    assert kept.not_read == ("pictures",) and "가나다라마바사아자차" in stored(session)


def test_a_short_page_with_no_picture_is_nothing_unread(session: Session) -> None:
    kept = put_up(session, "short.pdf", (PDF / "short.pdf").read_bytes())
    assert kept.not_read == () and stored(session).endswith("끝")


def test_a_marking_at_the_foot_of_a_pdfs_pages_stops_it(session: Session) -> None:
    # "Confidential" with a page number under it, printed at the foot of both pages.
    data = (PDF / "marked_foot.pdf").read_bytes()
    assert "Confidential" not in read_file("a.pdf", data).head

    with pytest.raises(ConfidentialFileError):
        put_up(session, "marked_foot.pdf", data)

    assert stored(session) == ""
    assert alarms(session) == ["english_marking"]


def test_a_marking_at_the_top_of_a_pdfs_first_page_stops_it(session: Session) -> None:
    with pytest.raises(ConfidentialFileError):
        put_up(session, "marked.pdf", (PDF / "marked.pdf").read_bytes())
    assert stored(session) == "" and len(alarms(session)) == 1


def test_a_pdf_that_names_a_marking_only_in_its_body_is_taken(session: Session) -> None:
    # Line seven of the first page's nine, and the first of the second page's
    # three: below the head, above each page's last two lines.
    data = (PDF / "marked_below.pdf").read_bytes()
    found = read_file("a.pdf", data)
    assert not any(word in found.head + found.foot for word in ("대외비", "기밀"))

    put_up(session, "marked_below.pdf", data)

    for words in ("대외비 표시를 붙이는 방법", "기밀 등급"):
        assert words in stored(session), words
    assert alarms(session) == []


def test_a_locked_pdf_is_refused_and_one_locked_only_for_editing_is_read() -> None:
    from pypdf import PdfReader, PdfWriter
    from pypdf.annotations import FreeText

    def rewritten(user_password: str | None, note: str | None = None) -> bytes:
        writer = PdfWriter(clone_from=PdfReader(PDF / "korean.pdf"))
        if note:
            writer.add_annotation(0, FreeText(text=note, rect=(50, 550, 300, 650)))
        if user_password is not None:
            writer.encrypt(user_password, "owner-only", algorithm="RC4-128")
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()

    with pytest.raises(UnreadableFileError) as refused:
        read_file("locked.pdf", rewritten("open-sesame"))
    assert refused.value.reason == "protected"
    assert "결제 모듈 이관" in read_file("edit-locked.pdf", rewritten("")).text
    # A note stuck on a page is text of the file too.
    assert f"메모 {PHONE}" in read_file("noted.pdf", rewritten(None, f"메모 {PHONE}")).text


def test_an_old_workbook_written_by_another_library_is_read_and_masked(session: Session) -> None:
    # contacts.xls: a phone number as text, another typed into a number cell,
    # and a hidden second sheet with an e-mail address.
    data = (XLS / "contacts.xls").read_bytes()

    found = read_file("연락망.xls", data)
    assert found.text.startswith("연락망\n숨긴 시트")
    assert "대표 번호\t1023456789" in found.text and EMAIL in found.text
    assert "1023456789.0" not in found.text, "a number cell is its digits"

    put_up(session, "연락망.xls", data)
    assert "운영 담당" in stored(session) and "숨긴 시트" in stored(session)
    assert PHONE not in stored(session) and EMAIL not in stored(session)
    assert "1023456789" not in stored(session)


def test_an_old_workbook_is_stopped_by_the_top_of_its_first_sheet_only(session: Session) -> None:
    with pytest.raises(ConfidentialFileError):
        put_up(session, "a.xls", (XLS / "marked_first_sheet.xls").read_bytes())
    assert stored(session) == ""

    put_up(session, "b.xls", (XLS / "marked_second_sheet.xls").read_bytes())
    assert "대외비" in stored(session)


def test_an_old_decks_head_is_the_first_lines_of_its_text() -> None:
    found = read_file("SampleShow.ppt", (POI / "SampleShow.ppt").read_bytes())
    head = found.head.splitlines()
    assert head[0] == "Title of the first slide" and len(head) == 5


# --- the head: a marking counts in the first lines and not below ------------------

LINES = ["첫째 줄", "둘째 줄", "셋째 줄", "넷째 줄", "다섯째 줄", "여섯째 줄", "일곱째 줄"]


def marked_at(line: int, words: str = "대외비") -> list[str]:
    """Seven ordinary lines, with a marking put in as line ``line``."""
    return [*LINES[: line - 1], words, *LINES[line - 1 :]]


def test_a_marking_on_the_fifth_line_stops_a_text_file_and_on_the_sixth_does_not(
    session: Session,
) -> None:
    # Empty lines between them are not counted.
    with pytest.raises(ConfidentialFileError):
        put_up(session, "a.txt", "\n\n\n".join(marked_at(5)).encode())
    assert stored(session) == ""

    put_up(session, "b.txt", "\n".join(marked_at(6)).encode())
    assert "대외비" in stored(session), "below the head the word is body, and kept like the rest"
    assert len(alarms(session)) == 1, "the first file's, and no second"


def test_a_marking_is_looked_for_a_line_at_a_time() -> None:
    # The end of one line and the start of the next do not make a word.
    found = FileText(text="본문", head="올해의 대외\n비용 계획", foot="1 급\n비밀번호 안내")
    assert found.edges() == ["올해의 대외", "비용 계획", "1 급", "비밀번호 안내"]
    assert file_marking("plan.docx", found.edges()) is None


# --- where text hides: Word -------------------------------------------------------


def test_word_headers_footers_notes_comments_and_deleted_text_are_read() -> None:
    found = read(
        "a.docx",
        {
            "word/document.xml": f"<w:document {W}><w:body><w:p><w:r><w:t>본문</w:t></w:r>"
            "<w:del><w:r><w:delText>지운 문장</w:delText></w:r></w:del></w:p>"
            "</w:body></w:document>",
            "word/header1.xml": f"<w:hdr {W}><w:p><w:r><w:t>머리말</w:t></w:r></w:p></w:hdr>",
            "word/footer2.xml": f"<w:ftr {W}><w:p><w:r><w:t>꼬리말</w:t></w:r></w:p></w:ftr>",
            "word/footnotes.xml": f"<w:footnotes {W}><w:p><w:r><w:t>각주</w:t></w:r>"
            "</w:p></w:footnotes>",
            "word/comments.xml": f'<w:comments {W}><w:comment w:author="{AUTHOR}"><w:p><w:r>'
            "<w:t>검토 의견</w:t></w:r></w:p></w:comment></w:comments>",
            "docProps/core.xml": core(**{"dc:title": "문서 제목"}),
            "word/media/image1.png": b"png",
        },
    )

    for words in ("본문", "지운 문장", "머리말", "꼬리말", "각주", "검토 의견"):
        assert words in found.text, words
    assert found.marks == "문서 제목" and found.not_read == ("pictures",)
    assert found.head == "본문지운 문장\n머리말", "the top of the body and the page header"
    assert found.foot == "꼬리말"
    assert found.edges() == ["본문지운 문장", "머리말", "꼬리말", "문서 제목"]
    assert AUTHOR not in found.text + found.marks


@pytest.mark.parametrize(
    "parts",
    [
        {"word/header1.xml": f"<w:hdr {W}><w:p><w:r><w:t>대외비</w:t></w:r></w:p></w:hdr>"},
        {"word/footer1.xml": f"<w:ftr {W}><w:p><w:r><w:t>대외비</w:t></w:r></w:p></w:ftr>"},
        {"word/document.xml": word(*marked_at(5))},
        {"docProps/core.xml": core(**{"cp:keywords": "confidential, plan"})},
        {
            "docProps/custom.xml": '<Properties xmlns:vt="x"><property name="MSIP_Label_1_Name">'
            "<vt:lpwstr>Strictly Confidential</vt:lpwstr></property></Properties>"
        },
    ],
    ids=["page-header", "page-footer", "fifth-line", "keywords", "sensitivity-label"],
)
def test_a_marking_at_the_edges_or_on_the_files_label_stops_a_word_file(
    session: Session, parts: dict[str, str]
) -> None:
    data = package({"word/document.xml": word("평범한 본문입니다."), **parts})
    with pytest.raises(ConfidentialFileError):
        put_up(session, "plan.docx", data)
    assert stored(session) == ""


def test_a_marking_in_the_body_notes_or_comments_of_a_word_file_does_not_stop_it(
    session: Session,
) -> None:
    data = package(
        {
            "word/document.xml": word(*marked_at(6, "기밀 유지 조항")),
            "word/footer1.xml": f"<w:ftr {W}><w:p><w:r><w:t>3쪽</w:t></w:r></w:p></w:ftr>",
            "word/footnotes.xml": f"<w:footnotes {W}><w:p><w:r>"
            "<w:t>Confidential 각주</w:t></w:r></w:p></w:footnotes>",
            "word/comments.xml": f"<w:comments {W}><w:comment><w:p><w:r>"
            "<w:t>사내 한정 의견</w:t></w:r></w:p></w:comment></w:comments>",
        }
    )
    put_up(session, "plan.docx", data)

    for words in ("기밀 유지 조항", "3쪽", "Confidential 각주", "사내 한정 의견"):
        assert words in stored(session), words
    assert alarms(session) == []


def test_properties_are_looked_at_and_not_kept(session: Session) -> None:
    data = package(
        {
            "word/document.xml": word("평범한 본문입니다."),
            "docProps/core.xml": core(**{"dc:title": "내부 제목 낱말", "dc:subject": PHONE}),
        }
    )
    put_up(session, "plan.docx", data)

    assert stored(session) == "평범한 본문입니다."
    assert "내부 제목" not in stored(session) and AUTHOR not in stored(session)


# --- where text hides: Excel ------------------------------------------------------


def workbook_parts() -> dict[str, str | bytes]:
    return {
        "xl/workbook.xml": workbook(("연락망", "visible"), ("숨긴 시트", "hidden")),
        "xl/sharedStrings.xml": f"<sst {S}><si><t>운영 담당</t></si>"
        "<si><r><t>굵은 </t></r><r><t>글씨</t></r><rPh><t>읽는법</t></rPh></si></sst>",
        "xl/worksheets/sheet1.xml": sheet(
            '<row r="1"><c t="s"><v>0</v></c><c><v>1.023456789E9</v></c></row>',
            '<row r="2" hidden="1"><c t="s"><v>1</v></c>'
            '<c t="inlineStr"><is><t>숨긴 행</t></is></c>'
            '<c t="str"><f>A1&amp;"!"</f><v>수식 결과</v></c></row>',
        ),
        "xl/worksheets/sheet2.xml": sheet(
            f'<row r="1"><c t="inlineStr"><is><t>{EMAIL}</t></is></c></row>'
        ),
        "xl/comments1.xml": f"<comments {S}><authors><author>{AUTHOR}</author>"
        "</authors><commentList>"
        "<comment><text><r><t>셀 메모</t></r></text></comment></commentList></comments>",
        "xl/drawings/drawing1.xml": f"<xdr:wsDr xmlns:xdr='x' {A}><a:p><a:r>"
        "<a:t>글상자</a:t></a:r></a:p></xdr:wsDr>",
        "xl/charts/chart1.xml": "<c/>",
        "xl/embeddings/oleObject1.bin": b"x",
        "docProps/core.xml": core(),
    }


def test_excel_sheet_names_hidden_sheets_and_rows_numbers_comments_and_boxes_are_read() -> None:
    found = read("a.xlsx", workbook_parts())

    assert found.text.startswith(
        "연락망\n숨긴 시트\n운영 담당\t1023456789\n굵은 글씨\t숨긴 행\t수식 결과"
    )
    for words in (EMAIL, "셀 메모", "글상자"):
        assert words in found.text, words
    assert "읽는법" not in found.text, "a reading aid is not the cell's text"
    assert 'A1&"!"' not in found.text, "the value is read, not the formula"
    assert AUTHOR not in found.text + found.marks
    assert found.not_read == ("charts", "embedded_files")


def noted(*notes: str, authors: tuple[str, ...] = (AUTHOR,)) -> dict[str, str | bytes]:
    """A workbook whose cell has notes; each is given as the runs of its text."""
    listed = "".join(f"<author>{name}</author>" for name in authors)
    return {
        "xl/workbook.xml": workbook(("예산", "visible")),
        "xl/worksheets/sheet1.xml": sheet(
            '<row r="1"><c t="inlineStr"><is><t>장비 예산</t></is></c></row>'
        ),
        "xl/comments1.xml": f"<comments {S}><authors>{listed}</authors><commentList>"
        + "".join(f'<comment authorId="0"><text>{note}</text></comment>' for note in notes)
        + "</commentList></comments>",
    }


def run(text: str, *, bold: bool = False) -> str:
    return f'<r>{"<rPr><b/></rPr>" if bold else ""}<t xml:space="preserve">{text}</t></r>'


def test_the_line_excel_opens_a_note_with_is_not_kept() -> None:
    # As Excel lays a new note out: the writer's name and a colon in bold,
    # then the note on the next line.
    found = read("a.xlsx", noted(run(f"{AUTHOR}:", bold=True) + run("\n재무팀 확인 필요")))

    assert found.text == "예산\n장비 예산\n재무팀 확인 필요"
    assert AUTHOR not in found.text + found.marks + found.head + found.foot


def test_a_note_whose_writer_deleted_that_line_reads_as_it_did() -> None:
    assert (
        read("a.xlsx", noted(run("\n재무팀 확인 필요"))).text == "예산\n장비 예산\n재무팀 확인 필요"
    )


def test_a_note_written_as_one_run_loses_the_same_line() -> None:
    found = read("a.xlsx", noted(run(f"{AUTHOR}:\n재무팀 확인 필요")))

    assert found.text == "예산\n장비 예산\n재무팀 확인 필요"


def test_the_writer_of_another_sheets_note_is_one_of_the_files_authors() -> None:
    parts = noted(run("이영희가상:\n다른 시트 사람의 메모"))
    parts["xl/comments2.xml"] = (
        f"<comments {S}><authors><author>이영희가상</author></authors><commentList>"
        "<comment><text><r><t>둘째 시트 메모</t></r></text></comment></commentList></comments>"
    )

    found = read("a.xlsx", parts)

    assert "이영희가상" not in found.text
    assert "다른 시트 사람의 메모" in found.text and "둘째 시트 메모" in found.text


@pytest.mark.parametrize(
    "note",
    [
        "박철수가상:\n목록에 없는 사람",  # not one of the file's note authors
        f"{AUTHOR}: 같은 줄에 이어 쓴 메모",  # not the line alone
        f"담당은 {AUTHOR}:\n아래와 같음",  # not the name alone
        f"확인 바람\n{AUTHOR}:\n둘째 줄의 이름",  # not the first line
        f"{AUTHOR}\n쌍점 없는 이름",
    ],
)
def test_a_name_written_any_other_way_in_a_note_stays(note: str) -> None:
    assert read("a.xlsx", noted(run(note))).text == f"예산\n장비 예산\n{note}"


def test_only_the_first_line_goes_from_a_note_that_repeats_it() -> None:
    found = read("a.xlsx", noted(run(f"{AUTHOR}:\n{AUTHOR}:\n본문")))

    assert found.text == f"예산\n장비 예산\n{AUTHOR}:\n본문"


def test_each_note_is_looked_at_on_its_own() -> None:
    found = read(
        "a.xlsx",
        noted(run(f"{AUTHOR}:") + run("\n첫 메모"), run("둘째 메모"), run(f"{AUTHOR}:\n셋째 메모")),
    )

    assert found.text == "예산\n장비 예산\n첫 메모\n둘째 메모\n셋째 메모"


def old_notes(*notes: tuple[str, str]) -> bytes:
    """An old-format workbook with a note for each (author, text), on the
    first cells of its last sheet. ``xls/contacts.xls``'s workbook stream with
    the three records of a note -- its object, its text, the note itself --
    written here before the sheet's end. Given to the reader as the stream,
    without the container; no Excel wrote it."""

    def record(kind: int, body: bytes) -> bytes:
        return struct.pack("<HH", kind, len(body)) + body

    def string(text: str) -> bytes:
        return b"\x01" + text.encode("utf-16-le")

    with olefile.OleFileIO(io.BytesIO((XLS / "contacts.xls").read_bytes())) as container:
        stream = container.openstream("Workbook").read()
    position = end = 0
    while position < len(stream):
        kind, size = struct.unpack_from("<HH", stream, position)
        if kind == 0x000A:  # the last end-of-sheet record
            end = position
        position += 4 + size
    added = b""
    for number, (author, text) in enumerate(notes, 1):
        runs = struct.pack("<HH4x", 0, 0) + struct.pack("<HH4x", len(text), 0)
        added += (
            record(0x005D, struct.pack("<HHHHH12x", 0x15, 0x12, 0x19, number, 0x4011) + bytes(4))
            + record(0x01B6, struct.pack("<HH6xHH4x", 0x0212, 0, len(text), len(runs)))
            + record(0x003C, string(text))
            + record(0x003C, runs)
            + record(
                0x001C,
                struct.pack("<HHHHH", 0, number - 1, 0, number, len(author))
                + string(author)
                + b"\0",
            )
        )
    return stream[:end] + added + stream[end:]


def test_an_old_workbooks_note_loses_the_line_that_names_its_writer() -> None:
    plain = read_file("contacts.xls", (XLS / "contacts.xls").read_bytes()).text

    found = read_file(
        "a.xls",
        old_notes(
            (AUTHOR, f"{AUTHOR}:\n재무팀 확인 필요"),
            (AUTHOR, f"{AUTHOR}: 같은 줄에 이어 쓴 메모"),
            ("", "박철수가상:\n목록에 없는 사람"),
        ),
    )

    assert found.text == (
        f"{plain}\n재무팀 확인 필요\n{AUTHOR}: 같은 줄에 이어 쓴 메모\n"
        "박철수가상:\n목록에 없는 사람"
    )
    assert AUTHOR not in plain, "the workbook itself does not say the name"


def test_a_number_cell_holding_a_phone_number_is_masked(session: Session) -> None:
    put_up(session, "a.xlsx", package(workbook_parts()))
    assert "운영 담당" in stored(session)
    assert "1023456789" not in stored(session) and EMAIL not in stored(session)


def test_a_marking_in_a_sheets_print_header_stops_it(session: Session) -> None:
    parts = workbook_parts()
    parts["xl/worksheets/sheet1.xml"] = (
        f"<worksheet {S}><sheetData><row><c t='inlineStr'><is><t>표</t></is></c></row></sheetData>"
        "<headerFooter><oddHeader>&amp;C대외비</oddHeader></headerFooter></worksheet>"
    )
    assert "대외비" in read("a.xlsx", parts).marks and "대외비" not in read("a.xlsx", parts).text
    with pytest.raises(ConfidentialFileError):
        put_up(session, "a.xlsx", package(parts))


def rows(*cells: str) -> str:
    return sheet(*(f"<row><c t='inlineStr'><is><t>{cell}</t></is></c></row>" for cell in cells))


def test_a_marking_at_the_top_of_the_first_sheet_stops_a_workbook(session: Session) -> None:
    parts = workbook_parts()
    parts["xl/worksheets/sheet1.xml"] = rows(*marked_at(5))
    with pytest.raises(ConfidentialFileError):
        put_up(session, "a.xlsx", package(parts))
    assert stored(session) == ""


def test_a_marking_in_a_sheets_print_footer_stops_it(session: Session) -> None:
    parts = workbook_parts()
    parts["xl/worksheets/sheet1.xml"] = rows("표").replace(
        "</worksheet>",
        "<headerFooter><oddFooter>&amp;C사외비 꼬리말</oddFooter></headerFooter></worksheet>",
    )
    found = read("a.xlsx", parts)
    assert "사외비" in found.marks and "사외비" not in found.text, "looked at, and not kept"
    with pytest.raises(ConfidentialFileError):
        put_up(session, "a.xlsx", package(parts))
    assert stored(session) == ""


def test_a_marking_lower_down_or_on_a_later_sheet_does_not_stop_a_workbook(
    session: Session,
) -> None:
    parts = workbook_parts()
    parts["xl/worksheets/sheet1.xml"] = rows(*marked_at(6))
    parts["xl/worksheets/sheet2.xml"] = rows("기밀 시트의 첫 줄")

    put_up(session, "a.xlsx", package(parts))

    assert "대외비" in stored(session) and "기밀 시트의 첫 줄" in stored(session)
    assert alarms(session) == []


# --- where a PDF page keeps its picture ---------------------------------------------

WRITTEN = (
    b"/Font << /F 3 0 R >>",
    b"BT /F 12 Tf 72 720 Td (The budget plan for the third quarter is agreed.) Tj ET",
)
DRAWS = b"q 400 0 0 500 100 150 cm /X Do Q"
INLINE = b"q 400 0 0 500 100 150 cm BI /W 1 /H 1 /CS /G /BPC 8 ID \x80 EI Q"
PICTURED: dict[str, tuple[bytes, bytes]] = {
    "in the page's own resources": (b"/XObject << /X 4 0 R >>", DRAWS),
    "inside a form object": (b"/XObject << /X 5 0 R >>", DRAWS),
    "inside a form object inside another": (b"/XObject << /X 6 0 R >>", DRAWS),
    "written into the page's content": (b"", INLINE),
    "written into a form object's content": (b"/XObject << /X 7 0 R >>", DRAWS),
}


def pdf(*pages: tuple[bytes, bytes]) -> bytes:
    """A PDF written by hand, object by object; a page is its resources and
    its content. Objects 3 to 8 are there for a page to name: a font, a
    one-dot picture, a form object holding the picture, one holding that
    form, one whose content has the picture inline, and one holding itself."""

    def stream(entries: bytes, data: bytes) -> bytes:
        return b"<< %s /Length %d >>\nstream\n%s\nendstream" % (entries, len(data), data)

    form = b"/Type /XObject /Subtype /Form /BBox [0 0 612 792] /Resources << %s >>"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # the page tree, once the pages have their numbers
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        stream(
            b"/Type /XObject /Subtype /Image /Width 1 /Height 1"
            b" /ColorSpace /DeviceGray /BitsPerComponent 8",
            b"\x80",
        ),
        stream(form % b"/XObject << /X 4 0 R >>", DRAWS),
        stream(form % b"/XObject << /X 5 0 R >>", DRAWS),
        stream(form % b"", INLINE),
        stream(form % b"/XObject << /X 8 0 R >>", b""),
    ]
    kids: list[int] = []
    for resources, content in pages:
        kids.append(len(objects) + 1)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << %s >>"
            b" /Contents %d 0 R >>" % (resources, len(objects) + 2)
        )
        objects.append(stream(b"", content))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % kid for kid in kids),
        len(kids),
    )
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    table = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        table,
    )
    return bytes(out)


def test_a_written_pdf_made_here_is_read_and_nothing_is_unread() -> None:
    found = read_file("plan.pdf", pdf(WRITTEN, WRITTEN))

    assert found.text.startswith("The budget plan for the third quarter is agreed.")
    assert found.not_read == ()


@pytest.mark.parametrize("where", PICTURED)
def test_a_page_that_is_only_a_picture_is_refused_wherever_the_picture_is_kept(where: str) -> None:
    with pytest.raises(UnreadableFileError) as among_written:
        read_file("plan.pdf", pdf(WRITTEN, PICTURED[where]))
    with pytest.raises(UnreadableFileError) as alone:
        read_file("plan.pdf", pdf(PICTURED[where]))

    assert among_written.value.reason == "page_without_text"
    assert alone.value.reason == "no_text_layer"


@pytest.mark.parametrize("where", PICTURED)
def test_a_picture_beside_a_pages_text_is_said_unread_wherever_it_is_kept(where: str) -> None:
    resources, content = PICTURED[where]

    found = read_file("plan.pdf", pdf((WRITTEN[0] + b" " + resources, WRITTEN[1] + b" " + content)))

    assert found.not_read == ("pictures",)
    assert found.text.startswith("The budget plan for the third quarter is agreed.")


def test_a_form_object_that_holds_no_picture_is_not_one() -> None:
    # Object 8 holds only itself: looking through it ends, and finds nothing.
    found = read_file("plan.pdf", pdf((WRITTEN[0] + b" /XObject << /X 8 0 R >>", WRITTEN[1])))

    assert found.not_read == ()


def test_a_pdf_page_refused_for_its_picture_stores_nothing(session: Session) -> None:
    with pytest.raises(AutuneError) as refused:
        put_up(session, "plan.pdf", pdf(WRITTEN, PICTURED["inside a form object"]))

    assert str(refused.value).endswith(": page_without_text") and refused.value.status_code == 422
    assert stored(session) == "" and materials.team_materials(session, TEAM) == []


# --- where text hides: PowerPoint -------------------------------------------------


def deck_parts(master: str = "Click to edit Master title style") -> dict[str, str | bytes]:
    return {
        "ppt/slides/slide1.xml": slide("첫 슬라이드", f"담당 {PHONE}"),
        "ppt/slides/slide2.xml": slide("숨긴 슬라이드").replace("<p:sld ", '<p:sld show="0" '),
        "ppt/slides/slide10.xml": slide("열째 슬라이드"),
        "ppt/notesSlides/notesSlide1.xml": slide("발표자 노트").replace("p:sld", "p:notes"),
        "ppt/comments/comment1.xml": f'<p:cmLst {P}><p:cm authorId="0">'
        "<p:text>슬라이드 의견</p:text></p:cm></p:cmLst>",
        "ppt/commentAuthors.xml": f'<p:cmAuthorLst {P}><p:cmAuthor name="{AUTHOR}"/>'
        "</p:cmAuthorLst>",
        "ppt/diagrams/data1.xml": f"<dgm:dataModel xmlns:dgm='x' {A}><a:p><a:r>"
        "<a:t>도식 글자</a:t></a:r></a:p></dgm:dataModel>",
        "ppt/slideMasters/slideMaster1.xml": slide(master).replace("p:sld", "p:sldMaster"),
        "ppt/media/image1.png": b"png",
        "docProps/core.xml": core(),
    }


def test_powerpoint_slides_hidden_slides_notes_comments_and_diagrams_are_read() -> None:
    found = read("a.pptx", deck_parts())

    assert (
        found.text.index("첫 슬라이드")
        < found.text.index("숨긴 슬라이드")
        < found.text.index("열째 슬라이드")
    )
    for words in ("발표자 노트", "슬라이드 의견", "도식 글자", PHONE):
        assert words in found.text, words
    assert "Click to edit Master" in found.marks and "Click to edit" not in found.text
    assert AUTHOR not in found.text + found.marks
    assert found.not_read == ("pictures",)


def test_a_marking_stamped_on_the_master_stops_the_deck(session: Session) -> None:
    with pytest.raises(ConfidentialFileError):
        put_up(session, "a.pptx", package(deck_parts("Company Confidential - do not distribute")))
    assert stored(session) == ""

    kept = put_up(session, "a.pptx", package(deck_parts()))
    assert kept.not_read == ("pictures",)
    assert "첫 슬라이드" in stored(session) and PHONE not in stored(session)
    assert "Click to edit" not in stored(session)


def test_a_marking_on_the_first_slide_stops_a_deck_and_one_on_a_later_slide_or_in_notes_does_not(
    session: Session,
) -> None:
    parts = deck_parts()
    parts["ppt/slides/slide1.xml"] = slide("대외비", "첫 슬라이드")
    with pytest.raises(ConfidentialFileError):
        put_up(session, "a.pptx", package(parts))
    assert stored(session) == ""

    parts = deck_parts()
    parts["ppt/slides/slide2.xml"] = slide("대외비 다루는 법")
    parts["ppt/notesSlides/notesSlide1.xml"] = slide("Confidential 노트").replace(
        "p:sld", "p:notes"
    )
    put_up(session, "b.pptx", package(parts))
    assert "대외비 다루는 법" in stored(session) and "Confidential 노트" in stored(session)


def test_a_marking_in_a_later_slides_footer_box_stops_a_deck(session: Session) -> None:
    box = (
        '<p:sp><p:nvSpPr><p:nvPr><p:ph type="{kind}"/></p:nvPr></p:nvSpPr>'
        "<p:txBody><a:p><a:r><a:t>Internal Use Only</a:t></a:r></a:p></p:txBody></p:sp></p:spTree>"
    )
    parts = deck_parts()
    parts["ppt/slides/slide10.xml"] = slide("열째 슬라이드").replace(
        "</p:spTree>", box.format(kind="ftr")
    )
    with pytest.raises(ConfidentialFileError):
        put_up(session, "a.pptx", package(parts))
    assert stored(session) == ""

    # The same words in a body box of that slide are body.
    parts["ppt/slides/slide10.xml"] = slide("열째 슬라이드").replace(
        "</p:spTree>", box.format(kind="body")
    )
    put_up(session, "b.pptx", package(parts))
    assert "Internal Use Only" in stored(session)


# --- refused ----------------------------------------------------------------------

OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600
ENTITIES = '<!DOCTYPE x [<!ENTITY a "aaaa">]>' + word("&a;")


@pytest.mark.parametrize(
    ("name", "data", "reason"),
    [
        ("a.doc", OLE, "unsupported_type"),
        ("a.hwp", b"hwp", "unsupported_type"),
        ("a.xlsx", OLE, "protected"),
        ("a.pptx", b"not a zip", "damaged"),
        ("a.xls", b"not a workbook", "damaged"),
        ("a.ppt", b"not a deck", "damaged"),
        ("a.pdf", b"%PDF-1.7 nothing", "damaged"),
        ("a.xlsx", package({"xl/styles.xml": "<x/>"}), "damaged"),
        ("a.pptx", package({"ppt/slides/slide1.xml": slide(" ")}), "empty"),
        ("a.docx", package({"word/document.xml": ENTITIES}), "damaged"),
        # The same part in UTF-16, where the two words are not the bytes looked for.
        ("a.docx", package({"word/document.xml": ENTITIES.encode("utf-16")}), "damaged"),
        (
            "a.docx",
            package(
                {"word/document.xml": word("x"), "word/media/big.bin": b"\0" * (81 * 1024 * 1024)}
            ),
            "too_large",
        ),
    ],
    ids=[
        "doc",
        "hwp",
        "encrypted-xlsx",
        "bad-pptx",
        "bad-xls",
        "bad-ppt",
        "bad-pdf",
        "no-workbook",
        "blank-deck",
        "entities",
        "entities-in-utf-16",
        "unpacks-huge",
    ],
)
def test_what_cannot_be_read_is_refused_with_a_reason(name: str, data: bytes, reason: str) -> None:
    with pytest.raises(UnreadableFileError) as refused:
        read_file(name, data)

    error = refused.value
    assert (error.status_code, error.details) == (422, {"field": "file", "reason": reason})
    assert str(error) == f"the file cannot be read: {reason}"


def quoting_workbook() -> bytes:
    """A workbook whose one cell points at a shared string by a phone number
    instead of an index: the reader's own error for it quotes the cell."""
    parts = workbook_parts()
    parts["xl/worksheets/sheet1.xml"] = sheet(f'<row r="1"><c t="s"><v>{PHONE}</v></c></row>')
    return package(parts)


def test_a_refusal_has_nothing_of_a_readers_own_error_behind_it() -> None:
    # What a handler that logs an exception with its chain would write.
    with pytest.raises(UnreadableFileError) as refused:
        read_file("a.xlsx", quoting_workbook())

    error = refused.value
    assert error.reason == "damaged"
    assert error.__cause__ is None and error.__context__ is None
    assert PHONE not in "".join(traceback.format_exception(error))
    assert PHONE not in repr(error.to_dict()) + str(error)


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("a.pdf", b"%PDF-1.7 nothing"),
        ("a.xls", b"not a workbook"),
        ("a.ppt", b"not a deck"),
        ("a.pptx", b"not a zip"),
        ("a.docx", package({"word/document.xml": f"<w:document {W}><w:body>{PHONE}"})),
        ("a.xlsx", OLE),
        ("a.txt", b"\x80\x80\xff\xfe"),
        ("password.xls", (POI / "password.xls").read_bytes()),
        ("scan.pdf", (PDF / "scan.pdf").read_bytes()),
    ],
    ids=[
        "bad-pdf",
        "bad-xls",
        "bad-ppt",
        "bad-pptx",
        "broken-xml",
        "encrypted-xlsx",
        "encoding",
        "protected-xls",
        "scan",
    ],
)
def test_no_refusal_carries_another_error(name: str, data: bytes) -> None:
    with pytest.raises(UnreadableFileError) as refused:
        read_file(name, data)
    assert refused.value.__cause__ is None and refused.value.__context__ is None


def test_a_refused_upload_stores_nothing_and_logs_nothing_of_the_file(
    session: Session, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(UnreadableFileError):
        put_up(session, "연락망.xlsx", quoting_workbook())

    assert materials.team_materials(session, TEAM) == [] and alarms(session) == []
    printed = capsys.readouterr()
    assert PHONE not in printed.out + printed.err + caplog.text


def test_a_readers_own_complaints_about_a_file_are_not_printed_or_logged(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    for name, data in (("a.pdf", b"%PDF-1.7 nothing"), ("a.xls", b"not a workbook")):
        with pytest.raises(UnreadableFileError):
            read_file(name, data)
    printed = capsys.readouterr()
    assert printed.out == "" and printed.err == "" and caplog.records == []


def test_a_missing_reader_is_not_called_a_damaged_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def not_installed(_: bytes | bytearray) -> FileText:
        raise ImportError("No module named 'pypdf'")

    monkeypatch.setitem(material_formats.READERS, ".pdf", not_installed)
    with pytest.raises(ImportError):
        read_file("a.pdf", (PDF / "short.pdf").read_bytes())


def test_text_a_reader_took_out_wrongly_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # A control character in what a reader returns: text no detector reads as
    # a person would. Refused like a binary file under a text name.
    monkeypatch.setitem(
        material_formats.READERS,
        ".ppt",
        lambda _: FileText(text="담당 010\x002345\x006789"),
    )
    with pytest.raises(UnreadableFileError) as refused:
        read_file("a.ppt", b"deck")
    assert refused.value.reason == "damaged"


def test_what_is_read_only_for_the_marking_counts_toward_the_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autune_extraction.material_reading import MAX_TEXT_CHARS

    monkeypatch.setitem(
        material_formats.READERS,
        ".pptx",
        lambda _: FileText(text="본문", marks="x" * MAX_TEXT_CHARS),
    )
    with pytest.raises(UnreadableFileError) as refused:
        read_file("a.pptx", b"deck")
    assert refused.value.reason == "too_long"


def test_the_list_of_formats_is_what_the_readers_take() -> None:
    assert SUFFIXES == (".txt", ".md", ".csv", ".docx", ".xlsx", ".pptx", ".pdf", ".xls", ".ppt")
