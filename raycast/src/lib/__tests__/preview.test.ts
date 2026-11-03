import { execFileSync } from "child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "fs";
import { mkdir, readdir, stat, utimes, writeFile } from "fs/promises";
import { tmpdir } from "os";
import { basename, join, resolve } from "path";
import { describe, expect, it } from "vitest";
import { cacheKey, prune } from "../preview/cache";
import { pageLayout, toFraction, zoomCrop, ZOOM_WIDTH_PX } from "../preview/layout";
import { Exec, renderPdfPage, renderPreview } from "../preview/render";
import { languageOf, passageLines, textContextMarkdown } from "../preview/text";
import { BoundingBox, SearchHit } from "../types";

const SAMPLES = resolve(__dirname, "../../../assets/samples");

function scratch(): string {
  return mkdtempSync(join(tmpdir(), "haiku-preview-"));
}

function box(over: Partial<BoundingBox> = {}): BoundingBox {
  return {
    page: 2,
    left: 72,
    top: 497,
    right: 400,
    bottom: 514,
    coordOrigin: "TOPLEFT",
    pageWidth: 595.28,
    pageHeight: 841.89,
    ...over,
  };
}

function hit(over: Partial<SearchHit> = {}): SearchHit {
  return {
    id: "files:c1",
    documentId: "d1",
    database: "files",
    path: join(SAMPLES, "sample.pdf"),
    documentType: "pdf",
    pages: [2],
    headings: [],
    labels: [],
    snippet: "The quick brown fox",
    text: "The quick brown fox jumps over the lazy dog near the riverbank.",
    score: 1,
    boxes: [box()],
    ...over,
  };
}

/** An osascript or qlmanage double that writes the files it was asked for. */
function fakeExec(calls: string[][]): Exec {
  return async (file, args) => {
    calls.push([file, ...args]);
    if (file.endsWith("osascript")) {
      const spec = JSON.parse(args.at(-1)!) as { outputs: { path: string }[] };
      for (const o of spec.outputs) writeFileSync(o.path, "png");
    } else {
      const out = args[args.indexOf("-o") + 1];
      writeFileSync(join(out, `${basename(args.at(-1)!)}.png`), "png");
    }
    return { stdout: "" };
  };
}

describe("toFraction", () => {
  it("places a top-left box as fractions of its page", () => {
    const [l, t, r, b] = toFraction(box({ left: 0, top: 0, right: 297.64, bottom: 420.945 }));
    expect([l, t, r, b]).toEqual([0, 0, 0.5, 0.5]);
  });

  it("flips a bottom-left box", () => {
    const f = toFraction(box({ coordOrigin: "BOTTOMLEFT", top: 841.89, bottom: 420.945, left: 0, right: 595.28 }));
    expect(f).toEqual([0, 0, 1, 0.5]);
  });

  it("clamps a box that overflows its page", () => {
    expect(toFraction(box({ left: -10, right: 700 }))[0]).toBe(0);
    expect(toFraction(box({ left: -10, right: 700 }))[2]).toBe(1);
  });
});

describe("zoomCrop", () => {
  it("grows one line to a readable region around it", () => {
    const [x0, y0, x1, y1] = zoomCrop([[0.4, 0.5, 0.5, 0.52]], 600, 800);
    expect(x1 - x0).toBeCloseTo(0.6);
    expect(((y1 - y0) * 800) / ((x1 - x0) * 600)).toBeCloseTo(0.75);
    expect(y0).toBeLessThan(0.5);
    expect(y1).toBeGreaterThan(0.52);
  });

  it("stays on the page at its edges", () => {
    const [x0, y0, x1, y1] = zoomCrop([[0.9, 0.97, 0.99, 0.99]], 600, 800);
    expect(x1).toBe(1);
    expect(y1).toBe(1);
    expect(x0).toBeCloseTo(0.4);
    expect(y0).toBeGreaterThan(0);
  });

  it("shows a tall passage from its start", () => {
    const [x0, y0, x1, y1] = zoomCrop([[0.1, 0.1, 0.9, 0.9]], 600, 800);
    expect([x0, x1]).toEqual([expect.closeTo(0.06), expect.closeTo(0.94)]);
    expect(y0).toBeCloseTo(0.075);
    expect(y1 - y0).toBeCloseTo((0.88 * 600 * 0.75) / 800);
  });
});

describe("pageLayout", () => {
  it("zooms on the passage and shows its page", () => {
    const layout = pageLayout(hit())!;
    expect(layout.page).toBe(2);
    expect(layout.highlights).toHaveLength(1);
    expect(layout.outputs.map((o) => o.name)).toEqual(["zoom", "page"]);
  });

  it("uses the first page that has boxes", () => {
    const layout = pageLayout(hit({ pages: [1, 2], boxes: [box({ page: 2 })] }))!;
    expect(layout.page).toBe(2);
  });

  it("shows the whole page when the zoom would cover most of it", () => {
    const slide = box({ left: 40, top: 40, right: 680, bottom: 300, pageWidth: 720, pageHeight: 405 });
    const layout = pageLayout(hit({ boxes: [slide] }))!;
    expect(layout.outputs).toEqual([{ name: "page", crop: [0, 0, 1, 1], width: ZOOM_WIDTH_PX }]);
  });

  it("shows the page without highlights when the passage has no boxes", () => {
    const layout = pageLayout(hit({ boxes: [] }))!;
    expect(layout.highlights).toEqual([]);
    expect(layout.outputs).toHaveLength(1);
  });

  it("has nothing to render without a page", () => {
    expect(pageLayout(hit({ pages: [], boxes: [] }))).toBeUndefined();
  });
});

