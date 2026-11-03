import { basename, dirname } from "path";

export interface RexSessionRow {
  session_id: string;
  label?: string;
}

export interface RexClientRow {
  client_id: string;
  info?: { kind?: string };
  connection_state?: string;
}

/** Splits the configured editor command into words; quotes group words that contain spaces. */
export function editorWords(command: string): string[] {
  const words = command.match(/"[^"]*"|'[^']*'|\S+/g) ?? [];
  return words.map((w) => w.replace(/^(["'])(.*)\1$/, "$2"));
}

/** `+LINE` and a centring command for vi-family editors; other editors get the file only. */
export function editorCommand(editor: string, path: string, line: number): string[] {
  const words = editorWords(editor.trim() || "nvim");
  const program = basename(words[0] ?? "nvim");
  if (/^n?vim?$|^gvim$|^mvim$/.test(program)) return [...words, `+${line}`, "-c", "normal! zz", path];
  if (program === "hx" || program === "helix") return [...words, `${path}:${line}`];
  return [...words, path];
}

export function tabName(path: string, line: number): string {
  return `${basename(path)}:${line}`;
}

export function findSession(sessions: RexSessionRow[], label: string): RexSessionRow | undefined {
  return sessions.find((s) => s.label === label) ?? sessions.find((s) => s.session_id === label);
}

/** The connected Rex app, when exactly one is connected. */
export function findAppClient(clients: RexClientRow[]): RexClientRow | undefined {
  const apps = clients.filter((c) => c.info?.kind === "app" && c.connection_state !== "disconnected");
  return apps.length === 1 ? apps[0] : undefined;
}

/** Arguments for a new tab running the editor in an existing session. */
export function newTabArgs(sessionId: string, path: string, line: number, editor: string): string[] {
  return [
    "window",
    "new",
    "-s",
    sessionId,
    tabName(path, line),
    "-c",
    dirname(path),
    "--json",
    "--",
    ...editorCommand(editor, path, line),
  ];
}

/** Arguments for a new session whose first tab runs the editor. */
export function newSessionArgs(label: string, path: string, line: number, editor: string): string[] {
  return [
    "new",
    label,
    "-c",
    dirname(path),
    "--window",
    tabName(path, line),
    "--json",
    "--",
    ...editorCommand(editor, path, line),
  ];
}
