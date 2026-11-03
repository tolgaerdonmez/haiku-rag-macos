import { boxesForFirstPage } from "../geometry";
import { BoundingBox, SearchHit } from "../types";

/** A rectangle as fractions of the page: left, top, right, bottom, origin top-left. */
export type Fraction = [number, number, number, number];

export interface PageOutput {
  name: "zoom" | "page";
  crop: Fraction;
  /** Width of the rendered image in pixels. */
  width: number;
}

export interface PageLayout {
  page: number;
  highlights: Fraction[];
  outputs: PageOutput[];
}

export const ZOOM_WIDTH_PX = 1000;
export const PAGE_WIDTH_PX = 600;
const MARGIN_X = 0.04;
const MARGIN_Y = 0.025;
const MIN_ZOOM_WIDTH = 0.6;
/** The zoom's height for its width, in page points: what Raycast's detail pane shows unscrolled. */
const ZOOM_ASPECT = 0.75;
/** A zoom covering this much of the page makes the page itself enough. */
const WHOLE_PAGE_AREA = 0.8;

const clamp = (v: number) => Math.min(1, Math.max(0, v));

/** A box as fractions of its page, top-left origin whatever the box's own. */
export function toFraction(box: BoundingBox): Fraction {
  const y = (v: number) => (box.coordOrigin === "TOPLEFT" ? v : box.pageHeight - v);
  const [y1, y2] = [y(box.top), y(box.bottom)];
  return [
    clamp(Math.min(box.left, box.right) / box.pageWidth),
    clamp(Math.min(y1, y2) / box.pageHeight),
    clamp(Math.max(box.left, box.right) / box.pageWidth),
    clamp(Math.max(y1, y2) / box.pageHeight),
  ];
}

/** `[from, to]` grown to at least `size`, kept inside 0..1. */
function atLeast(from: number, to: number, size: number): [number, number] {
  if (to - from >= size) return [from, to];
  const centre = (from + to) / 2;
  let a = centre - size / 2;
  let b = centre + size / 2;
  if (a < 0) [a, b] = [0, size];
  if (b > 1) [a, b] = [1 - size, 1];
  return [clamp(a), clamp(b)];
}

/**
 * The region worth reading around the highlights: their column, padded, at
 * least `MIN_ZOOM_WIDTH` of the page wide and `ZOOM_ASPECT` as tall as wide.
 * Highlights taller than that are shown from the passage's start.
 */
export function zoomCrop(highlights: Fraction[], pageWidth: number, pageHeight: number): Fraction {
  const [l, t, r, b] = highlights.reduce((u, f) => [
    Math.min(u[0], f[0]),
    Math.min(u[1], f[1]),
    Math.max(u[2], f[2]),
    Math.max(u[3], f[3]),
  ]);
  const [x0, x1] = atLeast(clamp(l - MARGIN_X), clamp(r + MARGIN_X), MIN_ZOOM_WIDTH);
  const height = Math.min(1, ((x1 - x0) * pageWidth * ZOOM_ASPECT) / pageHeight);
  const top = clamp(t - MARGIN_Y);
  const bottom = clamp(b + MARGIN_Y);
  if (bottom - top > height) return [x0, top, x1, top + height];
  const [y0, y1] = atLeast(top, bottom, height);
  return [x0, y0, x1, y1];
}

/** What to render for a PDF hit: its first page with boxes, zoomed on them, and the page. */
export function pageLayout(hit: SearchHit): PageLayout | undefined {
  const onPage = boxesForFirstPage(hit.boxes, hit.pages);
  const page = onPage?.page ?? hit.pages[0];
  if (!page) return undefined;
  const boxes = onPage?.boxes ?? [];
  const highlights = boxes.map(toFraction).filter(([l, t, r, b]) => r > l && b > t);
  const whole: PageOutput = { name: "page", crop: [0, 0, 1, 1], width: PAGE_WIDTH_PX };
  if (highlights.length === 0) return { page, highlights, outputs: [{ ...whole, width: ZOOM_WIDTH_PX }] };
  const crop = zoomCrop(highlights, boxes[0].pageWidth, boxes[0].pageHeight);
  const area = (crop[2] - crop[0]) * (crop[3] - crop[1]);
  if (area >= WHOLE_PAGE_AREA) return { page, highlights, outputs: [{ ...whole, width: ZOOM_WIDTH_PX }] };
  return { page, highlights, outputs: [{ name: "zoom", crop, width: ZOOM_WIDTH_PX }, whole] };
}
