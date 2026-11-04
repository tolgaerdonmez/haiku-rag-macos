import { BoundingBox, Failure, IndexedFolder, SearchHit, SearchResult, ServiceStatus } from "./types";

/** Wire shapes of docs/search-api.md. Unknown fields are ignored. */
export interface WireBox {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export interface WirePage {
  page: number;
  width: number;
  height: number;
  boxes: WireBox[];
}

export interface WireHit {
  rank: number;
  score: number;
  chunk_id: string;
  document_id: string;
  database: string;
  folder_id?: string | null;
  title?: string | null;
  path?: string | null;
  uri?: string | null;
  document_type: string;
  content_type?: string | null;
  page_numbers: number[];
  line?: number | null;
  headings?: string[] | null;
  labels: string[];
  snippet: string;
  snippet_highlights?: [number, number][];
  text: string;
  pages: WirePage[];
  context?: string | null;
}

export interface WireSearchResponse {
  query: string;
  took_ms?: number;
  hits: WireHit[];
}

export interface WireFolder {
  id: string;
  database: string;
  path: string;
  include?: string[];
  exclude?: string[];
  status?: {
    watching: boolean;
    documents: number;
    queued: number;
    failed: number;
    last_scan_at?: string | null;
  } | null;
}

export interface WireFailure {
  job_id: string;
  folder_id?: string | null;
  database?: string | null;
  path?: string | null;
  uri?: string | null;
  reason: string;
  attempts: number;
  failed_at?: string | null;
}

export interface WireStatus {
  indexing: boolean;
  queued: number;
  in_flight: number;
  failed: number;
  paused: boolean;
  pause_reason?: string | null;
  databases: { name: string; documents: number }[];
}

function pathFromUri(uri: string | null | undefined): string | undefined {
  if (!uri?.startsWith("file://")) return undefined;
  try {
    return decodeURIComponent(new URL(uri).pathname);
  } catch {
    return undefined;
  }
}

/** The hit in client form; undefined for a document that is not a local file. */
export function toSearchHit(wire: WireHit): SearchHit | undefined {
  const path = wire.path ?? pathFromUri(wire.uri);
  if (!path) return undefined;
  const boxes: BoundingBox[] = (wire.pages ?? []).flatMap((p) =>
    p.boxes.map((b) => ({
      page: p.page,
      left: b.left,
      top: b.top,
      right: b.right,
      bottom: b.bottom,
      coordOrigin: "TOPLEFT" as const,
      pageWidth: p.width,
      pageHeight: p.height,
    })),
  );
  return {
    id: `${wire.database}:${wire.chunk_id}`,
    documentId: wire.document_id,
    database: wire.database,
    folderId: wire.folder_id ?? undefined,
    path,
    title: wire.title ?? undefined,
    documentType: wire.document_type,
    pages: wire.page_numbers ?? [],
    headings: wire.headings ?? [],
    labels: wire.labels ?? [],
    snippet: wire.snippet,
    highlights: wire.snippet_highlights,
    text: wire.text,
    context: wire.context ?? undefined,
    score: wire.score,
    boxes,
    line: wire.line ?? undefined,
  };
}

export function toSearchResult(wire: WireSearchResponse): SearchResult {
  const hits = wire.hits.map(toSearchHit);
  return {
    query: wire.query,
    tookMs: wire.took_ms,
    hits: hits.filter((h): h is SearchHit => h !== undefined),
    skipped: hits.filter((h) => h === undefined).length,
  };
}

export function toFolder(wire: WireFolder): IndexedFolder {
  return {
    id: wire.id,
    database: wire.database,
    path: wire.path,
    include: wire.include ?? [],
    exclude: wire.exclude ?? [],
    status: wire.status
      ? {
          watching: wire.status.watching,
          documents: wire.status.documents,
          queued: wire.status.queued,
          failed: wire.status.failed,
          lastScanAt: wire.status.last_scan_at ?? undefined,
        }
      : undefined,
  };
}

export function toFailure(wire: WireFailure): Failure {
  return {
    jobId: wire.job_id,
    folderId: wire.folder_id ?? undefined,
    database: wire.database ?? undefined,
    path: wire.path ?? pathFromUri(wire.uri),
    reason: wire.reason,
    attempts: wire.attempts,
    failedAt: wire.failed_at ?? undefined,
  };
}

export function toStatus(wire: WireStatus): ServiceStatus {
  return {
    indexing: wire.indexing,
    queued: wire.queued,
    inFlight: wire.in_flight,
    failed: wire.failed,
    paused: wire.paused,
    pauseReason: wire.pause_reason ?? undefined,
    databases: wire.databases,
  };
}

export interface SearchOptions {
  limit?: number;
  context?: boolean;
  documentId?: string;
  database?: string;
  folders?: string[];
}

export interface NewFolder {
  path: string;
  id?: string;
  database?: string;
  include?: string[];
  exclude?: string[];
}

export interface SearchService {
  search(query: string, options?: SearchOptions, signal?: AbortSignal): Promise<SearchResult>;
  folders(): Promise<IndexedFolder[]>;
  addFolder(folder: NewFolder): Promise<IndexedFolder>;
  updateFolder(id: string, patch: { include?: string[]; exclude?: string[] }): Promise<IndexedFolder>;
  removeFolder(id: string, keepDocuments: boolean): Promise<void>;
  status(): Promise<ServiceStatus>;
  failures(folderId?: string): Promise<Failure[]>;
  retry(jobId?: string, folderId?: string): Promise<string[]>;
}

export class ServiceError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ServiceError";
  }
}

