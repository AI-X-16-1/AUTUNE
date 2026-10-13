"""The places a marking is looked for, where a file may put them elsewhere (#1252).

The marking check looks at a file's head, its foot and its own label. Some of
those places could be missed in a file that was taken all the same:

- the document properties, which a package may keep under any name: they are
  read where the package's own relationships say they are;
- a deck's layouts, which a slide master and a slide name and may name
  anything: a deck with one under another name is refused.

Every value here is invented.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from autune_extraction.material_reading import UnreadableFileError
from autune_extraction.materials import ConfidentialFileError

from .test_material_formats import (
    AUTHOR,
    PHONE,
    alarms,
    core,
    package,
    put_up,
    read,
    rows,
    session,  # noqa: F401 -- the fixture
    slide,
    stored,
    word,
)
from .test_material_named_parts import book, names, show
from .test_material_stream import Parts, reason

PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships/"
OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
STRICT = "http://purl.oclc.org/ooxml/officeDocument/relationships/"
CORE_KIND = f"{PACKAGE}metadata/core-properties"
CUSTOM_KIND = f"{OFFICE}custom-properties"
MARKING = "대외비"
ROOT = "_rels/.rels"

PLAIN: dict[str, Parts] = {
    "a.docx": {"word/document.xml": word("평범한 본문입니다.")},
    "a.xlsx": book(("sheet1.xml", rows("출시 일정"))),
    "a.pptx": show(("slide1.xml", slide("첫 장"))),
}
"""A file of each kind with nothing marked in it."""


def package_names(*named: tuple[str, str], mode: str = "") -> str:
    """The package's own relationships: a type and the part it names, for each."""
    each = "".join(
        f'<Relationship Id="rId{n}" Type="{kind}" Target="{target}"{mode}/>'
        for n, (kind, target) in enumerate(named, 1)
    )
    return (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{each}</Relationships>"
    )


def label(value: str) -> str:
    """Custom properties holding a sensitivity label, as Office writes one."""
    return (
        '<Properties xmlns:vt="x"><property name="MSIP_Label_1_Name">'
        f"<vt:lpwstr>{value}</vt:lpwstr></property></Properties>"
    )


def stopped(session: Session, name: str, parts: Parts) -> None:  # noqa: F811 -- the fixture
    """The file is stopped for its marking: one alarm, nothing kept."""
    with pytest.raises(ConfidentialFileError):
        put_up(session, name, package(parts))
    assert len(alarms(session)) == 1 and stored(session) == ""


# --- the document properties, where the package says they are -----------------------


@pytest.mark.parametrize("name", PLAIN)
def test_core_properties_under_another_name_are_looked_at(
    session: Session,  # noqa: F811 -- the fixture
    name: str,
) -> None:
    """Three files Word names itself in a survey of 597 keep them under
    ``docProps/core0.xml`` and the like; each was taken with its title,
    subject and keywords out of the marking check."""
    parts = PLAIN[name] | {
        ROOT: package_names((CORE_KIND, "docProps/core0.xml")),
        "docProps/core0.xml": core(**{"cp:keywords": f"계획, {MARKING}"}),
    }

    stopped(session, name, parts)


@pytest.mark.parametrize(
    "kind", [CUSTOM_KIND, f"{STRICT}custom-properties", f"{STRICT}customProperties"]
)
def test_a_label_property_under_another_name_is_looked_at(
    session: Session,  # noqa: F811 -- the fixture
    kind: str,
) -> None:
    parts = PLAIN["a.xlsx"] | {
        ROOT: package_names((kind, "meta/labels.xml")),
        "meta/labels.xml": label("Confidential"),
    }

    stopped(session, "a.xlsx", parts)


def test_a_part_named_from_the_packages_top_is_found() -> None:
    parts = PLAIN["a.docx"] | {
        ROOT: package_names((CORE_KIND, "/meta/core.xml")),
        "meta/core.xml": core(**{"dc:title": "문서 제목"}),
    }

    assert read("a.docx", parts).marks == "문서 제목"


def test_every_properties_part_the_package_names_is_looked_at(
    session: Session,  # noqa: F811 -- the fixture
) -> None:
    parts = PLAIN["a.docx"] | {
        ROOT: package_names((CORE_KIND, "docProps/core1.xml"), (CORE_KIND, "docProps/core2.xml")),
        "docProps/core1.xml": core(**{"dc:title": "문서 제목"}),
        "docProps/core2.xml": core(**{"dc:subject": MARKING}),
    }

    stopped(session, "a.docx", parts)


