import { execFile } from "child_process";
import { existsSync } from "fs";
import { mkdir, mkdtemp, readFile, rename, rm, stat } from "fs/promises";
import { basename, join } from "path";
import { pathToFileURL } from "url";
import { promisify } from "util";
import { fileKind } from "../doctype";
import { SearchHit } from "../types";
import { cacheKey, cached, prune } from "./cache";
import { pageLayout } from "./layout";
import { textContextMarkdown } from "./text";

export type Exec = (
  file: string,
  args: string[],
  options: { timeout: number; signal?: AbortSignal },
) => Promise<{ stdout: string }>;

const execFileAsync = promisify(execFile);
export const defaultExec: Exec = async (file, args, options) => {
  const { stdout } = await execFileAsync(file, args, options);
  return { stdout: String(stdout) };
};

/**
 * JXA over PDFKit: `SRC PAGE SPEC` renders page PAGE of SRC once per output of
 * SPEC (`{outputs: [{path, crop, width}], rects}`, rectangles as fractions of
 * the page, origin top-left) to a PNG, with `rects` highlighted, and prints
 * `path,width,height` per output. The source document is only read.
 */
export const PAGE_RENDER_JXA = `ObjC.import("PDFKit");
ObjC.import("AppKit");
function run(argv) {
  const [src, pageArg, specArg] = argv;
  const spec = JSON.parse(specArg);
  const doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(src));
  if (!doc || doc.isNil()) throw new Error("cannot read " + src);
  const index = Number(pageArg) - 1;
  if (index < 0 || index >= doc.pageCount) throw new Error("no page " + pageArg);
  const page = doc.pageAtIndex(index);
  const box = page.boundsForBox($.kPDFDisplayBoxCropBox);
  const turned = page.rotation % 180 !== 0;
  const pw = turned ? box.size.height : box.size.width;
  const ph = turned ? box.size.width : box.size.height;
  const printed = [];
  for (const o of spec.outputs) {
    const [x0, y0, x1, y1] = o.crop;
    const scale = o.width / ((x1 - x0) * pw);
    const fw = Math.round(pw * scale), fh = Math.round(ph * scale);
    const w = Math.max(1, Math.round((x1 - x0) * fw)), h = Math.max(1, Math.round((y1 - y0) * fh));
    const image = page.thumbnailOfSizeForBox($.NSMakeSize(fw, fh), $.kPDFDisplayBoxCropBox);
    const rep = $.NSBitmapImageRep.alloc.initWithBitmapDataPlanesPixelsWidePixelsHighBitsPerSampleSamplesPerPixelHasAlphaIsPlanarColorSpaceNameBytesPerRowBitsPerPixel(
      null, w, h, 8, 4, true, false, $.NSDeviceRGBColorSpace, 0, 0);
    $.NSGraphicsContext.saveGraphicsState;
    $.NSGraphicsContext.setCurrentContext($.NSGraphicsContext.graphicsContextWithBitmapImageRep(rep));
    $.NSColor.whiteColor.setFill;
    $.NSRectFill($.NSMakeRect(0, 0, w, h));
    const ox = -x0 * fw, oy = -(1 - y1) * fh;
    image.drawInRectFromRectOperationFraction($.NSMakeRect(ox, oy, fw, fh), $.NSZeroRect, $.NSCompositingOperationSourceOver, 1);
    for (const [l, t, r, b] of spec.rects) {
      const rect = $.NSMakeRect(ox + l * fw, oy + (1 - b) * fh, (r - l) * fw, (b - t) * fh);
      $.NSColor.colorWithSRGBRedGreenBlueAlpha(1, 0.82, 0, 0.28).setFill;
      $.NSBezierPath.fillRect(rect);
      const edge = $.NSBezierPath.bezierPathWithRect(rect);
      edge.lineWidth = Math.max(1.5, w / 500);
      $.NSColor.colorWithSRGBRedGreenBlueAlpha(0.95, 0.45, 0, 0.9).setStroke;
      edge.stroke;
    }
    $.NSGraphicsContext.restoreGraphicsState;
    const png = rep.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $());
    if (!png.writeToFileAtomically(o.path, true)) throw new Error("cannot write " + o.path);
    printed.push([o.path, w, h].join(","));
  }
  return printed.join("\\n");
}`;

