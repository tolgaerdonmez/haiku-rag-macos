import { environment } from "@raycast/api";
import { execFile, spawn } from "child_process";
import { createHash } from "crypto";
import { mkdir } from "fs/promises";
import { join } from "path";
import { promisify } from "util";
import { fileKind } from "../doctype";
import { boxesForFirstPage } from "../geometry";
import { SearchHit } from "../types";
import { skimTarget } from "./skim";
import { PageBounds } from "../geometry";

const run = promisify(execFile);

/**
 * JXA over PDFKit. `bounds SRC PAGE` prints the page's crop box (left, top, right, bottom);
 * `extract SRC PAGE OUT RECTS` writes that one page, with the rectangles outlined, to OUT.
 * The source document is only read.
 */
export const PAGE_TOOL_JXA = `ObjC.import("PDFKit");
ObjC.import("AppKit");
function run(argv) {
  const [mode, src, pageArg, out, rectsArg] = argv;
  const doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(src));
  if (!doc || doc.isNil()) throw new Error("cannot read " + src);
  const index = Number(pageArg) - 1;
  if (index < 0 || index >= doc.pageCount) throw new Error("no page " + pageArg);
  const page = doc.pageAtIndex(index);
  const box = page.boundsForBox($.kPDFDisplayBoxCropBox);
  if (mode === "bounds") {
    const l = box.origin.x, b = box.origin.y;
    return [l, b + box.size.height, l + box.size.width, b].join(",");
  }
  const copy = page.copy;
  const rects = JSON.parse(rectsArg || "[]");
  for (const [l, t, r, b] of rects) {
    const a = $.PDFAnnotation.alloc.initWithBoundsForTypeWithProperties(
      $.NSMakeRect(l, b, r - l, t - b), $.PDFAnnotationSubtypeSquare, $());
    a.color = $.NSColor.colorWithSRGBRedGreenBlueAlpha(0.95, 0.3, 0.1, 0.9);
    a.interiorColor = $.NSColor.colorWithSRGBRedGreenBlueAlpha(1, 0.85, 0, 0.25);
    const border = $.PDFBorder.alloc.init;
    border.lineWidth = 2;
    a.border = border;
    copy.addAnnotation(a);
  }
  const one = $.PDFDocument.alloc.init;
  one.insertPageAtIndex(copy, 0);
  if (!one.writeToFile(out)) throw new Error("cannot write " + out);
  return out;
}`;

async function pageTool(args: string[]): Promise<string> {
  const { stdout } = await run("/usr/bin/osascript", ["-l", "JavaScript", "-e", PAGE_TOOL_JXA, ...args], {
    timeout: 20_000,
  });
  return stdout.trim();
}

/** A one-page PDF of the hit's page with the passage outlined, in the extension's support folder. */
export async function pagePreview(hit: SearchHit): Promise<string | undefined> {
  if (fileKind(hit.path, hit.documentType) !== "pdf") return undefined;
  const onPage = boxesForFirstPage(hit.boxes, hit.pages);
  const page = onPage?.page ?? hit.pages[0];
  if (!page) return undefined;
  const bounds = (await pageTool(["bounds", hit.path, String(page)])).split(",").map(Number) as PageBounds;
  const target = skimTarget(onPage?.boxes ?? [], bounds);
  const dir = join(environment.supportPath, "previews");
  await mkdir(dir, { recursive: true });
  const name = createHash("sha1").update(`${hit.path}\u0000${page}\u0000${hit.id}`).digest("hex").slice(0, 16);
  const out = join(dir, `${name}.pdf`);
  const rects = target.marquee ? [target.marquee] : [];
  await pageTool(["extract", hit.path, String(page), out, JSON.stringify(rects)]);
  return out;
}

/** Shows the hit's page in Quick Look; files without pages preview whole. */
export async function quickLook(hit: SearchHit): Promise<void> {
  const file = (await pagePreview(hit)) ?? hit.path;
  const child = spawn("/usr/bin/qlmanage", ["-p", file], { detached: true, stdio: "ignore" });
  child.unref();
}