def test_the_usual_name_is_looked_at_whether_the_package_names_it_or_not(
    session: Session,  # noqa: F811 -- the fixture
) -> None:
    """A package that names its properties elsewhere, or names none, does not
    take the part under the usual name out of the check: what stopped a file
    before stops it still."""
    elsewhere = PLAIN["a.docx"] | {
        ROOT: package_names((CORE_KIND, "docProps/core0.xml")),
        "docProps/core0.xml": core(**{"dc:title": "문서 제목"}),
        "docProps/core.xml": core(**{"dc:title": MARKING}),
    }
    none = PLAIN["a.docx"] | {
        ROOT: package_names((f"{OFFICE}officeDocument", "word/document.xml")),
        "docProps/custom.xml": label("Confidential"),
    }

    stopped(session, "a.docx", elsewhere)
    with pytest.raises(ConfidentialFileError):
        put_up(session, "b.docx", package(none))


def test_a_part_named_twice_is_read_once() -> None:
    parts = PLAIN["a.docx"] | {
        ROOT: package_names((CORE_KIND, "docProps/core.xml"), (CORE_KIND, "docProps/core.xml")),
        "docProps/core.xml": core(**{"dc:title": "문서 제목"}),
    }

    assert read("a.docx", parts).marks == "문서 제목"


@pytest.mark.parametrize(
    ("target", "mode"),
    [
        ("docProps/core0.xml", ""),  # named, and not in the file
        ("../core.xml", ""),  # outside the package
        ("https://example.com/core.xml", ' TargetMode="External"'),  # somewhere else
    ],
)
@pytest.mark.parametrize("kind", [CORE_KIND, CUSTOM_KIND])
def test_a_properties_part_that_cannot_be_read_refuses_the_file(
    session: Session,  # noqa: F811 -- the fixture
    kind: str,
    target: str,
    mode: str,
) -> None:
    """What the file calls its label cannot be looked at, so the file is not
    taken. Nothing is fetched: an address is a name that is not in the file."""
    parts = PLAIN["a.docx"] | {ROOT: package_names((kind, target), mode=mode)}

    with pytest.raises(UnreadableFileError) as refused:
        put_up(session, "a.docx", package(parts))

    assert refused.value.details["reason"] == "damaged"
    assert alarms(session) == [] and stored(session) == ""


def test_properties_read_elsewhere_are_looked_at_and_not_kept(
    session: Session,  # noqa: F811 -- the fixture
) -> None:
    parts = PLAIN["a.docx"] | {
        ROOT: package_names((CORE_KIND, "docProps/core0.xml"), (CUSTOM_KIND, "meta/labels.xml")),
        "docProps/core0.xml": core(**{"dc:title": "내부 제목 낱말", "dc:subject": PHONE}),
        "meta/labels.xml": label("일반"),
    }

    found = read("a.docx", parts)
    put_up(session, "a.docx", package(parts))

    assert "내부 제목 낱말" in found.marks and "일반" in found.marks
    assert AUTHOR not in found.marks and AUTHOR not in found.text
    assert stored(session) == "평범한 본문입니다."


def test_the_properties_that_say_who_wrote_the_file_are_not_read() -> None:
    """``docProps/app.xml``: the company and the manager, and here a title
    and a nested value too, so that reading it as either kind would show."""
    parts = PLAIN["a.docx"] | {
        ROOT: package_names((f"{OFFICE}extended-properties", "docProps/app.xml")),
        "docProps/app.xml": (
            f"<Properties><Company>{AUTHOR}</Company><Manager>{AUTHOR}</Manager>"
            f"<title>{AUTHOR}</title><TitlesOfParts><part>{AUTHOR}</part></TitlesOfParts>"
            "</Properties>"
        ),
    }

    found = read("a.docx", parts)

    assert AUTHOR not in found.marks and AUTHOR not in found.text


def test_a_package_whose_own_relationships_cannot_be_read_is_refused() -> None:
    """Where it keeps its properties cannot be told. One sample of 669 is
    such a file, broken on purpose; it was taken before, by the usual names."""
    parts = PLAIN["a.docx"] | {ROOT: "CORRUPTED"}

    assert reason("a.docx", package(parts)) == "damaged"


def test_the_packages_relationships_are_read_as_carefully_as_any_part() -> None:
    """They are a part now read, and one that declares an entity is refused
    before a parser sees it, like any other."""
    declared = '<!DOCTYPE r [<!ENTITY a "aaaaaaaaaa">]>' + package_names(
        (CORE_KIND, "docProps/core.xml")
    )
    parts = PLAIN["a.docx"] | {ROOT: declared, "docProps/core.xml": core()}

    assert reason("a.docx", package(parts)) == "damaged"


# --- a layout under another name ------------------------------------------------------

MASTER = "ppt/slideMasters/slideMaster1.xml"
MASTER_NAMES = "ppt/slideMasters/_rels/slideMaster1.xml.rels"
SLIDE_NAMES = "ppt/slides/_rels/slide1.xml.rels"


