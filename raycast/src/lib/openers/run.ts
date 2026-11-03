import { Clipboard, environment, open } from "@raycast/api";
import { runAppleScript } from "@raycast/utils";
import { execFile, spawn } from "child_process";
import { createHash } from "crypto";
import { existsSync } from "fs";
import { copyFile, mkdir, readFile, readdir, stat, writeFile } from "fs/promises";
import { homedir } from "os";
import { basename, dirname, join } from "path";
import { promisify } from "util";
import { locateLine } from "../text";
import { SearchHit } from "../types";
import { pickSectionFile, rootFile, spineOrder } from "./epub";
import { powerPointScript, wordScript } from "./office";
import { OpenPlan, findText, htmlFragmentUrl, planOpen, redirectPage } from "./plan";
import { RexClientRow, RexSessionRow, findAppClient, findSession, newSessionArgs, newTabArgs } from "./rex";
import { parseBounds, pageBoundsScript, revealScript, skimTarget } from "./skim";

const run = promisify(execFile);

export interface OpenerSettings {
  rexPath: string;
  rexSession: string;
  editorCommand: string;
}

const APP_DIRS = ["/Applications", join(homedir(), "Applications"), "/System/Applications"];

export function hasApp(name: string): boolean {
  return APP_DIRS.some((dir) => existsSync(join(dir, `${name}.app`)));
}

/** What happened, for the HUD. */
export type OpenOutcome = string;

async function json<T>(file: string, args: string[]): Promise<T> {
  const { stdout } = await run(file, args, { timeout: 10_000 });
  return JSON.parse(stdout) as T;
}

async function openInSkim(plan: Extract<OpenPlan, { kind: "skim" }>): Promise<OpenOutcome> {
  const bounds = parseBounds(await runAppleScript(pageBoundsScript(plan.path, plan.page), { timeout: 30_000 }));
  const target = skimTarget(plan.boxes, bounds);
  const how = await runAppleScript(revealScript(plan.path, plan.page, target, plan.preferBox, plan.focus), {
    timeout: 15_000,
  });
  const where = `${basename(plan.path)}, page ${plan.page}`;
  if (how === "focus") return `Opened ${where}, match selected`;
  if (how === "text") return `Opened ${where}, passage selected`;
  if (how === "box") return `Opened ${where}, passage outlined`;
  return `Opened ${where}`;
}

async function editorLine(plan: Extract<OpenPlan, { kind: "editor" }>): Promise<number> {
  if (plan.line && plan.line > 0) return plan.line;
  try {
    const content = await readFile(plan.path, "utf8");
    return locateLine(content, plan.passage) ?? 1;
  } catch {
    return 1;
  }
}

async function openInRex(plan: Extract<OpenPlan, { kind: "editor" }>, settings: OpenerSettings): Promise<OpenOutcome> {
  const rex = settings.rexPath;
  if (!existsSync(rex)) throw new Error(`Rex CLI not found at ${rex}; set it in the extension preferences`);
  const line = await editorLine(plan);
  const label = settings.rexSession.trim() || "haiku";
  const { sessions } = await json<{ sessions: RexSessionRow[] }>(rex, ["ls", "--json"]);
  const existing = findSession(sessions, label);
  let sessionId: string;
  let windowId: string | undefined;
  if (existing) {
    const created = await json<{ window_id?: string }>(
      rex,
      newTabArgs(existing.session_id, plan.path, line, settings.editorCommand),
    );
    sessionId = existing.session_id;
    windowId = created.window_id;
  } else {
    const created = await json<{ session_id: string; initial_windows?: { window_id?: string }[] }>(
      rex,
      newSessionArgs(label, plan.path, line, settings.editorCommand),
    );
    sessionId = created.session_id;
    windowId = created.initial_windows?.[0]?.window_id;
  }
  try {
    const { clients } = await json<{ clients: RexClientRow[] }>(rex, ["client", "ls", "--json"]);
    const app = findAppClient(clients);
    if (app) {
      const args = ["-C", app.client_id, "do", "session.select", `session_id=${sessionId}`];
      if (windowId) args.push(`window_id=${windowId}`);
      await run(rex, args, { timeout: 10_000 });
    }
  } catch {
    // the tab exists either way; showing it is a convenience
  }
  await run("/usr/bin/open", ["-b", "com.superlogical.rex"]).catch(() => undefined);
  return `Opened ${basename(plan.path)} at line ${line} in Rex`;
}

/**
 * Opens a file URL with its fragment in the default browser. Launch Services drops the fragment of
 * a file URL, so the browser first loads a local page that replaces itself with the URL.
 */
async function openUrlInBrowser(url: string): Promise<void> {
  if (!url.includes("#")) {
    await open(url);
    return;
  }
  const dir = join(environment.supportPath, "redirect");
  await mkdir(dir, { recursive: true });
  const page = join(dir, `${createHash("sha1").update(url).digest("hex").slice(0, 16)}.html`);
  await writeFile(page, redirectPage(url), "utf8");
  await open(page);
}

