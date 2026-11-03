import { Action, ActionPanel, Icon, List, environment } from "@raycast/api";
import { usePromise } from "@raycast/utils";
import { useMemo, useRef, useState } from "react";
import { basename, dirname, join } from "path";
import { ShownGroup, documentName, groupByDocument, locationLabel, pageLabel, topPassages } from "../lib/citation";
import { fileKind } from "../lib/doctype";
import { preferences, resultLimit, searchService } from "../lib/preferences";
import { renderPreview } from "../lib/preview/render";
import { emphasiseMarkdown, emphasisePlain, snippetSpans } from "../lib/text";
import { DocumentGroup, SearchHit } from "../lib/types";
import { HitActions } from "./HitActions";

export interface DocumentScope {
  documentId: string;
  database?: string;
  title: string;
}

/** Every passage of a document the per-document search returns. */
export const DOCUMENT_LIMIT = 50;
/** Selections held shorter than this, while moving through the list, render nothing. */
const PREVIEW_DELAY_MS = 120;

const KIND_ICON: Record<string, Icon> = {
  pdf: Icon.Document,
  text: Icon.Code,
  word: Icon.Paragraph,
  powerpoint: Icon.AppWindowGrid2x2,
  html: Icon.Globe,
  epub: Icon.Book,
  other: Icon.BlankDocument,
};

function tidyHome(path: string): string {
  const home = process.env.HOME;
  return home && path.startsWith(home) ? `~${path.slice(home.length)}` : path;
}

/** Whitespace flattened to spaces one for one, so highlight offsets still apply. */
function flatten(text: string): string {
  return text.replace(/\s/g, " ");
}

function rowTitle(hit: SearchHit, query: string): string {
  return emphasisePlain(snippetSpans(flatten(hit.snippet), query, hit.highlights))
    .replace(/ {2,}/g, " ")
    .trim();
}

/** The document's name with the passage's page beside it, as the pane's one heading line. */
function header(hit: SearchHit): string {
  const where = locationLabel(hit);
  return `### ${documentName(hit)}${where ? `  \`${where}\`` : ""}`;
}

/** The passage as text, for files without a rendered preview. */
function textMarkdown(hit: SearchHit, query: string): string {
  const parts = [
    `> ${emphasiseMarkdown(snippetSpans(flatten(hit.snippet), query, hit.highlights))
      .replace(/ {2,}/g, " ")
      .trim()}`,
  ];
  const context = hit.context && hit.context.trim() !== hit.text.trim() ? hit.context : hit.text;
  if (context && context.trim() !== hit.snippet.trim()) parts.push("---", context);
  return parts.join("\n\n");
}

interface Preview {
  id: string;
  markdown?: string;
}

function pause(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(signal.reason);
      },
      { once: true },
    );
  });
}

/** The rendered preview of the selected hit only. */
function useSelectedPreview(hit: SearchHit | undefined) {
  const abortable = useRef<AbortController>(null);
  const dir = useMemo(() => join(environment.supportPath, "previews", "pages"), []);
  const { data, isLoading } = usePromise(
    async (selected: SearchHit | undefined): Promise<Preview | undefined> => {
      if (!selected) return undefined;
      const signal = abortable.current?.signal;
      await pause(PREVIEW_DELAY_MS, signal);
      try {
        return { id: selected.id, ...(await renderPreview(selected, dir, undefined, signal)) };
      } catch (error) {
        if (signal?.aborted) throw error;
        console.error(`No preview for ${selected.path}:`, error);
        return { id: selected.id };
      }
    },
    [hit],
    { abortable, onError: () => undefined },
  );
  return { preview: data && data.id === hit?.id ? data : undefined, rendering: isLoading };
}

/**
 * The selected passage's preview under a one-line caption, with no metadata
 * rows so the rendered page gets the pane. While it renders only the caption
 * shows, so the pane opens unscrolled.
 */
function HitDetail({
  hit,
  query,
  preview,
  rendering,
}: {
  hit: SearchHit;
  query: string;
  preview?: Preview;
  rendering?: boolean;
}) {
  const parts = [header(hit)];
  if (preview?.markdown) {
    parts.push(preview.markdown);
  } else if (!rendering) {
    parts.push(textMarkdown(hit, query), `\`${tidyHome(dirname(hit.path))}\``);
  }
  return <List.Item.Detail isLoading={rendering && !preview} markdown={parts.join("\n\n")} />;
}

/** The passages a file's section does not show, for the "show all" row's detail. */
function moreMarkdown(shown: ShownGroup, query: string): string {
  const rest = shown.group.hits.slice(shown.shown.length).map((hit) => {
    const where = [pageLabel(hit), hit.headings.at(-1)].filter(Boolean).join(" · ");
    return `- ${where ? `**${where}** ` : ""}${rowTitle(hit, query)}`;
  });
  return [`### ${shown.more} more in ${shown.group.title}`, ...rest].join("\n\n");
}

function scopeOf(group: DocumentGroup): DocumentScope {
  const hit = group.hits[0];
  return { documentId: hit.documentId, database: hit.database, title: group.title };
}

