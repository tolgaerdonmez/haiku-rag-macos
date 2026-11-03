import {
  Action,
  ActionPanel,
  Alert,
  Color,
  Form,
  Icon,
  List,
  Toast,
  confirmAlert,
  showToast,
  useNavigation,
  Keyboard,
} from "@raycast/api";
import { showFailureToast, usePromise } from "@raycast/utils";
import { basename } from "path";
import { useMemo } from "react";
import { SearchService } from "./lib/api";
import { parsePatterns } from "./lib/patterns";
import { searchService } from "./lib/preferences";
import { Failure, IndexedFolder } from "./lib/types";

function tidyHome(path: string): string {
  const home = process.env.HOME;
  return home && path.startsWith(home) ? `~${path.slice(home.length)}` : path;
}

function FolderForm({
  service,
  folder,
  onDone,
}: {
  service: SearchService;
  folder?: IndexedFolder;
  onDone: () => void;
}) {
  const { pop } = useNavigation();
  return (
    <Form
      navigationTitle={folder ? `Edit ${basename(folder.path)}` : "Add Folder"}
      actions={
        <ActionPanel>
          <Action.SubmitForm
            title={folder ? "Save Patterns" : "Add Folder"}
            onSubmit={async (values: { paths?: string[]; id?: string; include?: string; exclude?: string }) => {
              const include = parsePatterns(values.include);
              const exclude = parsePatterns(values.exclude);
              const toast = await showToast({ style: Toast.Style.Animated, title: folder ? "Saving…" : "Adding…" });
              try {
                if (folder) {
                  await service.updateFolder(folder.id, { include, exclude });
                  toast.title = "Patterns saved";
                } else {
                  const path = values.paths?.[0];
                  if (!path) throw new Error("Choose a folder");
                  const added = await service.addFolder({ path, id: values.id?.trim() || undefined, include, exclude });
                  toast.title = `Indexing ${basename(added.path)}`;
                }
                toast.style = Toast.Style.Success;
                onDone();
                pop();
              } catch (error) {
                await toast.hide();
                await showFailureToast(error, { title: folder ? "Could not save" : "Could not add the folder" });
              }
            }}
          />
        </ActionPanel>
      }
    >
      {folder ? (
        <Form.Description title="Folder" text={tidyHome(folder.path)} />
      ) : (
        <>
          <Form.FilePicker
            id="paths"
            title="Folder"
            canChooseDirectories
            canChooseFiles={false}
            allowMultipleSelection={false}
          />
          <Form.TextField id="id" title="Name" placeholder="Derived from the folder name when empty" />
        </>
      )}
      <Form.TextArea
        id="include"
        title="Include"
        placeholder={"*.pdf\n*.md"}
        info="Gitignore-style patterns, one per line; *.pdf matches at any depth. Empty takes every supported file."
        defaultValue={folder?.include.join("\n")}
      />
      <Form.TextArea
        id="exclude"
        title="Exclude"
        placeholder="drafts/"
        info="Gitignore-style patterns, one per line; drafts/ skips every drafts folder."
        defaultValue={folder?.exclude.join("\n")}
      />
    </Form>
  );
}

function FailuresList({ service, folder }: { service: SearchService; folder?: IndexedFolder }) {
  const { data, isLoading, revalidate } = usePromise((id?: string) => service.failures(id), [folder?.id]);
  const retry = async (failure?: Failure) => {
    try {
      const retried = await service.retry(failure?.jobId, failure ? undefined : folder?.id);
      await showToast({ style: Toast.Style.Success, title: `Queued ${retried.length} again` });
      revalidate();
    } catch (error) {
      await showFailureToast(error, { title: "Retry failed" });
    }
  };
  return (
    <List isLoading={isLoading} navigationTitle={folder ? `Failures in ${basename(folder.path)}` : "Failed Files"}>
      <List.EmptyView icon={Icon.CheckCircle} title="No failed files" />
      {(data ?? []).map((f) => (
        <List.Item
          key={f.jobId}
          icon={{ source: Icon.XMarkCircle, tintColor: Color.Red }}
          title={f.path ? basename(f.path) : f.jobId}
          subtitle={f.reason}
          accessories={[{ text: `${f.attempts} ${f.attempts === 1 ? "attempt" : "attempts"}` }]}
          actions={
            <ActionPanel>
              <Action title="Retry" icon={Icon.ArrowClockwise} onAction={() => retry(f)} />
              <Action title="Retry All" icon={Icon.ArrowClockwise} onAction={() => retry()} />
              {f.path ? <Action.ShowInFinder path={f.path} /> : null}
              <Action.CopyToClipboard title="Copy Reason" content={f.reason} />
            </ActionPanel>
          }
        />
      ))}
    </List>
  );
}

