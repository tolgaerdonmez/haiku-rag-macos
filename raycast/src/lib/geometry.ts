import { BoundingBox } from "./types";

/** Skim's rectangle order: left, top, right, bottom, in PDF space (origin bottom-left, y up). */
export type SkimRect = [number, number, number, number];

/** A page's crop box as Skim reports it: left, top, right, bottom. */
export type PageBounds = SkimRect;

/**
 * Maps a box given against a page of `pageWidth` x `pageHeight` onto the page's crop box in PDF space.
 * A TOPLEFT box has y growing downward from the top edge; a BOTTOMLEFT box has y growing upward.
 */
export function toPdfRect(box: BoundingBox, page: PageBounds): SkimRect {
  const [cropLeft, cropTop, cropRight, cropBottom] = page;
  const sx = (cropRight - cropLeft) / box.pageWidth;
  const sy = (cropTop - cropBottom) / box.pageHeight;
  const yUp = (y: number) => (box.coordOrigin === "TOPLEFT" ? box.pageHeight - y : y);
  const y1 = yUp(box.top);
  const y2 = yUp(box.bottom);
  const x1 = Math.min(box.left, box.right);
  const x2 = Math.max(box.left, box.right);
  return [
    cropLeft + x1 * sx,
    cropBottom + Math.max(y1, y2) * sy,
    cropLeft + x2 * sx,
    cropBottom + Math.min(y1, y2) * sy,
  ];
}

/** Grows a rectangle by `margin` points on every side, clamped to the page. */
export function padRect(rect: SkimRect, margin: number, page: PageBounds): SkimRect {
  const [l, t, r, b] = rect;
  const [pl, pt, pr, pb] = page;
  return [Math.max(pl, l - margin), Math.min(pt, t + margin), Math.min(pr, r + margin), Math.max(pb, b - margin)];
}

/** The smallest rectangle covering every given rectangle. */
export function unionRect(rects: SkimRect[]): SkimRect | undefined {
  if (rects.length === 0) return undefined;
  return rects.reduce((acc, [l, t, r, b]) => [
    Math.min(acc[0], l),
    Math.max(acc[1], t),
    Math.max(acc[2], r),
    Math.min(acc[3], b),
  ]);
}

/** The boxes on the passage's first page that has any, grouped for one Skim selection. */
export function boxesForFirstPage(
  boxes: BoundingBox[],
  pages: number[],
): { page: number; boxes: BoundingBox[] } | undefined {
  const page = pages.find((p) => boxes.some((b) => b.page === p)) ?? boxes[0]?.page;
  if (page === undefined) return undefined;
  return { page, boxes: boxes.filter((b) => b.page === page) };
}
