import { basename, extname } from "path";
import { fileKind } from "./doctype";
import { DocumentGroup, SearchHit } from "./types";

export function pageLabel(hit: SearchHit): string | undefined {
  if (hit.pages.length === 0) return hit.line ? `line ${hit.line}` : undefined;
  const unit = fileKind(hit.path, hit.documentType) === "powerpoint" ? "slide" : "p.";
  const pages = [...new Set(hit.pages)].sort((a, b) => a - b);
  const first = pages[0];
  const last = pages[pages.length - 1];
  const plural = unit === "p." ? "pp." : "slides";
  return first === last ? `${unit} ${first}` : `${plural} ${first}–${last}`;
}

/** Where the passage is, in words: "Page 55", "Pages 136–138", "Slide 4", "Line 42". */
export function locationLabel(hit: SearchHit): string | undefined {
  if (hit.pages.length === 0) return hit.line ? `Line ${hit.line}` : undefined;
  const slides = fileKind(hit.path, hit.documentType) === "powerpoint";
  const pages = [...new Set(hit.pages)].sort((a, b) => a - b);
  const [first, last] = [pages[0], pages[pages.length - 1]];
  if (first === last) return `${slides ? "Slide" : "Page"} ${first}`;
  return `${slides ? "Slides" : "Pages"} ${first}–${last}`;
}

/**
 * The document's title, or its file name without the extension, as a heading
 * shows it: underscores as spaces, no empty brackets, no markdown characters.
 */
export function documentName(hit: SearchHit): string {
  const file = basename(hit.path);
  const name = hit.title && hit.title !== file ? hit.title : basename(file, extname(file)) || file;
  return (
    name
      .replace(/[_*`\\]+/g, " ")
      .replace(/\[\s*\]|\(\s*\)/g, " ")
      .replace(/\s+/g, " ")
      .trim() || file
  );
}

export function headingTrail(hit: SearchHit): string {
  return hit.headings.join(" › ");
}

/** `file, page, "heading trail"` on one line, then the full path. */
export function formatCitation(hit: SearchHit): string {
  const parts = [
    hit.title && hit.title !== basename(hit.path) ? `${hit.title} (${basename(hit.path)})` : basename(hit.path),
  ];
  const page = pageLabel(hit);
  if (page) parts.push(page);
  const trail = headingTrail(hit);
  if (trail) parts.push(`“${trail}”`);
  return `${parts.join(", ")}\n${hit.path}`;
}

export const PASSAGES_PER_FILE = 3;

/** A document's group cut to its best `perFile` passages, and how many more it has. */
export interface ShownGroup {
  group: DocumentGroup;
  shown: SearchHit[];
  more: number;
}

export function topPassages(groups: DocumentGroup[], perFile = PASSAGES_PER_FILE): ShownGroup[] {
  return groups.map((group) => ({
    group,
    shown: group.hits.slice(0, perFile),
    more: Math.max(0, group.hits.length - perFile),
  }));
}

/** Groups hits by document, keeping the service's ranking: a group sits where its best hit ranked. */
export function groupByDocument(hits: SearchHit[]): DocumentGroup[] {
  const groups = new Map<string, DocumentGroup>();
  for (const hit of hits) {
    const key = `${hit.database ?? ""}\u0000${hit.documentId || hit.path}`;
    let group = groups.get(key);
    if (!group) {
      group = {
        key,
        path: hit.path,
        title: hit.title || basename(hit.path),
        documentType: hit.documentType,
        hits: [],
      };
      groups.set(key, group);
    }
    group.hits.push(hit);
  }
  return [...groups.values()];
}
