import {
  Action,
  ActionPanel,
  Icon,
  LaunchType,
  launchCommand,
  showHUD,
  showToast,
  Toast,
  Keyboard,
} from "@raycast/api";
import { showFailureToast } from "@raycast/utils";
import { ReactNode } from "react";
import { basename } from "path";
import { formatCitation } from "../lib/citation";
import { fileKind } from "../lib/doctype";
import { findText } from "../lib/openers/plan";
import { openHit, openInBooks } from "../lib/openers/run";
import { quickLook } from "../lib/openers/quicklook";
import { openerSettings } from "../lib/preferences";
import { SearchHit } from "../lib/types";
import { AskForm } from "./Ask";
import { SearchView } from "./SearchView";

async function openAtPassage(hit: SearchHit, query: string) {
  const toast = await showToast({ style: Toast.Style.Animated, title: `Opening ${basename(hit.path)}…` });
  try {
    const outcome = await openHit(hit, openerSettings(), query);
    await toast.hide();
    await showHUD(outcome);
  } catch (error) {
    await toast.hide();
    await showFailureToast(error, { title: `Could not open ${basename(hit.path)}` });
  }
}

async function previewPage(hit: SearchHit) {
  try {
    await quickLook(hit);
  } catch (error) {
    await showFailureToast(error, { title: "Quick Look failed" });
  }
}

export function HitActions({
  hit,
  query = "",
  scoped,
  drillIn,
  extra,
}: {
  hit: SearchHit;
  query?: string;
  scoped?: boolean;
  /** The view of every passage in the hit's file. */
  drillIn?: ReactNode;
  extra?: ReactNode;
}) {
  const kind = fileKind(hit.path, hit.documentType);
  return (
    <ActionPanel>
      <ActionPanel.Section>
        <Action title="Open at Passage" icon={Icon.ArrowRight} onAction={() => openAtPassage(hit, query)} />
        {drillIn ? <Action.Push title="Show All in This File" icon={Icon.ChevronRight} target={drillIn} /> : null}
        <Action.ShowInFinder path={hit.path} shortcut={{ modifiers: ["cmd", "shift"], key: "r" }} />
        {scoped ? null : (
          <Action.Push
            title="Search in This Document"
            icon={Icon.MagnifyingGlass}
            shortcut={{ modifiers: ["cmd"], key: "f" }}
            target={
              <SearchView
                scope={{ documentId: hit.documentId, database: hit.database, title: hit.title || basename(hit.path) }}
              />
            }
          />
        )}
        <Action
          title={kind === "pdf" ? "Quick Look Page" : "Quick Look File"}
          icon={Icon.Eye}
          shortcut={Keyboard.Shortcut.Common.ToggleQuickLook}
          onAction={() => previewPage(hit)}
        />
        <Action.CopyToClipboard
          title="Copy Citation"
          content={formatCitation(hit)}
          shortcut={Keyboard.Shortcut.Common.Copy}
        />
        <Action.Push
          title="Ask About This Result"
          icon={Icon.QuestionMarkCircle}
          shortcut={{ modifiers: ["cmd", "shift"], key: "a" }}
          target={<AskForm hit={hit} />}
        />
      </ActionPanel.Section>
      <ActionPanel.Section>
        <Action.CopyToClipboard
          title="Copy Passage"
          content={hit.text || hit.snippet}
          shortcut={Keyboard.Shortcut.Common.CopyName}
        />
        <Action.CopyToClipboard
          title="Copy Path"
          content={hit.path}
          shortcut={{ modifiers: ["cmd", "opt"], key: "p" }}
        />
        <Action.Open
          title="Open in Default App"
          target={hit.path}
          icon={Icon.Window}
          shortcut={Keyboard.Shortcut.Common.Open}
        />
        {kind === "epub" ? (
          <Action
            title="Open in Books"
            icon={Icon.Book}
            onAction={async () => showHUD(await openInBooks(hit.path, findText(hit.text || hit.snippet)))}
          />
        ) : null}
        {extra}
        <Action
          title="Manage Indexed Folders"
          icon={Icon.Folder}
          onAction={() => launchCommand({ name: "manage-folders", type: LaunchType.UserInitiated })}
        />
      </ActionPanel.Section>
    </ActionPanel>
  );
}
