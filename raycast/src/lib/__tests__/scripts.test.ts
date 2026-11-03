import { describe, expect, it } from "vitest";
import { pickSectionFile, rootFile, spineOrder } from "../openers/epub";
import { powerPointScript, wordScript } from "../openers/office";
import { editorCommand, findAppClient, findSession, newSessionArgs, newTabArgs } from "../openers/rex";
import { appleScriptString, pageBoundsScript, parseBounds, revealScript, skimTarget } from "../openers/skim";
import { BoundingBox } from "../types";

const A4: [number, number, number, number] = [0, 842, 595, 0];
const box: BoundingBox = {
  page: 2,
  left: 72,
  top: 497,
  right: 400,
  bottom: 514,
  coordOrigin: "TOPLEFT",
  pageWidth: 595,
  pageHeight: 842,
};

describe("Skim scripts", () => {
  it("quotes paths for AppleScript", () => {
    expect(appleScriptString('/a/"b"\\c.pdf')).toBe('"/a/\\"b\\"\\\\c.pdf"');
    expect(pageBoundsScript('/x/my "doc".pdf', 3)).toContain('open POSIX file "/x/my \\"doc\\".pdf"');
  });

  it("parses page bounds and rejects anything else", () => {
    expect(parseBounds("0, 841.89, 595.28, 0")).toEqual([0, 841.89, 595.28, 0]);
    expect(() => parseBounds("missing value")).toThrow();
  });

  it("selects text inside the mapped rectangle, outlining when there is none", () => {
    const target = skimTarget([box], A4);
    expect(target.rects[0]).toEqual([71, 346, 401, 327]);
    const script = revealScript("/t/s.pdf", 2, target, false);
    expect(script).toContain("set p to page 2 of d");
    expect(script).toContain("get selection for {71, 346, 401, 327} on p");
    expect(script).toContain("set useBox to false");
    expect(script).toContain("set selection bounds of d to {69, 348, 403, 325}");
    expect(script).not.toMatch(/make new note|save/);
  });

  it("selects a focus phrase found inside the boxes before the whole box text", () => {
    const script = revealScript("/t/s.pdf", 2, skimTarget([box], A4), false, ['a "quoted" phrase', "phrase"]);
    expect(script).toContain('repeat with phrase in {"a \\"quoted\\" phrase", "phrase"}');
    expect(script).toContain(
      "if (item 2 of r) is less than or equal to 348 and (item 4 of r) is greater than or equal to 325",
    );
    expect(script.indexOf('return "focus"')).toBeLessThan(script.indexOf('return "text"'));
    expect(revealScript("/t/s.pdf", 2, skimTarget([box], A4), false)).not.toContain("focus");
  });

  it("joins the selections of a passage split over several boxes", () => {
    const target = skimTarget([box, { ...box, top: 520, bottom: 540 }], A4);
    expect(revealScript("/t/s.pdf", 2, target, true)).toMatch(/set useBox to true[\s\S]*join sel to/);
  });

  it("only goes to the page without boxes", () => {
    const script = revealScript("/t/s.pdf", 5, skimTarget([], A4), false);
    expect(script).toContain("go d to page 5 of d");
    expect(script).not.toContain("selection");
  });
});

describe("Rex arguments", () => {
  it("starts vi-family editors at the line, centred", () => {
    expect(editorCommand("nvim", "/n/a.md", 43)).toEqual(["nvim", "+43", "-c", "normal! zz", "/n/a.md"]);
    expect(editorCommand("/opt/bin/vim -u NONE", "/n/a.md", 2)).toEqual([
      "/opt/bin/vim",
      "-u",
      "NONE",
      "+2",
      "-c",
      "normal! zz",
      "/n/a.md",
    ]);
    expect(editorCommand("hx", "/n/a.md", 9)).toEqual(["hx", "/n/a.md:9"]);
    expect(editorCommand("emacs -nw", "/n/a.md", 9)).toEqual(["emacs", "-nw", "/n/a.md"]);
  });

  it("opens a named tab in the file's folder", () => {
    expect(newTabArgs("session:1", "/n/My Notes/a.md", 7, "nvim")).toEqual([
      "window",
      "new",
      "-s",
      "session:1",
      "a.md:7",
      "-c",
      "/n/My Notes",
      "--json",
      "--",
      "nvim",
      "+7",
      "-c",
      "normal! zz",
      "/n/My Notes/a.md",
    ]);
    expect(newSessionArgs("haiku", "/n/a.md", 1, "nvim").slice(0, 7)).toEqual([
      "new",
      "haiku",
      "-c",
      "/n",
      "--window",
      "a.md:1",
      "--json",
    ]);
  });

  it("finds the session by label and the app among clients", () => {
    const sessions = [
      { session_id: "session:a", label: "firstmate" },
      { session_id: "session:b", label: "haiku" },
    ];
    expect(findSession(sessions, "haiku")?.session_id).toBe("session:b");
    expect(findSession(sessions, "session:a")?.label).toBe("firstmate");
    expect(findSession(sessions, "other")).toBeUndefined();
    const app = { client_id: "client:app", info: { kind: "app" }, connection_state: "connected" };
    expect(findAppClient([app, { client_id: "client:cli", info: { kind: "cli" } }, { client_id: "x" }])).toBe(app);
    expect(findAppClient([app, { ...app, client_id: "client:other" }])).toBeUndefined();
  });
});

describe("Office scripts", () => {
  it("goes to the page in Word and the slide in PowerPoint", () => {
    expect(wordScript("/w/r.docx", 3)).toContain("go to a page which go absolute count 3");
    expect(wordScript("/w/r.docx", undefined)).not.toContain("go to");
    expect(powerPointScript("/w/d.pptx", 7)).toContain("number 7");
  });
});

describe("EPUB sections", () => {
  const files = [
    { path: "ch1.xhtml", html: "<h1>Chapter One</h1><p>Scale filler.</p>" },
    {
      path: "ch2.xhtml",
      html: "<h1>Chapter Two</h1><h2>Sweep&nbsp;picking</h2><p>Sweep picking moves the <em>pick</em> across strings.</p>",
    },
  ];

  it("finds the file holding the passage, then the heading", () => {
    expect(pickSectionFile(files, "Sweep picking", "Sweep picking moves the pick across strings.")).toBe("ch2.xhtml");
    expect(pickSectionFile(files, "Chapter One", "no such words here")).toBe("ch1.xhtml");
    expect(pickSectionFile(files, undefined, "absent text entirely")).toBeUndefined();
  });

  it("reads the spine order through the manifest", () => {
    const opf = `<manifest><item id="c2" href="text/ch%202.xhtml"/><item href="ch1.xhtml" id="c1"/></manifest>
      <spine><itemref idref="c1"/><itemref idref="c2"/></spine>`;
    expect(spineOrder(opf)).toEqual(["ch1.xhtml", "text/ch 2.xhtml"]);
    expect(rootFile('<rootfile full-path="OEBPS/content.opf"/>')).toBe("OEBPS/content.opf");
  });
});
