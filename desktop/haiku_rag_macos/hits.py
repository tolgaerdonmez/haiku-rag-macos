"""Search hits in the shape the Raycast extension reads (docs/search-api.md):
document type, local path, snippet with highlights, line, and each page's
boxes."""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import unquote, urlparse

from pydantic import BaseModel

if TYPE_CHECKING:
    from docling_core.types.doc.document import DoclingDocument

DocumentType = Literal[
    "pdf",
    "word",
    "powerpoint",
    "excel",
    "epub",
    "html",
    "markdown",
    "text",
    "code",
    "image",
    "other",
]

_TYPES_BY_EXTENSION: dict[str, DocumentType] = {
    ".pdf": "pdf",
    ".docx": "word",
    ".doc": "word",
    ".odt": "word",
    ".pptx": "powerpoint",
    ".xlsx": "excel",
    ".csv": "excel",
    ".epub": "epub",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".md": "markdown",
    ".mdx": "markdown",
    ".qmd": "markdown",
    ".rmd": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".rst": "text",
    ".adoc": "text",
    ".asciidoc": "text",
    ".asc": "text",
    ".tex": "text",
    ".latex": "text",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tiff": "image",
    ".bmp": "image",
    ".webp": "image",
}

# The extensions haiku.rag reads as text (docs/ingester.md), less those above.
_CODE_EXTENSIONS = frozenset(
    ".astro .bash .c .clj .cljs .cpp .cs .css .dart .elm .ex .exs .fs .fsx .go "
    ".gql .graphql .groovy .h .hcl .hpp .hs .java .jl .js .json .kt .less .lua "
    ".mjs .ml .mli .nim .nix .php .pl .plantuml .pm .proto .pu .puml .ps1 .py .r "
    ".rb .rs .sass .scala .scss .sh .sql .svelte .swift .tf .toml .ts .tsx .vue "
    ".yaml .yml .zig".split()
)

# Types whose hits carry a line number: the file is the text that was indexed.
_LINE_TYPES = {"markdown", "text", "code"}
_MAX_LINE_FILE_BYTES = 4 * 1024 * 1024
_WORD = re.compile(r"\w{2,}")
_STOPWORDS = frozenset(
    "a an and are as at be by for from in into is it of on or the to with".split()
)

LOCATIONS_KEY = "locations"
"""Chunk metadata key under which chunks indexed by the earlier haiku.rag fork
recorded their boxes. Read when present; resolved from provenance otherwise."""


class Box(BaseModel):
    left: float
    top: float
    right: float
    bottom: float


class PageLocation(BaseModel):
    page: int
    width: float
    height: float
    boxes: list[Box]


class Hit(BaseModel):
    rank: int
    score: float
    chunk_id: str | None
    document_id: str | None
    database: str | None
    folder_id: str | None
    title: str | None
    path: str | None
    uri: str | None
    document_type: DocumentType
    content_type: str | None
    page_numbers: list[int]
    line: int | None
    headings: list[str] | None
    labels: list[str]
    snippet: str
    snippet_highlights: list[tuple[int, int]]
    text: str
    pages: list[PageLocation]
    context: str | None = None


class SearchResponse(BaseModel):
    query: str
    took_ms: float
    hits: list[Hit]


def document_type(uri: str | None, content_type: str | None) -> DocumentType:
    """What kind of file a document is, from its URI's extension."""
    suffix = Path(uri.split("#", 1)[0]).suffix.lower() if uri else ""
    if suffix in _TYPES_BY_EXTENSION:
        return _TYPES_BY_EXTENSION[suffix]
    if suffix in _CODE_EXTENSIONS:
        return "code"
    if content_type == "application/pdf":
        return "pdf"
    return "other"


def local_path(uri: str | None) -> Path | None:
    """The local file a `file://` URI names; None for anything else, a PDF
    attachment's `#attachment=` URI included."""
    if uri is None or "#" in uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return None
    return Path(unquote(parsed.path))


