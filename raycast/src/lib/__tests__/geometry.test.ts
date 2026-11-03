import { describe, expect, it } from "vitest";
import { boxesForFirstPage, padRect, toPdfRect, unionRect } from "../geometry";
import { BoundingBox } from "../types";

const A4: [number, number, number, number] = [0, 842, 595, 0];

function box(over: Partial<BoundingBox> = {}): BoundingBox {
  return {
    page: 1,
    left: 72,
    top: 100,
    right: 400,
    bottom: 120,
    coordOrigin: "TOPLEFT",
    pageWidth: 595,
    pageHeight: 842,
    ...over,
  };
}

describe("toPdfRect", () => {
  it("flips a top-left box into PDF space", () => {
    expect(toPdfRect(box(), A4)).toEqual([72, 742, 400, 722]);
  });

  it("keeps a bottom-left box as it is", () => {
    expect(toPdfRect(box({ coordOrigin: "BOTTOMLEFT", top: 742, bottom: 722 }), A4)).toEqual([72, 742, 400, 722]);
  });

  it("follows the contract's formula y = height - bottom", () => {
    const [, top, , bottom] = toPdfRect(
      box({ top: 412.3, bottom: 498.7, pageWidth: 612, pageHeight: 792 }),
      [0, 792, 612, 0],
    );
    expect(bottom).toBeCloseTo(792 - 498.7);
    expect(top - bottom).toBeCloseTo(498.7 - 412.3);
  });

  it("offsets by a crop box that does not start at the origin", () => {
    expect(toPdfRect(box(), [10, 852, 605, 10])).toEqual([82, 752, 410, 732]);
  });

  it("scales a box measured on a page of another size, such as pixels", () => {
    const pixels = box({ left: 144, top: 200, right: 800, bottom: 240, pageWidth: 1190, pageHeight: 1684 });
    const [l, t, r, b] = toPdfRect(pixels, A4);
    expect(l).toBeCloseTo(72, 0);
    expect(t).toBeCloseTo(742, 0);
    expect(r).toBeCloseTo(400, 0);
    expect(b).toBeCloseTo(722, 0);
  });

  it("orders swapped edges", () => {
    expect(toPdfRect(box({ left: 400, right: 72, top: 120, bottom: 100 }), A4)).toEqual([72, 742, 400, 722]);
  });
});

describe("padRect and unionRect", () => {
  it("pads within the page", () => {
    expect(padRect([1, 841, 594, 1], 5, A4)).toEqual([0, 842, 595, 0]);
    expect(padRect([72, 742, 400, 722], 2, A4)).toEqual([70, 744, 402, 720]);
  });

  it("covers every rectangle", () => {
    expect(
      unionRect([
        [72, 742, 300, 722],
        [310, 600, 520, 560],
      ]),
    ).toEqual([72, 742, 520, 560]);
    expect(unionRect([])).toBeUndefined();
  });
});

describe("boxesForFirstPage", () => {
  it("takes the first listed page that has boxes", () => {
    const boxes = [box({ page: 41 }), box({ page: 40 }), box({ page: 40, top: 300, bottom: 320 })];
    const result = boxesForFirstPage(boxes, [40, 41]);
    expect(result?.page).toBe(40);
    expect(result?.boxes).toHaveLength(2);
  });

  it("falls back to the boxes' own page", () => {
    expect(boxesForFirstPage([box({ page: 7 })], [])?.page).toBe(7);
    expect(boxesForFirstPage([], [3])).toBeUndefined();
  });
});