describe("text context", () => {
  const content = Array.from({ length: 20 }, (_, i) => `line ${i + 1} words here`).join("\n");

  it("marks the passage's lines and shows a few around them", () => {
    const md = textContextMarkdown(
      "/x/notes.md",
      content,
      "line 8 words here\nline 9 words here\nline 10 words here",
      8,
    )!;
    expect(md).toContain("```markdown");
    expect(md).toContain(" 5   line 5 words here");
    expect(md).toContain(" 8 ▌ line 8 words here");
    expect(md).toContain("10 ▌ line 10 words here");
    expect(md).toContain("13   line 13 words here");
    expect(md).not.toContain("line 14 words");
  });

  it("finds the passage when the service gave no line", () => {
    expect(passageLines(content, "line 4 words here", undefined)).toEqual({ start: 4, end: 4 });
  });

  it("has nothing for a passage not in the file", () => {
    expect(textContextMarkdown("/x/a.py", content, "absent entirely", undefined)).toBeUndefined();
  });

  it("fences past backticks in the file", () => {
    const md = textContextMarkdown("/x/a.md", "```py\ncode\n```", "code", 2)!;
    expect(md).toContain("````markdown");
  });

  it("names the language by extension", () => {
    expect(languageOf("/a/b.py")).toBe("python");
    expect(languageOf("/a/b.go")).toBe("go");
  });
});

describe("preview cache", () => {
  it("keys on the file's version", async () => {
    const dir = scratch();
    const file = join(dir, "a.pdf");
    await writeFile(file, "one");
    const first = await cacheKey(file, [1]);
    expect(await cacheKey(file, [1])).toBe(first);
    expect(await cacheKey(file, [2])).not.toBe(first);
    await utimes(file, new Date(), new Date(Date.now() + 5000));
    expect(await cacheKey(file, [1])).not.toBe(first);
  });

  it("removes the least recently used files past its cap", async () => {
    const dir = scratch();
    for (const [name, age] of [
      ["old", 300],
      ["mid", 200],
      ["new", 100],
    ] as const) {
      await writeFile(join(dir, name), "x".repeat(10));
      const when = new Date(Date.now() - age * 1000);
      await utimes(join(dir, name), when, when);
    }
    expect(await prune(dir, 25)).toBe(1);
    expect((await readdir(dir)).sort()).toEqual(["mid", "new"]);
    expect(await prune(join(dir, "missing"))).toBe(0);
  });
});

describe("renderPreview", () => {
  it("renders a PDF page once per file version", async () => {
    const dir = scratch();
    const calls: string[][] = [];
    const first = await renderPdfPage(hit(), dir, fakeExec(calls));
    const second = await renderPdfPage(hit(), dir, fakeExec(calls));
    expect(calls).toHaveLength(1);
    expect(second).toEqual(first);
    expect(calls[0].slice(-2, -1)).toEqual(["2"]);
  });

  it("shows a PDF hit as its zoomed and whole page", async () => {
    const preview = (await renderPreview(hit(), join(scratch(), "with space"), fakeExec([])))!;
    const images = [...preview.markdown.matchAll(/!\[Page 2\]\((file:\/\/[^)]+)\)/g)];
    expect(images).toHaveLength(2);
    expect(images[0][1]).toContain("with%20space");
    expect(images[1][1]).toMatch(/-page\.png\?raycast-height=\d+$/);
  });

  it("shows a text hit as its lines in the file", async () => {
    const md = await renderPreview(
      hit({ path: join(SAMPLES, "notes.md"), documentType: "markdown", text: "# Notes", line: 1, boxes: [] }),
      scratch(),
      fakeExec([]),
    );
    expect(md?.markdown).toContain("```markdown");
  });

  it("shows other files as a Quick Look thumbnail", async () => {
    const calls: string[][] = [];
    const preview = await renderPreview(
      hit({ path: join(SAMPLES, "page.html"), documentType: "html", boxes: [], pages: [] }),
      scratch(),
      fakeExec(calls),
    );
    expect(calls[0][0]).toBe("/usr/bin/qlmanage");
    expect(preview).toEqual({ markdown: expect.stringMatching(/-thumbnail\.png\)$/) });
  });

  it("has no preview when Quick Look makes no thumbnail", async () => {
    const none: Exec = async () => ({ stdout: "" });
    const dir = scratch();
    expect(await renderPreview(hit({ path: join(SAMPLES, "book.epub"), documentType: "epub" }), dir, none)).toBe(
      undefined,
    );
    expect((await readdir(dir)).filter((n) => n.startsWith(".ql-"))).toEqual([]);
  });

  it.skipIf(process.platform !== "darwin")("renders a real page with PDFKit", async () => {
    const dir = scratch();
    await mkdir(dir, { recursive: true });
    const images = (await renderPdfPage(hit(), dir))!;
    for (const image of images) {
      const png = readFileSync(image.file);
      expect(png.subarray(1, 4).toString()).toBe("PNG");
      expect((await stat(image.file)).size).toBeGreaterThan(1000);
    }
    const width = execFileSync("/usr/bin/sips", ["-g", "pixelWidth", images[0].file]).toString();
    expect(width).toContain(`pixelWidth: ${ZOOM_WIDTH_PX}`);
  });
});
