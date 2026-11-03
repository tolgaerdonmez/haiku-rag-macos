import { extensionOf } from "../doctype";
import { locateLine } from "../text";

const CONTEXT_LINES = 3;
const MAX_PASSAGE_LINES = 40;

const LANGUAGE: Record<string, string> = {
  md: "markdown",
  markdown: "markdown",
  mdx: "markdown",
  py: "python",
  ts: "typescript",
  tsx: "tsx",
  js: "javascript",
  jsx: "jsx",
  mjs: "javascript",
  cjs: "javascript",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  yml: "yaml",
  rs: "rust",
  rb: "ruby",
  kt: "kotlin",
  hs: "haskell",
  ex: "elixir",
  exs: "elixir",
  h: "c",
  hpp: "cpp",
  cc: "cpp",
  tex: "latex",
  txt: "text",
  text: "text",
  log: "text",
};

export function languageOf(path: string): string {
  const ext = extensionOf(path);
  return LANGUAGE[ext] ?? ext;
}

/** The passage's 1-based first and last line in `content`, or undefined when it cannot be placed. */
export function passageLines(
  content: string,
  passage: string,
  line?: number,
): { start: number; end: number } | undefined {
  const lines = content.split(/\r?\n/);
  const start = line && line <= lines.length ? line : locateLine(content, passage);
  if (!start) return undefined;
  const own = passage.split(/\r?\n/).filter((l) => l.trim());
  const last = own.at(-1) ?? "";
  const window = lines.slice(start - 1, start - 1 + MAX_PASSAGE_LINES * 4).join("\n");
  const found = own.length > 1 ? locateLine(window, last) : 1;
  const end = start - 1 + (found ?? Math.min(own.length, MAX_PASSAGE_LINES));
  return { start, end: Math.min(Math.max(start, end), start + MAX_PASSAGE_LINES - 1, lines.length) };
}

/** A fence longer than any backtick run in `text`. */
function fence(text: string): string {
  const longest = Math.max(2, ...[...text.matchAll(/`+/g)].map((m) => m[0].length));
  return "`".repeat(longest + 1);
}

/**
 * The passage's lines with a few around them as a code block, numbered, the
 * passage's own lines marked with `▌`.
 */
export function textContextMarkdown(path: string, content: string, passage: string, line?: number): string | undefined {
  const span = passageLines(content, passage, line);
  if (!span) return undefined;
  const lines = content.split(/\r?\n/);
  const from = Math.max(1, span.start - CONTEXT_LINES);
  const to = Math.min(lines.length, span.end + CONTEXT_LINES);
  const width = String(to).length;
  const body = lines
    .slice(from - 1, to)
    .map((text, i) => {
      const n = from + i;
      const mark = n >= span.start && n <= span.end ? "▌" : " ";
      return `${String(n).padStart(width)} ${mark} ${text}`;
    })
    .join("\n");
  const f = fence(body);
  return `${f}${languageOf(path)}\n${body}\n${f}`;
}
