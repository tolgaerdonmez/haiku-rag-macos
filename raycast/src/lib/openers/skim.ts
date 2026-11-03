import { PageBounds, SkimRect, padRect, toPdfRect, unionRect } from "../geometry";
import { BoundingBox } from "../types";

export function appleScriptString(value: string): string {
  return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

function rectLiteral(rect: SkimRect): string {
  return `{${rect.map((v) => Math.round(v * 100) / 100).join(", ")}}`;
}

/** Opens the PDF (or brings its open window forward) and returns the page's crop box. */
export function pageBoundsScript(path: string, page: number): string {
  return `tell application "Skim"
  set d to open POSIX file ${appleScriptString(path)}
  set n to count of pages of d
  if ${page} > n then error "page ${page} is beyond the document's " & n & " pages"
  set b to bounds of page ${page} of d
  return (item 1 of b as text) & "," & (item 2 of b as text) & "," & (item 3 of b as text) & "," & (item 4 of b as text)
end tell`;
}

export function parseBounds(output: string): PageBounds {
  const parts = output.split(",").map((s) => Number(s.trim()));
  if (parts.length !== 4 || parts.some((n) => !Number.isFinite(n))) {
    throw new Error(`Skim returned unexpected page bounds: ${output}`);
  }
  return parts as PageBounds;
}

export interface SkimTarget {
  rects: SkimRect[];
  marquee: SkimRect | undefined;
}

/** The passage rectangles in Skim's space, and the marquee covering them. */
export function skimTarget(boxes: BoundingBox[], page: PageBounds): SkimTarget {
  const rects = boxes.map((b) => padRect(toPdfRect(b, page), 1, page));
  const union = unionRect(rects);
  return { rects, marquee: union && padRect(union, 2, page) };
}

/** Brings the document's tab to the front: opening an already open PDF leaves its tab where it was. */
function raiseTab(pathLiteral: string): string {
  return `  repeat with w in windows
    try
      if POSIX path of (file of document of w as alias) is ${pathLiteral} then
        set index of w to 1
        exit repeat
      end if
    end try
  end repeat`;
}

/**
 * Goes to the page and shows the passage without writing to the PDF: a text selection when the
 * boxes cover selectable text, otherwise a selection-tool marquee (scanned pages, pictures).
 * Within the boxes, the first `focus` phrase found on the page inside them is selected instead
 * of all the boxes' text: a box covers a whole document element, larger than the match.
 * Returns "focus", "text", "box" or "page".
 */
export function revealScript(
  path: string,
  page: number,
  target: SkimTarget,
  preferBox: boolean,
  focus: string[] = [],
): string {
  const pathLiteral = appleScriptString(path);
  if (!target.marquee) {
    return `tell application "Skim"
  activate
  set d to open POSIX file ${pathLiteral}
${raiseTab(pathLiteral)}
  go d to page ${page} of d
  return "page"
end tell`;
  }
  const [left, top] = target.marquee;
  const joins = target.rects
    .slice(1)
    .map((r) => `      set sel to join sel to (get selection for ${rectLiteral(r)} on p) with continuous selection`)
    .join("\n");
  return `tell application "Skim"
  activate
  set d to open POSIX file ${pathLiteral}
${raiseTab(pathLiteral)}
  set p to page ${page} of d
  set useBox to ${preferBox ? "true" : "false"}
  if not useBox then
    try
      set sel to get selection for ${rectLiteral(target.rects[0])} on p
${joins}
      set found to obtain text for sel
      if (count of words of found) is 0 then set useBox to true
    on error
      set useBox to true
    end try
  end if
  if useBox then
    go d to p at {${Math.round(left)}, ${Math.round(top + 36)}}
    set tool of d to select tool
    set selection page of d to p
    set selection bounds of d to ${rectLiteral(target.marquee)}
    return "box"
  end if
  set tool of d to text tool
${focusBlock(target.marquee, focus)}
  go d to sel
  set selection of d to sel
  return "text"
end tell`;
}

function focusBlock(marquee: SkimRect, focus: string[]): string {
  if (focus.length === 0) return "";
  const [, top, , bottom] = marquee;
  return `  try
    set pageText to (text of p) as text
    repeat with phrase in {${focus.map(appleScriptString).join(", ")}}
      set k to offset of (phrase as text) in pageText
      if k > 0 then
        set candidate to a reference to (characters k thru (k + (length of (phrase as text)) - 1) of text of p)
        set r to obtain rectangle for candidate
        if (item 2 of r) is less than or equal to ${Math.round(top)} and (item 4 of r) is greater than or equal to ${Math.round(bottom)} then
          go d to candidate
          set selection of d to candidate
          return "focus"
        end if
      end if
    end repeat
  end try`;
}