export const SEARCH_TIMEOUT_MS = 15_000;
export const DEFAULT_TIMEOUT_MS = 5_000;

/** The search URL for a query: the per-document endpoint when a document is given. */
export function searchUrl(baseUrl: string, query: string, options: SearchOptions = {}): string {
  const base = baseUrl.replace(/\/+$/, "");
  const path = options.documentId ? `/documents/${encodeURIComponent(options.documentId)}/search` : "/search";
  const params = new URLSearchParams({ q: query });
  if (options.limit) params.set("limit", String(Math.max(1, Math.min(50, options.limit))));
  if (options.context) params.set("context", "true");
  if (options.database) params.append("database", options.database);
  for (const f of options.folders ?? []) params.append("folder", f);
  return `${base}${path}?${params.toString()}`;
}

type Fetch = typeof fetch;

export class HttpSearchService implements SearchService {
  constructor(
    private readonly baseUrl: string,
    private readonly token?: string,
    private readonly fetchImpl: Fetch = fetch,
  ) {}

  private async request<T>(
    path: string,
    init: RequestInit = {},
    timeoutMs = DEFAULT_TIMEOUT_MS,
    signal?: AbortSignal,
  ): Promise<T> {
    const url = path.startsWith("http") ? path : `${this.baseUrl.replace(/\/+$/, "")}${path}`;
    const timeout = AbortSignal.timeout(timeoutMs);
    const headers: Record<string, string> = { Accept: "application/json" };
    if (init.body) headers["Content-Type"] = "application/json";
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    let response: Response;
    try {
      response = await this.fetchImpl(url, {
        ...init,
        headers,
        signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      });
    } catch (error) {
      if (signal?.aborted) throw error;
      if (timeout.aborted) throw new ServiceError(`The search service did not answer within ${timeoutMs / 1000} s`);
      throw new ServiceError(
        `Cannot reach the search service at ${this.baseUrl}: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = (await response.json()) as { detail?: unknown };
        if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      } catch {
        // the body is not JSON; keep the status text
      }
      throw new ServiceError(detail || `HTTP ${response.status}`, response.status);
    }
    return (await response.json()) as T;
  }

  async search(query: string, options: SearchOptions = {}, signal?: AbortSignal): Promise<SearchResult> {
    const wire = await this.request<WireSearchResponse>(
      searchUrl(this.baseUrl, query, options),
      {},
      SEARCH_TIMEOUT_MS,
      signal,
    );
    return toSearchResult(wire);
  }

  async folders(): Promise<IndexedFolder[]> {
    const wire = await this.request<{ folders: WireFolder[] }>("/folders");
    return wire.folders.map(toFolder);
  }

  async addFolder(folder: NewFolder): Promise<IndexedFolder> {
    const body = Object.fromEntries(Object.entries(folder).filter(([, v]) => v !== undefined && v !== ""));
    return toFolder(await this.request<WireFolder>("/folders", { method: "POST", body: JSON.stringify(body) }));
  }

  async updateFolder(id: string, patch: { include?: string[]; exclude?: string[] }): Promise<IndexedFolder> {
    return toFolder(
      await this.request<WireFolder>(`/folders/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    );
  }

  async removeFolder(id: string, keepDocuments: boolean): Promise<void> {
    await this.request(`/folders/${encodeURIComponent(id)}?keep_documents=${keepDocuments}`, { method: "DELETE" });
  }

  async status(): Promise<ServiceStatus> {
    return toStatus(await this.request<WireStatus>("/status"));
  }

  async failures(folderId?: string): Promise<Failure[]> {
    const query = folderId ? `?folder=${encodeURIComponent(folderId)}` : "";
    const wire = await this.request<{ failures: WireFailure[] }>(`/failures${query}`);
    return wire.failures.map(toFailure);
  }

  async retry(jobId?: string, folderId?: string): Promise<string[]> {
    const path = jobId
      ? `/failures/${encodeURIComponent(jobId)}/retry`
      : `/failures/retry${folderId ? `?folder=${encodeURIComponent(folderId)}` : ""}`;
    const wire = await this.request<{ retried: string[] }>(path, { method: "POST" });
    return wire.retried;
  }
}