export function SearchView({ scope, initialQuery = "" }: { scope?: DocumentScope; initialQuery?: string }) {
  const prefs = useMemo(() => preferences(), []);
  const service = useMemo(() => searchService(prefs), [prefs]);
  const limit = scope ? DOCUMENT_LIMIT : resultLimit(prefs);
  const [query, setQuery] = useState(initialQuery);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showingDetail, setShowingDetail] = useState(true);
  const abortable = useRef<AbortController>(null);

  const { data, isLoading, error } = usePromise(
    async (q: string) => {
      if (!q.trim()) return undefined;
      return service.search(
        q,
        { limit, context: true, documentId: scope?.documentId, database: scope?.database },
        abortable.current?.signal,
      );
    },
    [query],
    { abortable, failureToastOptions: { title: "Search failed" } },
  );

  const groups = useMemo(() => groupByDocument(data?.hits ?? []), [data]);
  const sections = useMemo(
    () => (scope ? topPassages(groups, Number.POSITIVE_INFINITY) : topPassages(groups)),
    [groups, scope],
  );
  const selected = useMemo(
    () => (showingDetail ? data?.hits.find((h) => h.id === selectedId) : undefined),
    [data, selectedId, showingDetail],
  );
  const { preview, rendering } = useSelectedPreview(selected);
  const hasHits = groups.length > 0;
  const detailAction = (
    <Action
      title={showingDetail ? "Hide Details" : "Show Details"}
      icon={Icon.Sidebar}
      shortcut={{ modifiers: ["cmd"], key: "d" }}
      onAction={() => setShowingDetail((v) => !v)}
    />
  );

  return (
    <List
      isLoading={isLoading}
      throttle
      filtering={false}
      searchText={query}
      onSearchTextChange={setQuery}
      onSelectionChange={setSelectedId}
      isShowingDetail={showingDetail && hasHits}
      navigationTitle={scope ? `Search in ${scope.title}` : undefined}
      searchBarPlaceholder={scope ? `Search inside ${scope.title}…` : "Search your files by meaning…"}
    >
      {!query.trim() ? (
        <List.EmptyView
          icon={Icon.MagnifyingGlass}
          title={scope ? `Search inside ${scope.title}` : "Search your files"}
          description="Type a question or keywords. Enter opens the result at its page and passage."
        />
      ) : error && !hasHits ? (
        <List.EmptyView icon={Icon.Warning} title="The search service did not answer" description={error.message} />
      ) : !isLoading && !hasHits ? (
        <List.EmptyView icon={Icon.MagnifyingGlass} title="No matching passages" />
      ) : null}
      {sections.map((section) => {
        const { group } = section;
        const drillIn = <SearchView scope={scopeOf(group)} initialQuery={query} />;
        const count = `${group.hits.length} ${group.hits.length === 1 ? "passage" : "passages"}`;
        return (
          <List.Section
            key={group.key}
            title={documentName(group.hits[0])}
            subtitle={`${count} · ${basename(dirname(group.path))}`}
          >
            {section.shown.map((hit) => {
              const page = pageLabel(hit);
              const heading = hit.headings.at(-1);
              const title = rowTitle(hit, query);
              const isSelected = hit.id === selected?.id;
              return (
                <List.Item
                  key={hit.id}
                  id={hit.id}
                  icon={KIND_ICON[fileKind(hit.path, hit.documentType)]}
                  title={showingDetail ? [page, heading, title].filter(Boolean).join(" · ") : title}
                  subtitle={showingDetail ? undefined : heading}
                  accessories={showingDetail ? undefined : page ? [{ tag: page }] : undefined}
                  keywords={hit.headings}
                  detail={
                    <HitDetail
                      hit={hit}
                      query={query}
                      preview={isSelected ? preview : undefined}
                      rendering={isSelected && rendering}
                    />
                  }
                  actions={
                    <HitActions
                      hit={hit}
                      query={query}
                      scoped={!!scope}
                      drillIn={scope ? undefined : drillIn}
                      extra={detailAction}
                    />
                  }
                />
              );
            })}
            {section.more > 0 ? (
              <List.Item
                key={`more:${group.key}`}
                id={`more:${group.key}`}
                icon={Icon.ChevronRight}
                title={`Show all ${count} in this file`}
                detail={<List.Item.Detail markdown={moreMarkdown(section, query)} />}
                actions={
                  <ActionPanel>
                    <Action.Push title="Show All in This File" icon={Icon.ChevronRight} target={drillIn} />
                    {detailAction}
                  </ActionPanel>
                }
              />
            ) : null}
          </List.Section>
        );
      })}
      {data && data.skipped > 0 ? (
        <List.Section title={`${data.skipped} more results are not local files`}>
          <List.Item
            title="Results without a local file are hidden"
            icon={Icon.Info}
            actions={
              <ActionPanel>
                <Action.CopyToClipboard content={query} title="Copy Query" />
              </ActionPanel>
            }
          />
        </List.Section>
      ) : null}
    </List>
  );
}
