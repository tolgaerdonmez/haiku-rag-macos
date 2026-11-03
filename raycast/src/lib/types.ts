export type CoordOrigin = "TOPLEFT" | "BOTTOMLEFT";

/** A passage's box on one page, in the page's own coordinate space. */
export interface BoundingBox {
  page: number;
  left: number;
  top: number;
  right: number;
  bottom: number;
  coordOrigin: CoordOrigin;
  pageWidth: number;
  pageHeight: number;
}

export interface SearchHit {
  id: string;
  documentId: string;
  database?: string;
  folderId?: string;
  path: string;
  title?: string;
  documentType: string;
  pages: number[];
  headings: string[];
  labels: string[];
  snippet: string;
  /** `[start, end)` offsets into `snippet` of query words found in it. */
  highlights?: [number, number][];
  /** The whole matched passage. */
  text: string;
  /** The passage expanded to its section, when requested. */
  context?: string;
  score: number;
  boxes: BoundingBox[];
  /** 1-based line of the passage, when the service knows it. */
  line?: number;
}

export interface SearchResult {
  query: string;
  hits: SearchHit[];
  tookMs?: number;
  /** Hits the client could not open, such as documents without a local file. */
  skipped: number;
}

export interface FolderStatus {
  watching: boolean;
  documents: number;
  queued: number;
  failed: number;
  lastScanAt?: string;
}

export interface IndexedFolder {
  id: string;
  database: string;
  path: string;
  include: string[];
  exclude: string[];
  status?: FolderStatus;
}

export interface Failure {
  jobId: string;
  folderId?: string;
  database?: string;
  path?: string;
  reason: string;
  attempts: number;
  failedAt?: string;
}

export interface ServiceStatus {
  indexing: boolean;
  queued: number;
  inFlight: number;
  failed: number;
  paused: boolean;
  pauseReason?: string;
  databases: { name: string; documents: number }[];
}

export interface DocumentGroup {
  key: string;
  path: string;
  title: string;
  documentType: string;
  hits: SearchHit[];
}
