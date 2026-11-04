import asyncio
from types import SimpleNamespace

import pytest
from docling_core.types.doc.base import BoundingBox, CoordOrigin, Size
from docling_core.types.doc.document import DocItemLabel, DoclingDocument, ProvenanceItem
from haiku.rag.store.models import SearchResult

from haiku_rag_macos import search as search_module
from haiku_rag_macos.search import SearchService, id_filter


def stored_document() -> tuple[DoclingDocument, str]:
    document = DoclingDocument(name="t")
    item = document.add_text(
        label=DocItemLabel.TEXT,
        text="Travis picking",
        prov=ProvenanceItem(
            page_no=2,
            bbox=BoundingBox(l=10, t=700, r=110, b=650, coord_origin=CoordOrigin.BOTTOMLEFT),
            charspan=(0, 14),
        ),
    )
    return document, item.self_ref


class Repository:
    def __init__(self, document: DoclingDocument | None, with_pages: bool = True):
        self.document = document
        self.with_pages = with_pages
        self.reads = 0

    async def get_docling_data(self, document_id):
        self.reads += 1
        if self.document is None:
            return None
        return SimpleNamespace(get_docling_document=lambda: self.document)

    async def get_pages_data(self, document_id):
        if not self.with_pages:
            return None
        size = SimpleNamespace(width=612.0, height=792.0)
        return SimpleNamespace(
            get_page_images=lambda numbers: {
                n: SimpleNamespace(size=size) for n in numbers if n == 2
            }
        )


class Rag:
    """The read-only client SearchService opens, over one database."""

    def __init__(self, results, repository):
        self.results = results
        self.document_repository = repository
        self.searches = []
        self.source_names = ("files",)

    async def search(self, query, **options):
        self.searches.append((query, options))
        return self.results

    async def expand_context(self, results):
        return [
            SearchResult(content="the whole section", score=1, source="files", chunk_id="c1",
                         chunk_ids=["c1", "c9"])
        ]  # fmt: skip

    async def reader_for(self, source):
        return self if source == "files" else None

    async def count_documents(self, filter=None):
        return 3 if filter else 10


def result(**fields) -> SearchResult:
    base = {
        "content": "Travis picking is a fingerstyle technique.",
        "score": 0.5,
        "source": "files",
        "chunk_id": "c1",
        "document_id": "d1",
        "document_uri": "file:///Users/me/Guitar/country.pdf",
        "document_meta": {"source_id": "guitar", "content_type": "application/pdf"},
        "page_numbers": [2],
        "labels": ["text"],
    }
    return SearchResult(**{**base, **fields})


@pytest.fixture
def opened(monkeypatch):
    """SearchService over a fake client; yields a setter for its results."""
    state = {}

    class HaikuRAG:
        def __init__(self, config=None, read_only=False):
            assert read_only
            state["opened"] = state.get("opened", 0) + 1

        async def __aenter__(self):
            return state["rag"]

        async def __aexit__(self, *exc):
            state["closed"] = state.get("closed", 0) + 1

    monkeypatch.setattr("haiku.rag.client.HaikuRAG", HaikuRAG)
    return state


def run(coroutine):
    return asyncio.run(coroutine)


def test_hits_carry_boxes_resolved_from_provenance(opened):
    document, ref = stored_document()
    repository = Repository(document)
    opened["rag"] = Rag([result(doc_item_refs=[ref]), result(chunk_id="c2", doc_item_refs=[ref])], repository)
    service = SearchService(config=None, search_idle_s=60)

    async def go():
        response = await service.search("travis picking", limit=5, folders=["guitar"],
                                        document_id="d1", context=True)  # fmt: skip
        await service.aclose()
        return response

    response = run(go())

    first, second = response.hits
    assert first.folder_id == "guitar" and first.document_type == "pdf"
    assert first.path == "/Users/me/Guitar/country.pdf" and first.title == "country.pdf"
    assert [(p.page, p.width, p.height) for p in first.pages] == [(2, 612.0, 792.0)]
    assert first.pages[0].boxes[0].top == pytest.approx(92.0)
    assert first.context == "the whole section" and second.context is None
    assert [first.snippet[a:b] for a, b in first.snippet_highlights] == ["Travis", "picking"]
    # The stored document was read once for both hits.
    assert repository.reads == 1
    query, options = opened["rag"].searches[0]
    assert options["filter"].startswith(id_filter("d1") + " AND (metadata LIKE")
    assert options["include_images"] is False
    assert opened == {"rag": opened["rag"], "opened": 1, "closed": 1}


def test_hits_use_boxes_a_chunk_recorded(opened):
    recorded = [{"page": 4, "width": 100, "height": 200, "boxes": [[1, 2, 3, 4]]}]
    repository = Repository(None)
    opened["rag"] = Rag([result(chunk_meta={"locations": recorded}, doc_item_refs=["#/texts/0"])], repository)
    response = run(SearchService(config=None).search("x", limit=1))
    assert response.hits[0].pages[0].boxes[0].bottom == 4
    assert repository.reads == 0


def test_documents_without_provenance_or_pages(opened, tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("# Title\n\nThe chain rule says so.\n", encoding="utf-8")
    document, ref = stored_document()
    rag = Rag(
        [
            result(document_uri=notes.as_uri(), document_meta={}, page_numbers=[],
                   content="The chain rule says so."),
            result(chunk_id="c3", doc_item_refs=[ref], document_id="gone"),
        ],
        Repository(None),
    )  # fmt: skip
    opened["rag"] = rag
    service = SearchService(config=None)
    markdown, missing = run(service.search("chain rule", limit=2)).hits
    assert markdown.line == 3 and markdown.pages == [] and markdown.document_type == "markdown"
    assert missing.pages == []
    # Pages sizes from the structure when no pages are stored.
    document.add_page(page_no=2, size=Size(width=300, height=400))
    rag.document_repository = Repository(document, with_pages=False)
    rag.results = [result(doc_item_refs=[ref], document_id="d5")]
    [hit] = run(SearchService(config=None).search("x", limit=1)).hits
    assert (hit.pages[0].width, hit.pages[0].height) == (300, 400)


def test_locate_without_a_document_or_database(opened):
    service = SearchService(config=None)
    rag = Rag([], Repository(None))
    assert run(service.locate(rag, result(document_id=None))) == []
    assert run(service.locate(rag, result(source="other"))) == []


def test_counts(opened):
    opened["rag"] = Rag([], Repository(None))
    service = SearchService(config=None)
    assert run(service.document_counts()) == [{"name": "files", "documents": 10}]
    assert run(service.folder_documents("files", "guitar")) == 3


def test_the_client_closes_after_its_idle_time(opened):
    opened["rag"] = Rag([], Repository(None))
    service = SearchService(config=None, search_idle_s=0.05)

    async def go():
        async with service.client():
            assert service.loaded
        await asyncio.sleep(0.3)
        return service.loaded

    assert run(go()) is False
    assert opened["closed"] == 1


def test_id_filter_quotes():
    assert id_filter("a'b") == "id = 'a''b'"
    assert search_module.SearchService  # the module imports without haiku.rag's client
