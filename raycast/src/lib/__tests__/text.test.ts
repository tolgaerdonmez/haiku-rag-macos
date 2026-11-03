import { describe, expect, it } from "vitest";
import {
  PASSAGES_PER_FILE,
  documentName,
  formatCitation,
  groupByDocument,
  locationLabel,
  pageLabel,
  topPassages,
} from "../citation";
import { parsePatterns } from "../patterns";
import {
  emphasiseMarkdown,
  emphasisePlain,
  focusPhrases,
  locateLine,
  queryTerms,
  snippetSpans,
  splitHighlights,
  stripMarkdown,
  toBoldUnicode,
} from "../text";
import { SearchHit } from "../types";

function hit(over: Partial<SearchHit> = {}): SearchHit {
  return {
    id: "files:c1",
    documentId: "d1",
    path: "/Users/me/Guitar/Country Guitar.pdf",
    title: "Country Guitar",
    documentType: "pdf",
    pages: [40],
    headings: ["Chapter 5", "Travis Picking"],
    labels: ["text"],
    snippet: "Travis picking",
    text: "Travis picking",
    score: 1,
    boxes: [],
    ...over,
  };
}

describe("emphasis", () => {
  it("drops stopwords and short words from the query", () => {
    expect(queryTerms("What is the Travis picking of a song?")).toEqual(["travis", "picking", "song"]);
  });

  it("splits at the service's highlight offsets", () => {
    expect(splitHighlights("...Travis picking is", [[3, 9]])).toEqual([
      { text: "...", match: false },
      { text: "Travis", match: true },
      { text: " picking is", match: false },
    ]);
  });

  it("clips overlapping and out-of-range highlights", () => {
    const spans = splitHighlights("abcdef", [
      [4, 99],
      [0, 2],
      [1, 3],
    ]);
    expect(spans).toEqual([
      { text: "ab", match: true },
      { text: "c", match: true },
      { text: "d", match: false },
      { text: "ef", match: true },
    ]);
  });

  it("falls back to query words when the service sent no highlights", () => {
    const spans = snippetSpans("Fingerstyle picks and picking patterns", "picking", []);
    expect(spans.filter((s) => s.match).map((s) => s.text)).toEqual(["picking"]);
    expect(
      snippetSpans("Pickers pick it", "picked", undefined)
        .filter((s) => s.match)
        .map((s) => s.text),
    ).toEqual(["pick"]);
    expect(snippetSpans("an is it", "it").filter((s) => s.match)).toHaveLength(0);
    expect(
      snippetSpans("guitars", "guitar")
        .filter((s) => s.match)
        .map((s) => s.text),
    ).toEqual(["guitars"]);
  });

  it("bolds in markdown and escapes the rest", () => {
    expect(emphasiseMarkdown(splitHighlights("a *b* c", [[3, 4]]))).toBe("a \\***b**\\* c");
  });

  it("bolds plain titles with Unicode letters", () => {
    expect(toBoldUnicode("Ab9ü")).toBe("𝗔𝗯𝟵ü");
    expect(emphasisePlain(splitHighlights("fox den", [[0, 3]]))).toBe("𝗳𝗼𝘅 den");
  });

  it("strips markdown markup", () => {
    expect(stripMarkdown("## Title\n\n- **bold** [link](http://x) `code`")).toBe("Title bold link code");
  });
});

describe("locateLine", () => {
  const content = ["# Notes", "", "intro", "The passage about modal", "interchange lives here.", "end"].join("\n");

  it("finds the line where the passage starts, even when rewrapped", () => {
    expect(locateLine(content, "The passage about modal interchange lives here.")).toBe(4);
  });

  it("matches whole words only", () => {
    expect(locateLine("theme passage\nthe passage here", "the passage")).toBe(2);
  });

  it("returns undefined when the passage is not there", () => {
    expect(locateLine(content, "nothing like it at all")).toBeUndefined();
    expect(locateLine(content, "")).toBeUndefined();
  });
});