export default function Command() {
  const service = useMemo(() => searchService(), []);
  const folders = usePromise(() => service.folders());
  const status = usePromise(() => service.status());
  const reload = () => {
    folders.revalidate();
    status.revalidate();
  };

  const remove = async (folder: IndexedFolder, keepDocuments: boolean) => {
    const confirmed = await confirmAlert({
      title: keepDocuments
        ? `Stop watching ${basename(folder.path)}?`
        : `Remove ${basename(folder.path)} from the index?`,
      message: keepDocuments
        ? "Its documents stay searchable but are no longer updated."
        : "Its documents are deleted from the index. The files themselves are not touched.",
      primaryAction: { title: keepDocuments ? "Stop Watching" : "Remove", style: Alert.ActionStyle.Destructive },
    });
    if (!confirmed) return;
    try {
      await service.removeFolder(folder.id, keepDocuments);
      await showToast({ style: Toast.Style.Success, title: `Removed ${basename(folder.path)}` });
      reload();
    } catch (error) {
      await showFailureToast(error, { title: "Could not remove the folder" });
    }
  };

  const s = status.data;
  const statusText = s
    ? s.paused
      ? `Paused: ${s.pauseReason ?? "held back"}`
      : s.indexing
        ? `Indexing · ${s.inFlight} in progress, ${s.queued} queued`
        : "Idle"
    : status.error
      ? "Service not reachable"
      : "…";

  const addAction = (
    <Action.Push
      title="Add Folder"
      icon={Icon.Plus}
      shortcut={Keyboard.Shortcut.Common.New}
      target={<FolderForm service={service} onDone={reload} />}
    />
  );

  return (
    <List isLoading={folders.isLoading || status.isLoading} searchBarPlaceholder="Filter folders…">
      {folders.error ? (
        <List.EmptyView
          icon={Icon.Warning}
          title="The search service did not answer"
          description={folders.error.message}
          actions={
            <ActionPanel>
              <Action title="Try Again" icon={Icon.ArrowClockwise} onAction={reload} />
            </ActionPanel>
          }
        />
      ) : (
        <List.EmptyView
          icon={Icon.Folder}
          title="No folders are indexed"
          actions={<ActionPanel>{addAction}</ActionPanel>}
        />
      )}
      {s ? (
        <List.Section title="Service">
          <List.Item
            icon={{
              source: s.paused ? Icon.Pause : s.indexing ? Icon.CircleProgress50 : Icon.CheckCircle,
              tintColor: s.paused ? Color.Orange : Color.Green,
            }}
            title={statusText}
            accessories={[
              { text: `${s.databases.reduce((n, d) => n + d.documents, 0)} documents` },
              ...(s.failed > 0 ? [{ tag: { value: `${s.failed} failed`, color: Color.Red } }] : []),
            ]}
            actions={
              <ActionPanel>
                <Action.Push
                  title="Show Failed Files"
                  icon={Icon.XMarkCircle}
                  target={<FailuresList service={service} />}
                />
                {addAction}
                <Action
                  title="Refresh"
                  icon={Icon.ArrowClockwise}
                  shortcut={Keyboard.Shortcut.Common.Refresh}
                  onAction={reload}
                />
              </ActionPanel>
            }
          />
        </List.Section>
      ) : null}
      <List.Section title="Indexed Folders">
        {(folders.data ?? []).map((folder) => {
          const st = folder.status;
          const accessories: List.Item.Accessory[] = [];
          if (st) {
            accessories.push({ text: `${st.documents} docs` });
            if (st.queued > 0) accessories.push({ tag: { value: `${st.queued} queued`, color: Color.Blue } });
            if (st.failed > 0) accessories.push({ tag: { value: `${st.failed} failed`, color: Color.Red } });
            accessories.push({
              icon: st.watching ? { source: Icon.Eye, tintColor: Color.Green } : { source: Icon.EyeDisabled },
              tooltip: st.watching ? "Watching" : "Not watching",
            });
          } else {
            accessories.push({ tag: { value: "starting", color: Color.SecondaryText } });
          }
          return (
            <List.Item
              key={folder.id}
              icon={Icon.Folder}
              title={basename(folder.path)}
              subtitle={tidyHome(folder.path)}
              keywords={[folder.id, folder.database]}
              accessories={accessories}
              actions={
                <ActionPanel>
                  <ActionPanel.Section>
                    <Action.Push
                      title="Edit Patterns"
                      icon={Icon.Pencil}
                      target={<FolderForm service={service} folder={folder} onDone={reload} />}
                    />
                    <Action.Push
                      title="Show Failed Files"
                      icon={Icon.XMarkCircle}
                      target={<FailuresList service={service} folder={folder} />}
                    />
                    <Action.ShowInFinder path={folder.path} />
                    {addAction}
                  </ActionPanel.Section>
                  <ActionPanel.Section>
                    <Action
                      title="Stop Watching, Keep Documents"
                      icon={Icon.EyeDisabled}
                      onAction={() => remove(folder, true)}
                    />
                    <Action
                      title="Remove from Index"
                      icon={Icon.Trash}
                      style={Action.Style.Destructive}
                      shortcut={{ modifiers: ["ctrl"], key: "x" }}
                      onAction={() => remove(folder, false)}
                    />
                    <Action
                      title="Refresh"
                      icon={Icon.ArrowClockwise}
                      shortcut={Keyboard.Shortcut.Common.Refresh}
                      onAction={reload}
                    />
                  </ActionPanel.Section>
                </ActionPanel>
              }
            />
          );
        })}
      </List.Section>
    </List>
  );
}
