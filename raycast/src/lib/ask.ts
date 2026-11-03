import { basename } from "path";
import { pageLabel, headingTrail } from "./citation";
import { SearchHit } from "./types";

export const OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions";
export const MAX_ANSWER_TOKENS = 700;
/** USD per token charged when OpenRouter reports no cost: above the price of the small models this targets. */
export const FALLBACK_USD_PER_TOKEN = 2 / 1_000_000;

export interface SpendLedger {
  month: string;
  spentUsd: number;
}

export function monthKey(date: Date): string {
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
}

/** This month's ledger; a ledger from an earlier month starts over at zero. */
export function currentLedger(stored: SpendLedger | undefined, now: Date): SpendLedger {
  const month = monthKey(now);
  return stored && stored.month === month ? stored : { month, spentUsd: 0 };
}

export function parseCap(value: string | undefined): number {
  const cap = Number((value ?? "").trim().replace(",", "."));
  return Number.isFinite(cap) && cap >= 0 ? cap : 1;
}

export function capRemaining(ledger: SpendLedger, capUsd: number): number {
  return Math.max(0, capUsd - ledger.spentUsd);
}

export interface Usage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  cost?: number;
}

export function costOf(usage: Usage | undefined): number {
  if (usage && typeof usage.cost === "number" && Number.isFinite(usage.cost)) return usage.cost;
  const tokens = usage?.total_tokens ?? (usage?.prompt_tokens ?? 0) + (usage?.completion_tokens ?? 0);
  return tokens > 0 ? tokens * FALLBACK_USD_PER_TOKEN : MAX_ANSWER_TOKENS * 4 * FALLBACK_USD_PER_TOKEN;
}

export function sourceBlock(hit: SearchHit): string {
  const lines = [`File: ${basename(hit.path)}`];
  const page = pageLabel(hit);
  if (page) lines.push(`Location: ${page}`);
  const trail = headingTrail(hit);
  if (trail) lines.push(`Section: ${trail}`);
  lines.push("", hit.context || hit.text || hit.snippet);
  return lines.join("\n");
}

export interface ChatMessage {
  role: "system" | "user";
  content: string;
}

export function askMessages(hit: SearchHit, question: string): ChatMessage[] {
  return [
    {
      role: "system",
      content:
        "You answer questions about one passage from the user's own files. Use only the passage. " +
        "If it does not contain the answer, say so in one sentence. Be brief; quote the passage where it helps.",
    },
    { role: "user", content: `Passage:\n"""\n${sourceBlock(hit)}\n"""\n\nQuestion: ${question}` },
  ];
}

export function askRequestBody(model: string, messages: ChatMessage[]): Record<string, unknown> {
  return {
    model,
    messages,
    max_tokens: MAX_ANSWER_TOKENS,
    temperature: 0.2,
    usage: { include: true },
  };
}

export interface AskResult {
  answer: string;
  costUsd: number;
  model: string;
}

export async function askOpenRouter(
  apiKey: string,
  model: string,
  messages: ChatMessage[],
  signal?: AbortSignal,
  fetchImpl: typeof fetch = fetch,
): Promise<AskResult> {
  const response = await fetchImpl(OPENROUTER_URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
      "X-Title": "haiku.rag Raycast",
    },
    body: JSON.stringify(askRequestBody(model, messages)),
    signal,
  });
  const body = (await response.json().catch(() => ({}))) as {
    choices?: { message?: { content?: string } }[];
    usage?: Usage;
    model?: string;
    error?: { message?: string };
  };
  if (!response.ok) {
    throw new Error(body.error?.message || `OpenRouter answered HTTP ${response.status}`);
  }
  return {
    answer: body.choices?.[0]?.message?.content?.trim() || "(no answer)",
    costUsd: costOf(body.usage),
    model: body.model || model,
  };
}