describe("citations and grouping", () => {
  it("labels pages, page ranges, slides and lines", () => {
    expect(pageLabel(hit())).toBe("p. 40");
    expect(pageLabel(hit({ pages: [41, 40] }))).toBe("pp. 40–41");
    expect(pageLabel(hit({ path: "/d.pptx", documentType: "powerpoint", pages: [3] }))).toBe("slide 3");
    expect(pageLabel(hit({ pages: [], line: 12 }))).toBe("line 12");
    expect(pageLabel(hit({ pages: [] }))).toBeUndefined();
  });

  it("formats file, page and heading trail, then the path", () => {
    expect(formatCitation(hit())).toBe(
      "Country Guitar (Country Guitar.pdf), p. 40, “Chapter 5 › Travis Picking”\n/Users/me/Guitar/Country Guitar.pdf",
    );
    expect(formatCitation(hit({ title: undefined, headings: [], pages: [] }))).toBe(
      "Country Guitar.pdf\n/Users/me/Guitar/Country Guitar.pdf",
    );
  });

  it("groups by document in ranking order", () => {
    const groups = groupByDocument([
      hit({ id: "1", documentId: "a" }),
      hit({ id: "2", documentId: "b", path: "/b.md" }),
      hit({ id: "3", documentId: "a" }),
      hit({ id: "4", documentId: "a", database: "other" }),
    ]);
    expect(groups.map((g) => g.hits.map((h) => h.id))).toEqual([["1", "3"], ["2"], ["4"]]);
  });

  it("shows each file's best three passages and counts the rest", () => {
    const hits = ["1", "2", "3", "4", "5"].map((id) => hit({ id, documentId: "a" }));
    const [a, b] = topPassages(groupByDocument([...hits, hit({ id: "6", documentId: "b" })]));
    expect(PASSAGES_PER_FILE).toBe(3);
    expect(a.shown.map((h) => h.id)).toEqual(["1", "2", "3"]);
    expect(a.more).toBe(2);
    expect(a.group.hits).toHaveLength(5);
    expect(b.shown.map((h) => h.id)).toEqual(["6"]);
    expect(b.more).toBe(0);
  });

  it("shows every passage of a file when uncapped", () => {
    const hits = ["1", "2", "3", "4"].map((id) => hit({ id }));
    const [only] = topPassages(groupByDocument(hits), Number.POSITIVE_INFINITY);
    expect(only.shown).toHaveLength(4);
    expect(only.more).toBe(0);
  });

  it("names the passage's place in words", () => {
    expect(locationLabel(hit())).toBe("Page 40");
    expect(locationLabel(hit({ pages: [138, 136, 137] }))).toBe("Pages 136–138");
    expect(locationLabel(hit({ path: "/d.pptx", pages: [4] }))).toBe("Slide 4");
    expect(locationLabel(hit({ path: "/d.pptx", pages: [4, 5] }))).toBe("Slides 4–5");
    expect(locationLabel(hit({ path: "/n.md", pages: [], line: 42 }))).toBe("Line 42");
    expect(locationLabel(hit({ path: "/n.md", pages: [] }))).toBeUndefined();
  });

  it("names a document by its title, else its file without the extension", () => {
    expect(documentName(hit())).toBe("Country Guitar");
    expect(documentName(hit({ title: "Country Guitar.pdf" }))).toBe("Country Guitar");
    expect(documentName(hit({ title: undefined, path: "/x/notes" }))).toBe("notes");
    expect(documentName(hit({ title: undefined, path: "/x/[Hanson,_Mark]_The_art_of_*solo*.pdf" }))).toBe(
      "[Hanson, Mark] The art of solo",
    );
    expect(documentName(hit({ title: undefined, path: "/x/[]_Dave_Hunter,_Play.pdf" }))).toBe("Dave Hunter, Play");
    expect(documentName(hit({ title: undefined, path: "/x/___.pdf" }))).toBe("___.pdf");
  });

  it("ranks files by their best passage", () => {
    const groups = groupByDocument([
      hit({ id: "1", documentId: "a", score: 0.9 }),
      hit({ id: "2", documentId: "b", score: 0.8 }),
      hit({ id: "3", documentId: "b", score: 0.7 }),
      hit({ id: "4", documentId: "b", score: 0.6 }),
      hit({ id: "5", documentId: "c", score: 0.5 }),
    ]);
    expect(groups.map((g) => g.hits[0].documentId)).toEqual(["a", "b", "c"]);
  });

  it("parses folder patterns", () => {
    expect(parsePatterns("**/*.pdf\n **/*.md , \n")).toEqual(["**/*.pdf", "**/*.md"]);
    expect(parsePatterns(undefined)).toEqual([]);
  });
});

describe("focusPhrases", () => {
  const snippet =
    "...ipsum dolor sit amet. Filler line 24 on page 2 lorem ipsum dolor sit amet. The quick brown fox jumps over the lazy dog near the riverbank. Filler line 26 on page 2...";

  it("picks the sentence with the most highlighted words, then shorter runs around the first match", () => {
    const highlights: [number, number][] = [
      [82, 87],
      [88, 93],
      [94, 97],
      [131, 140],
    ];
    expect(focusPhrases(snippet, "", highlights)).toEqual([
      "The quick brown fox jumps over the lazy dog near",
      "The quick brown fox jumps",
      "quick brown fox",
    ]);
  });

  it("falls back to the query words and returns nothing without a match", () => {
    expect(focusPhrases(snippet, "riverbank", [])[0]).toBe("The quick brown fox jumps over the lazy dog near");
    expect(focusPhrases(snippet, "", [])).toEqual([]);
  });
});
