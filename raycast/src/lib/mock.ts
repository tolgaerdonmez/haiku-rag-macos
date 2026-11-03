import { join } from "path";
import { NewFolder, SearchOptions, SearchService, WireHit, toSearchResult } from "./api";
import { queryTerms } from "./text";
import { Failure, IndexedFolder, SearchResult, ServiceStatus } from "./types";

/** Hits over the sample files in assets/samples, shaped as the service sends them. */
export function sampleHits(samplesDir: string): WireHit[] {
  const base = {
    database: "files",
    folder_id: "samples",
    uri: null,
    content_type: null,
    snippet_highlights: [],
    context: null,
  };
  return [
    {
      ...base,
      rank: 1,
      score: 0.92,
      chunk_id: "pdf-2-25",
      document_id: "doc-pdf",
      title: "Sample PDF",
      path: join(samplesDir, "sample.pdf"),
      document_type: "pdf",
      page_numbers: [2],
      line: null,
      headings: ["Chapter 2: Test heading 2"],
      labels: ["text"],
      snippet: "The quick brown fox jumps over the lazy dog near the riverbank.",
      text: "The quick brown fox jumps over the lazy dog near the riverbank.",
      pages: [{ page: 2, width: 595.28, height: 841.89, boxes: [{ left: 72, top: 497, right: 400, bottom: 514 }] }],
      context:
        "## Chapter 2: Test heading 2\n\nFiller line 24 on page 2 lorem ipsum dolor sit amet.\n\nThe quick brown fox jumps over the lazy dog near the riverbank.\n\nFiller line 26 on page 2 lorem ipsum dolor sit amet.",
    },
    {
      ...base,
      rank: 2,
      score: 0.61,
      chunk_id: "pdf-1-0",
      document_id: "doc-pdf",
      title: "Sample PDF",
      path: join(samplesDir, "sample.pdf"),
      document_type: "pdf",
      page_numbers: [1],
      line: null,
      headings: ["Chapter 1: Test heading 1"],
      labels: ["section_header"],
      snippet: "Chapter 1: Test heading 1",
      text: "Chapter 1: Test heading 1",
      pages: [{ page: 1, width: 595.28, height: 841.89, boxes: [{ left: 72, top: 54, right: 330, bottom: 76 }] }],
    },
    {
      ...base,
      rank: 3,
      score: 0.58,
      chunk_id: "md-43",
      document_id: "doc-md",
      title: "notes.md",
      path: join(samplesDir, "notes.md"),
      document_type: "markdown",
      page_numbers: [],
      line: null,
      headings: ["Notes", "Target section"],
      labels: ["text"],
      snippet: "The passage about modal interchange lives here.",
      text: "The passage about modal interchange lives here.",
      pages: [],
    },
    {
      ...base,
      rank: 4,
      score: 0.55,
      chunk_id: "html-modal",
      document_id: "doc-html",
      title: "Harmony notes",
      path: join(samplesDir, "page.html"),
      document_type: "html",
      page_numbers: [],
      line: null,
      headings: ["Harmony notes", "Modal interchange"],
      labels: ["text"],
      snippet: "Borrowed chords come from the parallel minor, giving the progression a darker colour.",
      text: "Borrowed chords come from the parallel minor, giving the progression a darker colour.",
      pages: [],
    },
    {
      ...base,
      rank: 5,
      score: 0.51,
      chunk_id: "epub-sweep",
      document_id: "doc-epub",
      title: "Test Book",
      path: join(samplesDir, "book.epub"),
      document_type: "epub",
      page_numbers: [],
      line: null,
      headings: ["Chapter Two: Arpeggios", "Sweep picking"],
      labels: ["text"],
      snippet: "Sweep picking moves the pick across adjacent strings in one motion.",
      text: "Sweep picking moves the pick across adjacent strings in one motion.",
      pages: [],
    },
  ];
}

function highlightsFor(snippet: string, query: string): [number, number][] {
  const terms = queryTerms(query);
  const out: [number, number][] = [];
  for (const m of snippet.matchAll(/[\p{L}\p{N}]+/gu)) {
    if (terms.some((t) => m[0].toLowerCase().startsWith(t))) out.push([m.index, m.index + m[0].length]);
  }
  return out;
}

/** An in-memory stand-in for the service, for development without one running. */
export class MockSearchService implements SearchService {
  private folderList: IndexedFolder[];

  constructor(private readonly samplesDir: string) {
    this.folderList = [
      {
        id: "samples",
        database: "files",
        path: samplesDir,
        include: [],
        exclude: [],
        status: { watching: true, documents: 4, queued: 0, failed: 1 },
      },
    ];
  }

  async search(query: string, options: SearchOptions = {}): Promise<SearchResult> {
    const terms = queryTerms(query);
    let hits = sampleHits(this.samplesDir).map((h) => ({ ...h, snippet_highlights: highlightsFor(h.snippet, query) }));
    if (options.documentId) hits = hits.filter((h) => h.document_id === options.documentId);
    const scored = hits
      .map((h) => {
        const haystack = `${h.text} ${(h.headings ?? []).join(" ")}`.toLowerCase();
        return { h, s: terms.filter((t) => haystack.includes(t)).length };
      })
      .sort((a, b) => b.s - a.s || a.h.rank - b.h.rank)
      .map(({ h }, i) => ({ ...h, rank: i + 1, context: options.context ? h.context : null }));
    return toSearchResult({ query, took_ms: 1, hits: scored.slice(0, options.limit ?? 10) });
  }

  async folders(): Promise<IndexedFolder[]> {
    return this.folderList;
  }

  async addFolder(folder: NewFolder): Promise<IndexedFolder> {
    const id = folder.id || folder.path.split("/").filter(Boolean).pop()!.toLowerCase();
    if (this.folderList.some((f) => f.id === id || f.path === folder.path)) throw new Error("already registered");
    const added: IndexedFolder = {
      id,
      database: folder.database || "files",
      path: folder.path,
      include: folder.include ?? [],
      exclude: folder.exclude ?? [],
    };
    this.folderList = [...this.folderList, added];
    return added;
  }

  async updateFolder(id: string, patch: { include?: string[]; exclude?: string[] }): Promise<IndexedFolder> {
    const folder = this.folderList.find((f) => f.id === id);
    if (!folder) throw new Error(`unknown folder ${id}`);
    Object.assign(folder, patch);
    return folder;
  }

  async removeFolder(id: string): Promise<void> {
    this.folderList = this.folderList.filter((f) => f.id !== id);
  }

  async status(): Promise<ServiceStatus> {
    return {
      indexing: false,
      queued: 0,
      inFlight: 0,
      failed: 1,
      paused: false,
      databases: [{ name: "files", documents: 4 }],
    };
  }

  async failures(): Promise<Failure[]> {
    return [
      {
        jobId: "mock-1",
        folderId: "samples",
        database: "files",
        path: join(this.samplesDir, "broken.pdf"),
        reason: "conversion timed out after 300s (ocrmac), retried with rapidocr: timed out after 300s",
        attempts: 1,
      },
    ];
  }

  async retry(jobId?: string): Promise<string[]> {
    return jobId ? [jobId] : ["mock-1"];
  }
}
