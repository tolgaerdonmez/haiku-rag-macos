/** Glob patterns from a form field: one per line, commas also separate. */
export function parsePatterns(text: string | undefined): string[] {
  return (text ?? "")
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);
}