def path_from_uri(uri: str | None) -> str | None:
    path = local_path(uri)
    return str(path) if path is not None else None


def query_terms(query: str) -> list[str]:
    """The query's words worth highlighting, lowercased, first seen first."""
    terms: list[str] = []
    for word in _WORD.findall(query.lower()):
        if word not in _STOPWORDS and word not in terms:
            terms.append(word)
    return terms


def snippet(text: str, terms: list[str], width: int) -> tuple[str, list[tuple[int, int]]]:
    """At most `width` characters of `text` around the first term found, and
    where each term occurs in them."""
    flat = " ".join(text.split())
    lowered = flat.lower()
    found = [i for term in terms if (i := lowered.find(term)) >= 0]
    start = 0
    if len(flat) > width and found:
        start = max(0, min(found) - width // 4)
        start = min(start, len(flat) - width)
        # Back to the start of the word it cuts.
        start = flat.rfind(" ", 0, start) + 1
    window = flat[start : start + width]
    if start + width < len(flat) and (space := window.rfind(" ")) > width // 2:
        window = window[:space]
    prefix = "..." if start > 0 else ""
    suffix = "..." if start + len(window) < len(flat) else ""
    shown = f"{prefix}{window}{suffix}"
    highlights = []
    if terms:
        pattern = re.compile(
            r"\b(" + "|".join(re.escape(t) for t in terms) + r")", re.IGNORECASE
        )
        highlights = [(m.start(), m.end()) for m in pattern.finditer(shown)]
    return shown, highlights


def find_line(path: Path, text: str) -> int | None:
    """The 1-based line of `path` where `text` starts, by its first line long
    enough to place it."""
    try:
        if path.stat().st_size > _MAX_LINE_FILE_BYTES:
            return None
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for candidate in text.splitlines():
        probe = candidate.strip().lstrip("#>-*`|0123456789. ").strip()
        if len(probe) < 4:
            continue
        # Shorter probes too: the indexed text may have joined the file's
        # lines, so its first line can run past the file's.
        for prefix in dict.fromkeys(probe[:length] for length in (80, 40, 20)):
            for number, line in enumerate(lines, 1):
                if prefix in line:
                    return number
    return None


def item_locations(
    document: "DoclingDocument", sizes: dict[int, tuple[float, float]], refs: list[str]
) -> list[dict[str, Any]]:
    """Where the items `refs` sit, page by page in page order, the way
    haiku.rag's `visualize_chunk` finds them: each item's provenance boxes.

    `sizes` gives each page's width and height. Each page is
    `{"page", "width", "height", "boxes"}`, every box `[left, top, right,
    bottom]` in the page's units with the origin at its top-left corner. Items
    without provenance, refs that do not resolve and pages of unknown size are
    left out.
    """
    from docling_core.types.doc.document import RefItem

    pages: dict[int, dict[str, Any]] = {}
    for ref in refs:
        try:
            item = RefItem.model_validate({"$ref": ref}).resolve(document)
        except (AttributeError, IndexError, KeyError, ValueError):
            continue
        for prov in getattr(item, "prov", None) or []:
            size = sizes.get(prov.page_no)
            if size is None:
                continue
            width, height = size
            box = prov.bbox.to_top_left_origin(height)
            entry = pages.setdefault(
                prov.page_no,
                {
                    "page": prov.page_no,
                    "width": round(width, 2),
                    "height": round(height, 2),
                    "boxes": [],
                },
            )
            entry["boxes"].append(
                [round(box.l, 2), round(box.t, 2), round(box.r, 2), round(box.b, 2)]
            )
    return [pages[number] for number in sorted(pages)]


def page_location(location: dict[str, Any]) -> PageLocation:
    return PageLocation(
        page=location["page"],
        width=location["width"],
        height=location["height"],
        boxes=[
            Box(left=left, top=top, right=right, bottom=bottom)
            for left, top, right, bottom in location["boxes"]
        ],
    )