def laid_out(layout: str, said: str) -> Parts:
    """A deck of one slide and one master, whose master names one layout:
    the layout's name under ``ppt/`` and what is written on it."""
    parts = show(("slide1.xml", slide("첫 장")), masters=(("slideMaster1.xml", slide("바탕")),))
    parts[MASTER_NAMES] = names(("slideLayout", f"../{layout}"), ("theme", "../theme/theme1.xml"))
    parts[f"ppt/{layout}"] = slide(said)
    return parts


def test_a_marking_on_a_layout_with_another_name_no_longer_gets_the_file_in(
    session: Session,  # noqa: F811 -- the fixture
) -> None:
    """A layout is where a deck's "Confidential" is stamped, and it is read
    for the marking check by its usual name. Under another name it was left
    out of the check and the deck was taken; now the deck is refused as one
    that cannot be read, and nothing of it is kept."""
    stopped(session, "a.pptx", laid_out("slideLayouts/slideLayout1.xml", MARKING))
    told = alarms(session)

    with pytest.raises(UnreadableFileError) as refused:
        put_up(session, "b.pptx", package(laid_out("slideLayouts/cover.xml", MARKING)))

    assert refused.value.details["reason"] == "damaged"
    assert alarms(session) == told and stored(session) == ""


@pytest.mark.parametrize("layout", ["slideLayouts/cover.xml", "layouts/slideLayout1.xml"])
def test_a_deck_whose_master_names_a_layout_under_another_name_is_refused(layout: str) -> None:
    assert reason("a.pptx", package(laid_out(layout, "표지"))) == "damaged"


def test_a_deck_whose_slide_names_a_layout_under_another_name_is_refused() -> None:
    """The layout a slide is laid out on is the one that stamps it."""
    parts = laid_out("slideLayouts/slideLayout1.xml", "표지")
    parts[SLIDE_NAMES] = names(("slideLayout", "../slideLayouts/cover.xml"))
    parts["ppt/slideLayouts/cover.xml"] = slide(MARKING)

    assert reason("a.pptx", package(parts)) == "damaged"


def test_a_layout_that_is_named_and_is_not_in_the_file_is_refused() -> None:
    parts = laid_out("slideLayouts/slideLayout1.xml", "표지")
    parts[MASTER_NAMES] = names(
        ("slideLayout", "../slideLayouts/slideLayout1.xml"),
        ("slideLayout", "../slideLayouts/slideLayout2.xml"),
    )

    assert reason("a.pptx", package(parts)) == "damaged"


def test_the_strict_flavours_layout_is_the_same_kind_and_its_names_are_found_in_any_case() -> None:
    strict = laid_out("slideLayouts/cover.xml", "표지")
    strict[MASTER_NAMES] = names(("slideLayout", "../slideLayouts/cover.xml"), kinds=STRICT)
    cased = laid_out("slideLayouts/cover.xml", "표지")
    cased["ppt/slideMasters/_RELS/SlideMaster1.xml.rels"] = cased.pop(MASTER_NAMES)

    assert reason("a.pptx", package(strict)) == "damaged"
    assert reason("a.pptx", package(cased)) == "damaged"


def test_a_deck_laid_out_as_powerpoint_lays_one_out_is_read() -> None:
    """The master names its layouts, each slide names its layout and its
    notes, each layout names its master back -- under the usual names."""
    parts = laid_out("slideLayouts/slideLayout1.xml", "표지 배치")
    parts[MASTER_NAMES] = names(
        ("slideLayout", "../slideLayouts/slideLayout1.xml"),
        ("slideLayout", "../slideLayouts/slideLayout2.xml"),
        ("theme", "../theme/theme1.xml"),
    )
    parts["ppt/slideLayouts/slideLayout2.xml"] = slide("본문 배치")
    parts["ppt/slideLayouts/_rels/slideLayout1.xml.rels"] = names(
        ("slideMaster", "../slideMasters/slideMaster1.xml")
    )
    parts[SLIDE_NAMES] = names(
        ("slideLayout", "../slideLayouts/slideLayout2.xml"),
        ("notesSlide", "../notesSlides/notesSlide1.xml"),
        ("image", "../media/image1.png"),
    )
    parts["ppt/notesSlides/notesSlide1.xml"] = slide("발표자 노트")
    parts["ppt/media/image1.png"] = "그림"

    found = read("a.pptx", parts)

    assert found.text == "첫 장\n발표자 노트"
    assert found.marks == "바탕\n표지 배치\n본문 배치"
    assert found.not_read == ("pictures",)


def test_what_a_slide_names_besides_its_layout_is_still_not_reached() -> None:
    """The written limit: speaker notes under another name are left unread
    and the deck is taken. No marking is looked for in notes under any name."""
    parts = laid_out("slideLayouts/slideLayout1.xml", "표지")
    parts[SLIDE_NAMES] = names(("notesSlide", "../notes/first.xml"))
    parts["ppt/notes/first.xml"] = slide("발표자 노트")

    assert read("a.pptx", parts).text == "첫 장"