export interface RenderedImage {
  file: string;
  name: "zoom" | "page" | "thumbnail";
}

const MAX_TEXT_BYTES = 2 * 1024 * 1024;

/** The hit's page as PNGs, zoomed on the passage and whole, rendered once per file version. */
export async function renderPdfPage(
  hit: SearchHit,
  dir: string,
  exec: Exec = defaultExec,
  signal?: AbortSignal,
): Promise<RenderedImage[] | undefined> {
  const layout = pageLayout(hit);
  if (!layout) return undefined;
  const key = await cacheKey(hit.path, [layout]);
  const images = layout.outputs.map((o) => ({ file: join(dir, `${key}-${o.name}.png`), name: o.name }));
  const hits = await Promise.all(images.map((i) => cached(i.file)));
  if (hits.every(Boolean)) return images;
  await mkdir(dir, { recursive: true });
  const spec = {
    outputs: layout.outputs.map((o, i) => ({ path: images[i].file, crop: o.crop, width: o.width })),
    rects: layout.highlights,
  };
  await exec(
    "/usr/bin/osascript",
    ["-l", "JavaScript", "-e", PAGE_RENDER_JXA, hit.path, String(layout.page), JSON.stringify(spec)],
    { timeout: 15_000, signal },
  );
  await prune(dir).catch(() => 0);
  return images;
}

/** Quick Look's thumbnail of the file, for documents without rendered pages. */
export async function renderThumbnail(
  hit: SearchHit,
  dir: string,
  exec: Exec = defaultExec,
  signal?: AbortSignal,
): Promise<RenderedImage[] | undefined> {
  const file = join(dir, `${await cacheKey(hit.path, ["thumbnail"])}-thumbnail.png`);
  if (await cached(file)) return [{ file, name: "thumbnail" }];
  await mkdir(dir, { recursive: true });
  const scratch = await mkdtemp(join(dir, ".ql-"));
  try {
    await exec("/usr/bin/qlmanage", ["-t", "-s", "1000", "-o", scratch, hit.path], { timeout: 15_000, signal });
    const made = join(scratch, `${basename(hit.path)}.png`);
    if (!existsSync(made)) return undefined;
    await rename(made, file);
  } finally {
    await rm(scratch, { recursive: true, force: true });
  }
  await prune(dir).catch(() => 0);
  return [{ file, name: "thumbnail" }];
}

/** The passage in its file with the lines around it, for text and code. */
export async function renderTextContext(hit: SearchHit): Promise<string | undefined> {
  if ((await stat(hit.path)).size > MAX_TEXT_BYTES) return undefined;
  const content = await readFile(hit.path, "utf8");
  return textContextMarkdown(hit.path, content, hit.text || hit.snippet, hit.line);
}

export function imageMarkdown(image: RenderedImage, alt: string): string {
  const url = pathToFileURL(image.file).href;
  return image.name === "page" ? `![${alt}](${url}?raycast-height=380)` : `![${alt}](${url})`;
}

/**
 * The detail pane's preview of a hit: its PDF page with the passage
 * highlighted, its lines in a text file, or a Quick Look thumbnail. Undefined
 * when there is none, and the pane shows the passage's text.
 */
export async function renderPreview(
  hit: SearchHit,
  dir: string,
  exec: Exec = defaultExec,
  signal?: AbortSignal,
): Promise<{ markdown: string } | undefined> {
  const kind = fileKind(hit.path, hit.documentType);
  if (kind === "text") {
    const markdown = await renderTextContext(hit);
    return markdown ? { markdown } : undefined;
  }
  if (kind === "pdf") {
    const images = await renderPdfPage(hit, dir, exec, signal);
    if (!images) return undefined;
    const page = pageLayout(hit)?.page;
    return { markdown: images.map((i) => imageMarkdown(i, `Page ${page}`)).join("\n\n") };
  }
  const images = await renderThumbnail(hit, dir, exec, signal);
  return images ? { markdown: imageMarkdown(images[0], basename(hit.path)) } : undefined;
}
