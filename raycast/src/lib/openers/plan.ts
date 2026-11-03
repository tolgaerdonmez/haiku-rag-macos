import { boxesForFirstPage } from "../geometry";
import { fileKind } from "../doctype";
import { focusPhrases, stripMarkdown } from "../text";
import { BoundingBox, SearchHit } from "../types";

export type OpenPlan =
  | {
      kind: "skim";
      path: string;
      page: number;
      boxes: BoundingBox[];
      preferBox: boolean;
      passage: string;
      focus: string[];
    }
  | { kind: "editor"; path: string; line?: number; passage: string }
  | { kind: "word"; path: string; page?: number; passage: string }
  | { kind: "powerpoint"; path: string; slide?: number; passage: string }
  | { kind: "html"; path: string; url: string }
  | { kind: "epub"; path: string; heading?: string; passage: string }
  | { kind: "copy-and-open"; path: string; passage: string; app?: string; reason: string };

export interface OpenEnvironment {
  /** Applications installed, by name, e.g. "Skim", "Microsoft Word". */
  hasApp: (name: string) => boolean;
}

const BOX_LABELS = new Set(["picture", "chart", "table", "formula", "figure"]);

/** The passage's opening words as plain text, suitable for a Find field. */
export function findText(passage: string, maxWords = 12): string {
  const words = stripMarkdown(passage.replace(/^\s*(\.\.\.|…)/, ""))
    .split(" ")
    .filter(Boolean);
  return words.slice(0, maxWords).join(" ");
}

function passageOf(hit: SearchHit): string {
  return hit.text || hit.snippet;
}

function firstPage(hit: SearchHit): number | undefined {
  return hit.pages.length > 0 ? Math.min(...hit.pages) : hit.boxes[0]?.page;
}

function directiveText(text: string, maxWords: number): string {
  const words = stripMarkdown(text).split(" ").filter(Boolean).slice(0, maxWords);
  return encodeURIComponent(words.join(" ")).replace(/-/g, "%2D").replace(/,/g, "%2C").replace(/&/g, "%26");
}

/** A file URL that lands on the section and highlights the passage through text fragments. */
export function htmlFragmentUrl(path: string, heading: string | undefined, passage: string): string {
  const base = encodeURI(`file://${path}`).replace(/#/g, "%23").replace(/\?/g, "%3F");
  const directives = [heading, passage]
    .filter((t): t is string => !!t && stripMarkdown(t).length > 0)
    .map((t, i) => `text=${directiveText(t, i === 0 ? 10 : 6)}`);
  return directives.length > 0 ? `${base}#:~:${directives.join("&")}` : base;
}

/** A page that replaces itself with `url`, so the fragment survives being opened as a file. */
export function redirectPage(url: string): string {
  const literal = JSON.stringify(url).replace(/</g, "\\u003c");
  return `<!doctype html><meta charset="utf-8"><title>Opening…</title><script>location.replace(${literal});</script>\n`;
}

/** Decides how to open a hit at its exact spot; executing the plan is the caller's job. */
export function planOpen(hit: SearchHit, env: OpenEnvironment, query = ""): OpenPlan {
  const passage = findText(passageOf(hit));
  const kind = fileKind(hit.path, hit.documentType);
  switch (kind) {
    case "pdf": {
      const page = firstPage(hit);
      if (!env.hasApp("Skim") || page === undefined) {
        return { kind: "copy-and-open", path: hit.path, passage, reason: "Skim is not installed" };
      }
      const onPage = boxesForFirstPage(hit.boxes, hit.pages);
      return {
        kind: "skim",
        path: hit.path,
        page: onPage?.page ?? page,
        boxes: onPage?.boxes ?? [],
        preferBox: hit.labels.some((l) => BOX_LABELS.has(l.toLowerCase())),
        passage,
        focus: focusPhrases(hit.snippet, query, hit.highlights),
      };
    }
    case "text":
      return { kind: "editor", path: hit.path, line: hit.line, passage: passageOf(hit) };
    case "word":
      if (env.hasApp("Microsoft Word")) return { kind: "word", path: hit.path, page: firstPage(hit), passage };
      return { kind: "copy-and-open", path: hit.path, passage, reason: "Microsoft Word is not installed" };
    case "powerpoint":
      if (env.hasApp("Microsoft PowerPoint")) {
        return { kind: "powerpoint", path: hit.path, slide: firstPage(hit), passage };
      }
      return { kind: "copy-and-open", path: hit.path, passage, reason: "Microsoft PowerPoint is not installed" };
    case "html":
      return { kind: "html", path: hit.path, url: htmlFragmentUrl(hit.path, hit.headings.at(-1), passageOf(hit)) };
    case "epub":
      return { kind: "epub", path: hit.path, heading: hit.headings.at(-1), passage: passageOf(hit) };
    default:
      return { kind: "copy-and-open", path: hit.path, passage, reason: "no exact-spot opener for this file type" };
  }
}
