import { describe, expect, it, vi } from "vitest";
import { HttpSearchService, ServiceError, WireHit, searchUrl, toSearchHit, toSearchResult } from "../api";
import { askMessages, askRequestBody, capRemaining, costOf, currentLedger, monthKey, parseCap } from "../ask";
import { MockSearchService } from "../mock";

const wireHit: WireHit = {
  rank: 1,
  score: 0.0312,
  chunk_id: "4f0c",
  document_id: "8a1e",
  database: "files",
  folder_id: "guitar",
  title: "Country Guitar",
  path: "/Users/me/Documents/Guitar/Country Guitar.pdf",
  uri: "file:///Users/me/Documents/Guitar/Country%20Guitar.pdf",
  document_type: "pdf",
  content_type: "application/pdf",
  page_numbers: [40, 41],
  line: null,
  headings: ["Chapter 5", "Travis Picking"],
  labels: ["text", "list_item"],
  snippet: "...Travis picking is a fingerstyle technique named after...",
  snippet_highlights: [
    [3, 9],
    [10, 17],
  ],
  text: "Travis picking is a fingerstyle technique named after Merle Travis...",
  pages: [
    { page: 40, width: 612, height: 792, boxes: [{ left: 72, top: 412.3, right: 540, bottom: 498.7 }] },
    { page: 41, width: 612, height: 792, boxes: [{ left: 72, top: 60.1, right: 540, bottom: 96.4 }] },
  ],
  context: null,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("wire mapping", () => {
  it("maps the contract's hit, boxes top-left with their page size", () => {
    const hit = toSearchHit(wireHit)!;
    expect(hit.id).toBe("files:4f0c");
    expect(hit.pages).toEqual([40, 41]);
    expect(hit.line).toBeUndefined();
    expect(hit.highlights).toEqual([
      [3, 9],
      [10, 17],
    ]);
    expect(hit.boxes).toEqual([
      {
        page: 40,
        left: 72,
        top: 412.3,
        right: 540,
        bottom: 498.7,
        coordOrigin: "TOPLEFT",
        pageWidth: 612,
        pageHeight: 792,
      },
      {
        page: 41,
        left: 72,
        top: 60.1,
        right: 540,
        bottom: 96.4,
        coordOrigin: "TOPLEFT",
        pageWidth: 612,
        pageHeight: 792,
      },
    ]);
  });

  it("derives the path from a file URI and skips documents that are not local files", () => {
    expect(toSearchHit({ ...wireHit, path: null })?.path).toBe("/Users/me/Documents/Guitar/Country Guitar.pdf");
    const result = toSearchResult({ query: "q", hits: [wireHit, { ...wireHit, path: null, uri: "https://x/y" }] });
    expect(result.hits).toHaveLength(1);
    expect(result.skipped).toBe(1);
  });

  it("tolerates null headings and missing pages", () => {
    const hit = toSearchHit({ ...wireHit, headings: null, pages: [], line: 12, document_type: "markdown" })!;
    expect(hit.headings).toEqual([]);
    expect(hit.boxes).toEqual([]);
    expect(hit.line).toBe(12);
  });
});

describe("searchUrl", () => {
  it("builds the global and the per-document search", () => {
    expect(searchUrl("http://127.0.0.1:8765/", "travis picking", { limit: 99, context: true })).toBe(
      "http://127.0.0.1:8765/search?q=travis+picking&limit=50&context=true",
    );
    expect(searchUrl("http://h", "q", { documentId: "a/b", database: "files", folders: ["g", "t"] })).toBe(
      "http://h/documents/a%2Fb/search?q=q&database=files&folder=g&folder=t",
    );
  });
});

describe("HttpSearchService", () => {
  it("sends the token and maps the response", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ query: "q", took_ms: 3, hits: [wireHit] }));
    const service = new HttpSearchService("http://h", "secret", fetchMock as unknown as typeof fetch);
    const result = await service.search("q", { limit: 5 });
    expect(result.hits[0].documentId).toBe("8a1e");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://h/search?q=q&limit=5");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer secret");
  });

  it("surfaces the service's detail on errors", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ detail: "path already registered" }, 409));
    const service = new HttpSearchService("http://h", undefined, fetchMock as unknown as typeof fetch);
    await expect(service.addFolder({ path: "/x" })).rejects.toEqual(new ServiceError("path already registered", 409));
  });

  it("explains an unreachable service", async () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    const service = new HttpSearchService("http://127.0.0.1:1", undefined, fetchMock as unknown as typeof fetch);
    await expect(service.folders()).rejects.toThrow(/Cannot reach the search service at http:\/\/127.0.0.1:1/);
  });

  it("talks to the folder and failure endpoints", async () => {
    const calls: [string, string][] = [];
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      calls.push([init.method ?? "GET", url]);
      if (url.endsWith("/folders") && init.method === "POST") {
        expect(JSON.parse(init.body as string)).toEqual({ path: "/d", include: ["**/*.pdf"] });
        return jsonResponse({ id: "d", database: "files", path: "/d", include: ["**/*.pdf"], status: null }, 201);
      }
      if (url.includes("/failures/") || url.includes("/failures/retry")) return jsonResponse({ retried: ["j"] });
      if (url.endsWith("/failures")) {
        return jsonResponse({
          failures: [{ job_id: "j", reason: "timed out", attempts: 1, uri: "file:///a%20b.pdf" }],
        });
      }
      if (init.method === "DELETE") return jsonResponse({ removed: "d" });
      return jsonResponse({ folders: [] });
    });
    const service = new HttpSearchService("http://h", undefined, fetchMock as unknown as typeof fetch);
    expect((await service.addFolder({ path: "/d", id: "", include: ["**/*.pdf"] })).status).toBeUndefined();
    await service.removeFolder("d", true);
    expect((await service.failures())[0].path).toBe("/a b.pdf");
    await service.retry("j");
    await service.retry(undefined, "guitar");
    expect(calls).toEqual([
      ["POST", "http://h/folders"],
      ["DELETE", "http://h/folders/d?keep_documents=true"],
      ["GET", "http://h/failures"],
      ["POST", "http://h/failures/j/retry"],
      ["POST", "http://h/failures/retry?folder=guitar"],
    ]);
  });
});