async function extractEpub(path: string): Promise<string> {
  const info = await stat(path);
  const key = createHash("sha1").update(`${path}\u0000${info.mtimeMs}`).digest("hex").slice(0, 16);
  const dir = join(environment.supportPath, "epub", key);
  if (!existsSync(join(dir, "META-INF", "container.xml"))) {
    await mkdir(dir, { recursive: true });
    await run("/usr/bin/unzip", ["-q", "-o", path, "-d", dir], { timeout: 30_000 });
  }
  return dir;
}

async function epubContentFiles(dir: string): Promise<string[]> {
  try {
    const opf = rootFile(await readFile(join(dir, "META-INF", "container.xml"), "utf8"));
    if (opf) {
      const opfPath = join(dir, opf);
      const order = spineOrder(await readFile(opfPath, "utf8")).map((href) => join(dirname(opfPath), href));
      if (order.length > 0) return order;
    }
  } catch {
    // fall through to a directory walk
  }
  const entries = await readdir(dir, { recursive: true });
  return entries.filter((e) => /\.x?html?$/i.test(e)).map((e) => join(dir, e));
}

/** An HTML-typed copy beside an XHTML file: browsers apply text fragments to HTML documents only. */
async function asHtml(file: string): Promise<string> {
  if (/\.html?$/i.test(file)) return file;
  const copy = file.replace(/\.[^./]+$/, "") + ".haiku.html";
  if (!existsSync(copy)) await copyFile(file, copy);
  return copy;
}

async function openEpubSection(plan: Extract<OpenPlan, { kind: "epub" }>): Promise<OpenOutcome> {
  const dir = await extractEpub(plan.path);
  const files = await epubContentFiles(dir);
  const contents = await Promise.all(
    files.map(async (f) => ({ path: f, html: await readFile(f, "utf8").catch(() => "") })),
  );
  const section = pickSectionFile(contents, plan.heading, plan.passage);
  if (!section) return openInBooks(plan.path, findText(plan.passage));
  await openUrlInBrowser(htmlFragmentUrl(await asHtml(section), plan.heading, plan.passage));
  return `Opened the section of ${basename(plan.path)}`;
}

export async function openInBooks(path: string, passage: string): Promise<OpenOutcome> {
  await Clipboard.copy(passage);
  await open(path, "com.apple.iBooksX");
  return "Opened in Books; passage copied, press ⌘F and paste";
}

/** The application that opens the file by default, if any claims it. */
async function defaultAppFor(path: string): Promise<string | undefined> {
  const script = `ObjC.import("AppKit");
function run(argv) {
  const url = $.NSWorkspace.sharedWorkspace.URLForApplicationToOpenURL($.NSURL.fileURLWithPath(argv[0]));
  return url.isNil() ? "" : url.path.js;
}`;
  try {
    const { stdout } = await run("/usr/bin/osascript", ["-l", "JavaScript", "-e", script, path], { timeout: 10_000 });
    return stdout.trim() || undefined;
  } catch {
    return undefined;
  }
}

async function copyAndOpen(path: string, passage: string, app?: string): Promise<OpenOutcome> {
  await Clipboard.copy(passage);
  if (!app && !(await defaultAppFor(path))) {
    const child = spawn("/usr/bin/qlmanage", ["-p", path], { detached: true, stdio: "ignore" });
    child.unref();
    return `No app opens ${basename(path)}; showing it in Quick Look, passage copied`;
  }
  await open(path, app);
  return `Opened ${basename(path)}; passage copied, press ⌘F and paste`;
}

export async function executePlan(plan: OpenPlan, settings: OpenerSettings): Promise<OpenOutcome> {
  if (!existsSync(plan.path)) throw new Error(`File not found: ${plan.path}`);
  switch (plan.kind) {
    case "skim":
      return openInSkim(plan);
    case "editor":
      return openInRex(plan, settings);
    case "word":
      await Clipboard.copy(plan.passage);
      await runAppleScript(wordScript(plan.path, plan.page), { timeout: 60_000 });
      return `Opened ${basename(plan.path)}${plan.page ? ` at page ${plan.page}` : ""}; passage copied for ⌘F`;
    case "powerpoint":
      await Clipboard.copy(plan.passage);
      await runAppleScript(powerPointScript(plan.path, plan.slide), { timeout: 60_000 });
      return `Opened ${basename(plan.path)}${plan.slide ? ` at slide ${plan.slide}` : ""}; passage copied for ⌘F`;
    case "html":
      await openUrlInBrowser(plan.url);
      return `Opened ${basename(plan.path)} at the section`;
    case "epub":
      return openEpubSection(plan);
    case "copy-and-open":
      return copyAndOpen(plan.path, plan.passage, plan.app);
  }
}

export async function openHit(hit: SearchHit, settings: OpenerSettings, query = ""): Promise<OpenOutcome> {
  return executePlan(planOpen(hit, { hasApp }, query), settings);
}
