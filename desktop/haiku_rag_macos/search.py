"""Searches the configured databases through one read-only haiku.rag client.

The client opens on the first search and closes after `search_idle_s` without
one. Hits carry their page boxes: chunks indexed by the earlier haiku.rag fork
recorded them; for the others they are resolved from the stored document's
provenance, the way haiku.rag's `visualize_chunk` resolves them, keeping the
last few documents read.
"""

import asyncio
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any, Literal

from haiku_rag_macos.hits import (
    _LINE_TYPES,
    LOCATIONS_KEY,
    Hit,
    SearchResponse,
    document_type,
    find_line,
    item_locations,
    local_path,
    page_location,
    query_terms,
    snippet,
)
from haiku_rag_macos.hook import source_filter

if TYPE_CHECKING:
    from docling_core.types.doc.document import DoclingDocument

    from haiku.rag.client import HaikuRAG
    from haiku.rag.config import AppConfig
    from haiku.rag.store.models import SearchResult

SearchType = Literal["hybrid", "vector", "fts"]


def id_filter(document_id: str) -> str:
    return "id = '" + document_id.replace("'", "''") + "'"


class _Located:
    """A stored document's structure and page sizes, for boxes."""

    def __init__(self, document: "DoclingDocument | None"):
        self.document = document
        self.sizes: dict[int, tuple[float, float]] = {}


class SearchService:
    def __init__(self, config: "AppConfig", *, search_idle_s: float = 300, snippet_chars: int = 300):
        self._config = config
        self.search_idle_s = search_idle_s
        self.snippet_chars = snippet_chars
        self._lock = asyncio.Lock()
        self._stack: AsyncExitStack | None = None
        self._client: HaikuRAG | None = None
        self._active = 0
        self._timer: asyncio.TimerHandle | None = None
        self._documents: OrderedDict[tuple[str | None, str], _Located] = OrderedDict()

    @property
    def loaded(self) -> bool:
        return self._client is not None

    @asynccontextmanager
    async def client(self) -> AsyncIterator["HaikuRAG"]:
        """The open client, held open while the caller uses it."""
        async with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if self._client is None:
                await self._open()
            self._active += 1
            assert self._client is not None
            client = self._client
        try:
            yield client
        finally:
            self._active -= 1
            if self._active == 0:
                loop = asyncio.get_running_loop()
                self._timer = loop.call_later(self.search_idle_s, self._idle)

    async def _open(self) -> None:
        from haiku.rag.client import HaikuRAG

        stack = AsyncExitStack()
        self._client = await stack.enter_async_context(
            HaikuRAG(config=self._config, read_only=True)
        )
        self._stack = stack

    def _idle(self) -> None:
        self._timer = None
        asyncio.ensure_future(self._close_if_idle())

    async def _close_if_idle(self) -> None:
        async with self._lock:
            if self._active == 0:
                await self._close()

    async def _close(self) -> None:
        stack, self._stack, self._client = self._stack, None, None
        self._documents.clear()
        if stack is not None:
            await stack.aclose()

    async def aclose(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        async with self._lock:
            await self._close()

    # --- counts ---------------------------------------------------------------

    async def document_counts(self) -> list[dict[str, Any]]:
        async with self.client() as rag:
            counts = []
            for name in rag.source_names:
                owner = await rag.reader_for(name)
                assert owner is not None
                counts.append({"name": name, "documents": await owner.count_documents()})
            return counts

    async def folder_documents(self, database: str, folder_id: str) -> int:
        async with self.client() as rag:
            owner = await rag.reader_for(database)
            assert owner is not None
            return await owner.count_documents(filter=source_filter(folder_id))

    # --- search ---------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        limit: int,
        search_type: SearchType = "hybrid",
        databases: list[str] | None = None,
        folders: list[str] | None = None,
        document_id: str | None = None,
        context: bool = False,
    ) -> SearchResponse:
        started = time.perf_counter()
        clauses = []
        if document_id is not None:
            clauses.append(id_filter(document_id))
        if folders:
            clauses.append("(" + " OR ".join(source_filter(f) for f in folders) + ")")
        async with self.client() as rag:
            results = await rag.search(
                query,
                limit=limit,
                search_type=search_type,
                filter=" AND ".join(clauses) or None,
                include_images=False,
                sources=databases,
            )
            expanded = await rag.expand_context(results) if context else None
            terms = query_terms(query)
            hits = [
                await self._hit(rag, rank, result, terms)
                for rank, result in enumerate(results, 1)
            ]
        if expanded is not None:
            contexts = {
                (wider.source, chunk_id): wider.content
                for wider in expanded
                for chunk_id in wider.chunk_ids or [wider.chunk_id]
            }
            for hit in hits:
                hit.context = contexts.get((hit.database, hit.chunk_id))
        return SearchResponse(
            query=query,
            took_ms=round((time.perf_counter() - started) * 1000, 1),
            hits=hits,
        )

    async def _hit(
        self, rag: "HaikuRAG", rank: int, result: "SearchResult", terms: list[str]
    ) -> Hit:
        meta = result.document_meta or {}
        content_type = meta.get("content_type")
        kind = document_type(result.document_uri, content_type)
        path = local_path(result.document_uri)
        line = None
        if path is not None and kind in _LINE_TYPES:
            line = await asyncio.to_thread(find_line, path, result.content)
        shown, highlights = snippet(result.content, terms, self.snippet_chars)
        locations = (result.chunk_meta or {}).get(LOCATIONS_KEY)
        if locations is None and result.page_numbers and result.doc_item_refs:
            locations = await self.locate(rag, result)
        return Hit(
            rank=rank,
            score=result.score,
            chunk_id=result.chunk_id,
            document_id=result.document_id,
            database=result.source,
            folder_id=meta.get("source_id"),
            title=result.document_title or (path.name if path is not None else None),
            path=str(path) if path is not None else None,
            uri=result.document_uri,
            document_type=kind,
            content_type=content_type,
            page_numbers=result.page_numbers,
            line=line,
            headings=result.headings,
            labels=result.labels,
            snippet=shown,
            snippet_highlights=highlights,
            text=result.content,
            pages=[page_location(location) for location in locations or []],
        )

    async def locate(self, rag: "HaikuRAG", result: "SearchResult") -> list[dict[str, Any]]:
        """A result's boxes, from its document's stored provenance."""
        if result.document_id is None:
            return []
        key = (result.source, result.document_id)
        located = self._documents.get(key)
        owner = await rag.reader_for(result.source)
        if owner is None:
            return []
        repository = owner.document_repository
        if located is None:
            stored = await repository.get_docling_data(result.document_id)
            document = (
                await asyncio.to_thread(stored.get_docling_document) if stored else None
            )
            located = _Located(document)
            self._documents[key] = located
            while len(self._documents) > 8:
                self._documents.popitem(last=False)
        self._documents.move_to_end(key)
        if located.document is None:
            return []
        missing = [n for n in result.page_numbers if n not in located.sizes]
        if missing:
            pages = await repository.get_pages_data(result.document_id)
            if pages is not None:
                items = await asyncio.to_thread(pages.get_page_images, missing)
                for number, page in items.items():
                    located.sizes[number] = (page.size.width, page.size.height)
            for number in missing:
                if number not in located.sizes and number in located.document.pages:
                    size = located.document.pages[number].size
                    located.sizes[number] = (size.width, size.height)
        return await asyncio.to_thread(
            item_locations, located.document, located.sizes, result.doc_item_refs
        )

