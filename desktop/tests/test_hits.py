from pathlib import Path

import pytest
from docling_core.types.doc.base import BoundingBox, CoordOrigin, Size
from docling_core.types.doc.document import DocItemLabel, DoclingDocument, ProvenanceItem

from haiku_rag_macos.hits import (
    document_type,
    find_line,
    item_locations,
    local_path,
    page_location,
    query_terms,
    snippet,
)


@pytest.mark.parametrize(
    ("uri", "content_type", "kind"),
    [
        ("file:///a/b.PDF", None, "pdf"),
        ("file:///a/notes.md", None, "markdown"),
        ("file:///a/script.py", None, "code"),
        ("file:///a/data.json", None, "code"),
        ("file:///a/book.epub", None, "epub"),
        ("file:///a/slides.pptx", None, "powerpoint"),
        ("https://example.com/report", "application/pdf", "pdf"),
        ("file:///a/b.pdf#attachment=x.txt", None, "pdf"),
        (None, None, "other"),
    ],
)
def test_document_type_follows_the_extension(uri, content_type, kind):
    assert document_type(uri, content_type) == kind


def test_local_path_only_for_local_files():
    assert local_path("file:///Users/me/A%20B/c.pdf") == Path("/Users/me/A B/c.pdf")
    assert local_path("file://localhost/x.md") == Path("/x.md")
    assert local_path("file:///x.pdf#attachment=y.txt") is None
    assert local_path("https://example.com/x.pdf") is None
    assert local_path(None) is None


def test_query_terms_drop_stopwords_and_repeats():
    assert query_terms("The chain rule and the CHAIN of a derivative") == [
        "chain",
        "rule",
        "derivative",
    ]


def test_snippet_centres_on_a_term_and_marks_every_one():
    text = "word " * 100 + "Travis picking is a technique. Travis played it. " + "tail " * 100
    shown, highlights = snippet(text, ["travis"], 120)
    assert shown.startswith("...") and shown.endswith("...")
    assert len(shown) <= 126
    assert [shown[a:b] for a, b in highlights] == ["Travis", "Travis"]
    short, marks = snippet("Short text.", [], 120)
    assert short == "Short text." and marks == []


def test_find_line_places_joined_lines(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("intro\nZebra quokka marmalade is a phrase.\nSecond line.\n", encoding="utf-8")
    # The indexed text joined the file's lines into one.
    assert find_line(path, "Zebra quokka marmalade is a phrase. Second line.") == 2
    assert find_line(path, "# Not there at all") is None
    assert find_line(tmp_path / "missing.txt", "Zebra quokka") is None


def test_item_locations_use_provenance_in_top_left_coordinates():
    document = DoclingDocument(name="t")
    document.add_page(page_no=1, size=Size(width=600, height=800))
    document.add_page(page_no=2, size=Size(width=600, height=800))
    first = document.add_text(
        label=DocItemLabel.TEXT,
        text="on page two",
        prov=ProvenanceItem(
            page_no=2,
            bbox=BoundingBox(l=10, t=700, r=110, b=650, coord_origin=CoordOrigin.BOTTOMLEFT),
            charspan=(0, 11),
        ),
    )
    second = document.add_text(
        label=DocItemLabel.TEXT,
        text="on page one",
        prov=ProvenanceItem(
            page_no=1,
            bbox=BoundingBox(l=5, t=20, r=50, b=40, coord_origin=CoordOrigin.TOPLEFT),
            charspan=(0, 11),
        ),
    )
    sizes = {1: (600.0, 800.0), 2: (600.0, 800.0)}

    located = item_locations(document, sizes, [first.self_ref, second.self_ref, "#/texts/9", "x"])

    assert located == [
        {"page": 1, "width": 600.0, "height": 800.0, "boxes": [[5.0, 20.0, 50.0, 40.0]]},
        {"page": 2, "width": 600.0, "height": 800.0, "boxes": [[10.0, 100.0, 110.0, 150.0]]},
    ]
    # A page of unknown size is left out.
    assert item_locations(document, {1: (600.0, 800.0)}, [first.self_ref]) == []
    page = page_location(located[1])
    assert (page.page, page.boxes[0].top, page.boxes[0].bottom) == (2, 100.0, 150.0)
