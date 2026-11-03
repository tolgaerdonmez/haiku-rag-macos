import { environment, getPreferenceValues } from "@raycast/api";
import { existsSync } from "fs";
import { homedir } from "os";
import { join } from "path";
import { HttpSearchService, SearchService } from "./api";
import { MockSearchService } from "./mock";
import { OpenerSettings } from "./openers/run";

export interface Preferences {
  serviceUrl?: string;
  serviceToken?: string;
  useMockData?: boolean;
  resultLimit?: string;
  rexPath?: string;
  rexSession?: string;
  editorCommand?: string;
  openRouterApiKey?: string;
  askModel?: string;
  monthlySpendCapUsd?: string;
}

export const DEFAULT_SERVICE_URL = "http://127.0.0.1:8765";
/** Where Rex installs its CLI; Raycast runs without the login shell's PATH. */
export function rexCandidates(home = homedir()): string[] {
  return [
    join(
      home,
      "Library/Application Support/com.superlogical.rex/RexServer/Versions/Current/Rex Server.app/Contents/MacOS/rex",
    ),
    join(home, ".local/bin/rex"),
    "/opt/homebrew/bin/rex",
    "/usr/local/bin/rex",
  ];
}

export function resolveRex(configured: string | undefined): string {
  if (configured?.trim()) return configured.trim();
  return rexCandidates().find((p) => existsSync(p)) ?? rexCandidates()[0];
}

export function preferences(): Preferences {
  return getPreferenceValues<Preferences>();
}

export function searchService(prefs: Preferences = preferences()): SearchService {
  if (prefs.useMockData) return new MockSearchService(join(environment.assetsPath, "samples"));
  return new HttpSearchService(
    prefs.serviceUrl?.trim() || DEFAULT_SERVICE_URL,
    prefs.serviceToken?.trim() || undefined,
  );
}

export function resultLimit(prefs: Preferences = preferences()): number {
  const n = Number(prefs.resultLimit);
  return Number.isInteger(n) && n > 0 ? Math.min(n, 50) : 50;
}

export function openerSettings(prefs: Preferences = preferences()): OpenerSettings {
  return {
    rexPath: resolveRex(prefs.rexPath),
    rexSession: prefs.rexSession?.trim() || "haiku",
    editorCommand: prefs.editorCommand?.trim() || "nvim",
  };
}