describe("mock service", () => {
  it("serves the samples and narrows to a document", async () => {
    const mock = new MockSearchService("/samples");
    const all = await mock.search("quick fox");
    expect(all.hits[0].path).toBe("/samples/sample.pdf");
    expect(all.hits[0].highlights?.length).toBeGreaterThan(0);
    const one = await mock.search("anything", { documentId: "doc-md" });
    expect(one.hits.map((h) => h.documentId)).toEqual(["doc-md"]);
  });
});

describe("ask budget", () => {
  it("starts a new ledger each month", () => {
    const now = new Date("2026-11-03T12:00:00Z");
    expect(monthKey(now)).toBe("2026-11");
    expect(currentLedger({ month: "2026-10", spentUsd: 0.9 }, now)).toEqual({ month: "2026-11", spentUsd: 0 });
    expect(currentLedger({ month: "2026-11", spentUsd: 0.3 }, now).spentUsd).toBe(0.3);
  });

  it("parses the cap and computes what is left", () => {
    expect(parseCap("0,50")).toBe(0.5);
    expect(parseCap("nonsense")).toBe(1);
    expect(capRemaining({ month: "m", spentUsd: 1.2 }, 1)).toBe(0);
  });

  it("charges the reported cost, else a conservative estimate", () => {
    expect(costOf({ cost: 0.00012 })).toBe(0.00012);
    expect(costOf({ prompt_tokens: 1000, completion_tokens: 500 })).toBeCloseTo(0.003);
    expect(costOf(undefined)).toBeGreaterThan(0);
  });

  it("bounds the answer and asks for usage", () => {
    const body = askRequestBody("m", []);
    expect(body).toMatchObject({ model: "m", max_tokens: 700, usage: { include: true } });
  });

  it("grounds the question in the passage", () => {
    const [system, user] = askMessages(
      {
        id: "1",
        documentId: "d",
        path: "/a/b.pdf",
        documentType: "pdf",
        pages: [3],
        headings: ["H"],
        labels: [],
        snippet: "s",
        text: "the passage",
        score: 1,
        boxes: [],
      },
      "why?",
    );
    expect(system.content).toMatch(/only the passage/);
    expect(user.content).toContain("File: b.pdf\nLocation: p. 3\nSection: H\n\nthe passage");
    expect(user.content).toContain("Question: why?");
  });
});
