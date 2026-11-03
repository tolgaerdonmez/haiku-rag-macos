import { stripMarkdown } from "../text";

export interface SectionFile {
  path: string;
  html: string;
}

function plain(html: string): string {
  return html
    .replace(/<(script|style)[^>]*>[\s\S]*?<\/\1>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;|&#160;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ");
}

function phrase(text: string | undefined, words: number): string | undefined {
  if (!text) return undefined;
  const w = stripMarkdown(text)
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .split(" ")
    .filter(Boolean)
    .slice(0, words);
  return w.length > 0 ? ` ${w.join(" ")} ` : undefined;
}

/** The content file holding the passage, else the one holding the heading. */
export function pickSectionFile(
  files: SectionFile[],
  heading: string | undefined,
  passage: string,
): string | undefined {
  const texts = files.map((f) => ({ path: f.path, text: ` ${plain(f.html)} ` }));
  for (const words of [8, 5, 3]) {
    const needle = phrase(passage, words);
    const hit = needle && texts.find((t) => t.text.includes(needle));
    if (hit) return hit.path;
  }
  const head = phrase(heading, 12);
  return (head && texts.find((t) => t.text.includes(head))?.path) || undefined;
}

/** Spine order from an OPF package document: content file paths relative to the OPF. */
export function spineOrder(opf: string): string[] {
  const manifest = new Map<string, string>();
  for (const m of opf.matchAll(/<item\b[^>]*>/gi)) {
    const id = /\bid="([^"]+)"/i.exec(m[0])?.[1];
    const href = /\bhref="([^"]+)"/i.exec(m[0])?.[1];
    if (id && href) manifest.set(id, decodeURIComponent(href));
  }
  const order: string[] = [];
  for (const m of opf.matchAll(/<itemref\b[^>]*\bidref="([^"]+)"/gi)) {
    const href = manifest.get(m[1]);
    if (href) order.push(href);
  }
  return order;
}

/** The OPF path a container.xml names. */
export function rootFile(containerXml: string): string | undefined {
  return /full-path="([^"]+)"/i.exec(containerXml)?.[1];
}
