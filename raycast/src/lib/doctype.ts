import { extname } from "path";

export type FileKind = "pdf" | "text" | "word" | "powerpoint" | "html" | "epub" | "other";

const TEXT_EXTENSIONS = new Set([
  "md",
  "markdown",
  "mdx",
  "txt",
  "text",
  "rst",
  "org",
  "adoc",
  "tex",
  "bib",
  "csv",
  "tsv",
  "log",
  "py",
  "ts",
  "tsx",
  "js",
  "jsx",
  "mjs",
  "cjs",
  "json",
  "jsonl",
  "yaml",
  "yml",
  "toml",
  "ini",
  "cfg",
  "conf",
  "sh",
  "bash",
  "zsh",
  "fish",
  "c",
  "h",
  "cc",
  "cpp",
  "hpp",
  "m",
  "mm",
  "rs",
  "go",
  "java",
  "kt",
  "swift",
  "rb",
  "php",
  "lua",
  "nix",
  "css",
  "scss",
  "sql",
  "r",
  "jl",
  "hs",
  "ml",
  "ex",
  "exs",
  "zig",
  "vim",
  "xml",
  "svg",
]);

const BY_EXTENSION: Record<string, FileKind> = {
  pdf: "pdf",
  doc: "word",
  docx: "word",
  docm: "word",
  dotx: "word",
  ppt: "powerpoint",
  pptx: "powerpoint",
  pptm: "powerpoint",
  potx: "powerpoint",
  html: "html",
  htm: "html",
  xhtml: "html",
  epub: "epub",
};

const BY_TYPE: Record<string, FileKind> = {
  pdf: "pdf",
  "scanned-pdf": "pdf",
  scanned_pdf: "pdf",
  "application/pdf": "pdf",
  markdown: "text",
  md: "text",
  text: "text",
  code: "text",
  asciidoc: "text",
  "text/markdown": "text",
  "text/plain": "text",
  docx: "word",
  word: "word",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "word",
  "application/msword": "word",
  pptx: "powerpoint",
  powerpoint: "powerpoint",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "powerpoint",
  "application/vnd.ms-powerpoint": "powerpoint",
  html: "html",
  "text/html": "html",
  "application/xhtml+xml": "html",
  epub: "epub",
  "application/epub+zip": "epub",
};

export function extensionOf(path: string): string {
  return extname(path).slice(1).toLowerCase();
}

/** The file's extension decides; the service's document type covers files without a telling one. */
export function fileKind(path: string, documentType?: string): FileKind {
  const ext = extensionOf(path);
  if (BY_EXTENSION[ext]) return BY_EXTENSION[ext];
  if (TEXT_EXTENSIONS.has(ext)) return "text";
  const type = documentType?.toLowerCase().trim();
  if (type) {
    if (BY_TYPE[type]) return BY_TYPE[type];
    if (type.startsWith("text/") || type.startsWith("code")) return "text";
  }
  return "other";
}
