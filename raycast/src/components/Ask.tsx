import {
  Action,
  ActionPanel,
  Detail,
  Form,
  Icon,
  LocalStorage,
  openExtensionPreferences,
  useNavigation,
} from "@raycast/api";
import { usePromise } from "@raycast/utils";
import { useRef } from "react";
import { basename } from "path";
import { SpendLedger, askMessages, askOpenRouter, capRemaining, currentLedger, parseCap } from "../lib/ask";
import { formatCitation, pageLabel } from "../lib/citation";
import { preferences } from "../lib/preferences";
import { SearchHit } from "../lib/types";

const LEDGER_KEY = "openrouter-spend";
const DEFAULT_MODEL = "google/gemini-2.5-flash-lite";

async function loadLedger(): Promise<SpendLedger> {
  const raw = await LocalStorage.getItem<string>(LEDGER_KEY);
  let stored: SpendLedger | undefined;
  try {
    stored = raw ? (JSON.parse(raw) as SpendLedger) : undefined;
  } catch {
    stored = undefined;
  }
  return currentLedger(stored, new Date());
}

async function record(costUsd: number): Promise<SpendLedger> {
  const ledger = await loadLedger();
  const next = { ...ledger, spentUsd: ledger.spentUsd + costUsd };
  await LocalStorage.setItem(LEDGER_KEY, JSON.stringify(next));
  return next;
}

function usd(value: number): string {
  return `$${value.toFixed(value < 0.01 ? 4 : 2)}`;
}

class CapReached extends Error {}

function Answer({ hit, question }: { hit: SearchHit; question: string }) {
  const prefs = preferences();
  const cap = parseCap(prefs.monthlySpendCapUsd);
  const model = prefs.askModel?.trim() || DEFAULT_MODEL;
  const abortable = useRef<AbortController>(null);
  const { data, isLoading, error } = usePromise(
    async () => {
      if (!prefs.openRouterApiKey) throw new Error("Set your OpenRouter API key in the extension preferences.");
      const before = await loadLedger();
      if (capRemaining(before, cap) <= 0) {
        throw new CapReached(`This month's spending cap of ${usd(cap)} is reached (${usd(before.spentUsd)} spent).`);
      }
      const result = await askOpenRouter(
        prefs.openRouterApiKey,
        model,
        askMessages(hit, question),
        abortable.current?.signal,
      );
      const after = await record(result.costUsd);
      return { ...result, ledger: after };
    },
    [],
    { abortable, failureToastOptions: { title: "Ask failed" } },
  );

  const source = `${basename(hit.path)}${pageLabel(hit) ? `, ${pageLabel(hit)}` : ""}`;
  const markdown = error
    ? `## Could not ask\n\n${error.message}`
    : data
      ? `**${question}**\n\n${data.answer}\n\n---\n\n_From ${source}._`
      : `**${question}**\n\nAsking ${model}…`;

  return (
    <Detail
      isLoading={isLoading}
      markdown={markdown}
      navigationTitle="Ask About This Result"
      metadata={
        <Detail.Metadata>
          <Detail.Metadata.Label title="Model" text={data?.model ?? model} />
          {data ? <Detail.Metadata.Label title="This answer" text={usd(data.costUsd)} /> : null}
          {data ? (
            <Detail.Metadata.Label title="Spent this month" text={`${usd(data.ledger.spentUsd)} of ${usd(cap)}`} />
          ) : null}
          <Detail.Metadata.Label title="Source" text={source} />
        </Detail.Metadata>
      }
      actions={
        <ActionPanel>
          {data ? <Action.CopyToClipboard title="Copy Answer" content={data.answer} /> : null}
          <Action.CopyToClipboard
            title="Copy Answer with Citation"
            content={`${data?.answer ?? ""}\n\n${formatCitation(hit)}`}
          />
          <Action title="Open Extension Preferences" icon={Icon.Gear} onAction={openExtensionPreferences} />
        </ActionPanel>
      }
    />
  );
}

export function AskForm({ hit }: { hit: SearchHit }) {
  const { push } = useNavigation();
  const { data: ledger } = usePromise(loadLedger);
  const cap = parseCap(preferences().monthlySpendCapUsd);
  return (
    <Form
      navigationTitle="Ask About This Result"
      actions={
        <ActionPanel>
          <Action.SubmitForm
            title="Ask"
            icon={Icon.QuestionMarkCircle}
            onSubmit={(values: { question: string }) => {
              if (values.question.trim()) push(<Answer hit={hit} question={values.question.trim()} />);
            }}
          />
          <Action title="Open Extension Preferences" icon={Icon.Gear} onAction={openExtensionPreferences} />
        </ActionPanel>
      }
    >
      <Form.Description title="Passage" text={formatCitation(hit).split("\n")[0]} />
      <Form.TextArea id="question" title="Question" placeholder="What does this passage say about…?" autoFocus />
      <Form.Description
        title="Budget"
        text={ledger ? `${usd(ledger.spentUsd)} of ${usd(cap)} spent this month` : `Cap ${usd(cap)} a month`}
      />
    </Form>
  );
}
