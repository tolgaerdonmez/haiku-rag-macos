import { describe, expect, it } from "vitest";
import { fileKind } from "../doctype";
import { findText, htmlFragmentUrl, planOpen, redirectPage } from "../openers/plan";
import { SearchHit } from "../types";

const ALL_APPS = { hasApp: () => true };
const NO_APPS = { hasApp: () => false };

function hit(over: Partial<SearchHit> = {}): SearchHit {
  return {
    id: "files:c1",
    documentId: "d1",
    database: "files",
    path: "/Users/me/Docs/Book.pdf",
    documentType: "pdf",
    pages: [40, 41],
    headings: ["Chapter 5", "Travis Picking"],
    labels: ["text"],
    snippet: "...Travis picking is a fingerstyle technique named after...",
    text: "Travis picking is a fingerstyle technique named after Merle Travis, who popularised it.",
    score: 0.5,
    boxes: [
      {
        page: 41,
        left: 72,
        top: 60,
        right: 540,
        bottom: 96,
        coordOrigin: "TOPLEFT",
        pageWidth: 612,
        pageHeight: 792,
      },
      {
        page: 40,
        left: 72,
        top: 412,
        right: 540,
        bottom: 498,
        coordOrigin: "TOPLEFT",
        pageWidth: 612,
        pageHeight: 792,
      },
    ],
    ...over,
  };
}

describe("fileKind", () => {
  it("decides by extension first", () => {
    expect(fileKind("/a/b.PDF")).toBe("pdf");
    expect(fileKind("/a/notes.md", "other")).toBe("text");
    expect(fileKind("/a/main.py")).toBe("text");
    expect(fileKind("/a/report.docx")).toBe("word");
    expect(fileKind("/a/deck.pptx")).toBe("powerpoint");
    expect(fileKind("/a/page.htm")).toBe("html");
    expect(fileKind("/a/book.epub")).toBe("epub");
  });

  it("uses the service's document type for files without a telling extension", () => {
    expect(fileKind("/a/README", "markdown")).toBe("text");
    expect(fileKind("/a/Makefile", "code")).toBe("text");
    expect(fileKind("/a/scan", "pdf")).toBe("pdf");
    expect(fileKind("/a/sheet.xlsx", "excel")).toBe("other");
    expect(fileKind("/a/photo.png", "image")).toBe("other");
  });
});

describe("planOpen", () => {
  it("opens a PDF in Skim at the first page with boxes, keeping that page's boxes", () => {
    const plan = planOpen(hit(), ALL_APPS, "travis picking");
    expect(plan.kind).toBe("skim");
    if (plan.kind !== "skim") return;
    expect(plan.page).toBe(40);
    expect(plan.boxes.map((b) => b.page)).toEqual([40]);
    expect(plan.preferBox).toBe(false);
    expect(plan.focus[0]).toBe("Travis picking is a fingerstyle technique named after");
  });

  it("outlines pictures and tables instead of selecting text", () => {
    const plan = planOpen(hit({ labels: ["caption", "picture"] }), ALL_APPS);
    expect(plan.kind === "skim" && plan.preferBox).toBe(true);
  });

  it("goes to the page when the PDF hit has no boxes", () => {
    const plan = planOpen(hit({ boxes: [], pages: [12] }), ALL_APPS);
    expect(plan).toMatchObject({ kind: "skim", page: 12, boxes: [] });
  });

  it("falls back to copying the passage when Skim is missing", () => {
    const plan = planOpen(hit(), NO_APPS);
    expect(plan).toMatchObject({ kind: "copy-and-open", passage: findText(hit().text) });
  });

  it("opens text, Markdown and code in the editor with the service's line", () => {
    const plan = planOpen(
      hit({ path: "/n/notes.md", documentType: "markdown", pages: [], boxes: [], line: 42 }),
      ALL_APPS,
    );
    expect(plan).toMatchObject({ kind: "editor", path: "/n/notes.md", line: 42 });
    const code = planOpen(hit({ path: "/n/run.sh", documentType: "code", pages: [], boxes: [] }), ALL_APPS);
    expect(code).toMatchObject({ kind: "editor", line: undefined });
  });

  it("opens Word at the page and PowerPoint at the slide when installed", () => {
    expect(planOpen(hit({ path: "/w/r.docx", documentType: "word", pages: [3] }), ALL_APPS)).toMatchObject({
      kind: "word",
      page: 3,
    });
    expect(
      planOpen(hit({ path: "/w/d.pptx", documentType: "powerpoint", pages: [7], boxes: [] }), ALL_APPS),
    ).toMatchObject({ kind: "powerpoint", slide: 7 });
  });

  it("copies the passage for Find when Office is not installed", () => {
    expect(planOpen(hit({ path: "/w/r.docx", documentType: "word", pages: [] }), NO_APPS)).toMatchObject({
      kind: "copy-and-open",
      path: "/w/r.docx",
    });
    expect(planOpen(hit({ path: "/w/d.pptx", documentType: "powerpoint" }), NO_APPS).kind).toBe("copy-and-open");
  });

  it("opens HTML at the section through a text fragment", () => {
    const plan = planOpen(hit({ path: "/h/a page.html", documentType: "html", pages: [], boxes: [] }), ALL_APPS);
    expect(plan.kind).toBe("html");
    if (plan.kind !== "html") return;
    expect(plan.url.startsWith("file:///h/a%20page.html#:~:text=Travis%20Picking&text=")).toBe(true);
  });

  it("opens EPUB at the section with the deepest heading", () => {
    expect(planOpen(hit({ path: "/b/x.epub", documentType: "epub", pages: [], boxes: [] }), ALL_APPS)).toMatchObject({
      kind: "epub",
      heading: "Travis Picking",
    });
  });

  it("copies and opens anything else", () => {
    expect(planOpen(hit({ path: "/x/sheet.xlsx", documentType: "excel" }), ALL_APPS).kind).toBe("copy-and-open");
  });
});

describe("findText and htmlFragmentUrl", () => {
  it("drops a leading ellipsis and markdown, keeps the opening words", () => {
    expect(findText("...**Travis** picking is a [fingerstyle](x) technique", 4)).toBe("Travis picking is a");
  });

  it("escapes the characters text fragments reserve", () => {
    const url = htmlFragmentUrl("/p/a#b.html", "Rock & roll, part-2", "x");
    expect(url).toBe("file:///p/a%23b.html#:~:text=Rock%20%26%20roll%2C%20part%2D2&text=x");
  });

  it("leaves the URL bare without text", () => {
    expect(htmlFragmentUrl("/p/a.html", undefined, "")).toBe("file:///p/a.html");
  });
});

describe("redirectPage", () => {
  it("replaces itself with the URL, which cannot close the script", () => {
    const page = redirectPage("file:///a.html#:~:text=</script><b>");
    expect(page).toContain('location.replace("file:///a.html#:~:text=\\u003c/script>\\u003cb>")');
    expect(page.match(/<\/script>/g)).toHaveLength(1);
  });
});
