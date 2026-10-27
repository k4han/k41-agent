import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import {
  BookOpen,
  Download,
  FileText,
  Folder,
  Plus,
  RefreshCw,
  Save,
  Search,
  Sliders,
  Trash2,
  TriangleAlert,
  Upload,
} from "lucide-solid";
import { Dialog } from "@/components/Dialog";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Markdown } from "@/components/Markdown";
import { useToast } from "@/components/Toast";
import {
  WorkspaceSelector,
  type WorkspaceSelectionDraft,
} from "@/components/WorkspaceSelector";
import {
  apiFetch,
  postJson,
  putJson,
  fetchWithCsrf,
  readError,
} from "@/lib/api";
import type { WorkspaceRef } from "@/types";
import type {
  SkillDiagnostic as Diagnostic,
  SkillPackageSummary as Package,
  SkillPackageDetail as Detail,
  SkillEntry as Entry,
  SkillFileDetail as FileDetail,
} from "@/lib/skillPackages";
import { SettingsLayout } from "./SettingsLayout";

type Preview = {
  preview_id: string;
  commit: string | null;
  skills: {
    name: string;
    description: string;
    conflict: boolean;
    diagnostics: Diagnostic[];
  }[];
};
const base = "/dashboard-api/skill-packages";

export function SkillsPage() {
  const { showToast } = useToast();
  const [packages, setPackages] = createSignal<Package[]>([]);
  const [scope, setScope] = createSignal("global");
  const [workspace, setWorkspace] = createSignal<WorkspaceRef | null>(null);
  const [defaultDirectory, setDefaultDirectory] = createSignal("");
  const [search, setSearch] = createSignal("");
  const [busy, setBusy] = createSignal(false);
  const [error, setError] = createSignal("");
  const [detail, setDetail] = createSignal<Detail | null>(null);
  const [mode, setMode] = createSignal("form");
  const [raw, setRaw] = createSignal("");
  const [body, setBody] = createSignal("");
  const [description, setDescription] = createSignal("");
  const [compatibility, setCompatibility] = createSignal("");
  const [metadata, setMetadata] = createSignal("{}");
  const [diagnostics, setDiagnostics] = createSignal<Diagnostic[]>([]);
  const [directory, setDirectory] = createSignal("");
  const [entries, setEntries] = createSignal<Entry[]>([]);
  const [nextOffset, setNextOffset] = createSignal<number | null>(null);
  const [file, setFile] = createSignal<FileDetail | null>(null);
  const [fileText, setFileText] = createSignal("");
  const [entryPath, setEntryPath] = createSignal("");
  const [renamePath, setRenamePath] = createSignal("");
  const [packageName, setPackageName] = createSignal("");
  const [deleteTarget, setDeleteTarget] = createSignal<Package | null>(null);
  const [entryDeleteTarget, setEntryDeleteTarget] =
    createSignal<FileDetail | null>(null);
  const [createOpen, setCreateOpen] = createSignal(false);
  const [newName, setNewName] = createSignal("my-skill");
  const [newDocument, setNewDocument] = createSignal(
    "---\nname: my-skill\ndescription: Describe when to use this skill.\n---\n# Instructions\n",
  );
  const [importOpen, setImportOpen] = createSignal(false);
  const [importKind, setImportKind] = createSignal("zip");
  const [importPath, setImportPath] = createSignal("");
  const [importRef, setImportRef] = createSignal("HEAD");
  const [subdirectory, setSubdirectory] = createSignal("");
  const [installationId, setInstallationId] = createSignal("");
  const [zipFile, setZipFile] = createSignal<File | null>(null);
  const [preview, setPreview] = createSignal<Preview | null>(null);
  const [overwrite, setOverwrite] = createSignal(false);
  const [repositoryDirectory, setRepositoryDirectory] =
    createSignal(".agent/skills");
  const [additionalRoots, setAdditionalRoots] = createSignal("");
  const [localExecutionMode, setLocalExecutionMode] = createSignal("snapshot");
  const [cacheRoot, setCacheRoot] = createSignal("");
  const query = () =>
    workspace()
      ? `workspace=${encodeURIComponent(JSON.stringify(workspace()))}`
      : "";
  const url = (path: string, extra = "") =>
    `${base}${path}?${[query(), extra].filter(Boolean).join("&")}`;
  const fileUrl = (path: string, download = false) =>
    url(
      `/${detail()!.id}/files/${path.split("/").map(encodeURIComponent).join("/")}`,
      download ? "download=true" : "",
    );
  const shown = createMemo(() =>
    packages().filter((item) =>
      `${item.name} ${item.description} ${item.source.directory}`
        .toLowerCase()
        .includes(search().toLowerCase()),
    ),
  );
  const execute = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await operation();
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Skill operation failed";
      setError(message);
      showToast(message, "error");
    } finally {
      setBusy(false);
    }
  };
  const load = async () => {
    setPackages(
      (await apiFetch<{ packages: Package[] }>(url("", `scope=${scope()}`)))
        .packages,
    );
  };
  const selectWorkspace = async (draft: WorkspaceSelectionDraft) =>
    execute(async () => {
      const response = await postJson<{
        workspace: { execution: WorkspaceRef };
      }>("/dashboard-api/workspace/resolve", {
        kind: draft.source === "github" ? "github" : draft.backend,
        backend: draft.backend,
        locator: draft.source === "sandbox" ? draft.sandboxId : draft.localPath,
        repository_id: draft.repositoryId,
      });
      setWorkspace(response.workspace.execution);
      await load();
    });
  const browse = async (path: string, offset = 0) => {
    const response = await apiFetch<{
      entries: Entry[];
      next_offset: number | null;
    }>(
      url(
        `/${detail()!.id}/files`,
        `path=${encodeURIComponent(path)}&offset=${offset}`,
      ),
    );
    setDirectory(path);
    setEntries(offset ? [...entries(), ...response.entries] : response.entries);
    setNextOffset(response.next_offset);
  };
  const open = async (item: Package) =>
    execute(async () => {
      const current = (await apiFetch<{ package: Detail }>(url(`/${item.id}`)))
        .package;
      setDetail(current);
      setMode(current.valid === false ? "raw" : "form");
      setRaw(current.content);
      setBody(current.body);
      setDescription(String(current.frontmatter.description || ""));
      setCompatibility(String(current.frontmatter.compatibility || ""));
      setMetadata(JSON.stringify(current.frontmatter.metadata || {}, null, 2));
      setDiagnostics(current.diagnostics);
      setPackageName(item.name);
      setFile(null);
      await browse("");
    });
  const openFile = async (entry: Entry) =>
    execute(async () => {
      if (entry.kind === "directory") {
        await browse(entry.path);
        setFile(null);
        return;
      }
      const current = await apiFetch<FileDetail>(fileUrl(entry.path));
      setFile(current);
      setFileText(current.content || "");
      setRenamePath(entry.path);
    });
  const writeFile = async (path: string, data: BodyInit, version?: string) => {
    const response = await fetchWithCsrf(fileUrl(path), {
      method: "PUT",
      headers: version ? { "If-Match": version } : {},
      body: data,
    });
    if (!response.ok) throw new Error(await readError(response));
    await browse(directory());
  };
  const mutateFile = async (
    action: string,
    path: string,
    destination = "",
    version?: string,
  ) => {
    await postJson(url(`/${detail()!.id}/files`), {
      action,
      path,
      destination,
      expected_version: version,
    });
    await browse(directory());
  };
  const saveDocument = async () =>
    execute(async () => {
      const current = detail()!;
      if (mode() === "raw") {
        const validation = await postJson<{
          valid: boolean;
          diagnostics: Diagnostic[];
        }>(`${base}/validate`, {
          content: raw(),
          name: current.diagnostics.some(
            (item) =>
              item.code === "invalid_name" || item.code === "invalid_document",
          )
            ? undefined
            : current.name,
          strict: true,
        });
        setDiagnostics(validation.diagnostics);
        if (!validation.valid)
          throw new Error(
            validation.diagnostics[0]?.message || "Invalid document",
          );
        await writeFile("SKILL.md", raw(), current.file_version);
      } else
        await putJson(url(`/${current.id}/document`), {
          expected_version: current.file_version,
          frontmatter: {
            ...current.frontmatter,
            description: description(),
            compatibility: compatibility(),
            metadata: JSON.parse(metadata()),
          },
          body: body(),
        });
      const updated = (
        await apiFetch<{ package: Detail }>(url(`/${current.id}`))
      ).package;
      setDetail(updated);
      setRaw(updated.content);
      setDiagnostics(updated.diagnostics);
      await load();
      showToast("Skill document saved.");
    });
  const switchMode = async (next: string) =>
    execute(async () => {
      if (mode() === "form") {
        const rendered = await postJson<{ content: string }>(`${base}/render`, {
          content: raw(),
          frontmatter: {
            description: description(),
            compatibility: compatibility(),
            metadata: JSON.parse(metadata()),
          },
          body: body(),
        });
        setRaw(rendered.content);
      } else if (mode() === "raw") {
        const parsed = await postJson<{
          valid: boolean;
          frontmatter: Record<string, unknown>;
          body: string;
          diagnostics: Diagnostic[];
        }>(`${base}/validate`, { content: raw(), name: detail()!.name });
        setDiagnostics(parsed.diagnostics);
        if (!parsed.valid)
          throw new Error(parsed.diagnostics[0]?.message || "Invalid document");
        setDescription(String(parsed.frontmatter.description || ""));
        setCompatibility(String(parsed.frontmatter.compatibility || ""));
        setMetadata(JSON.stringify(parsed.frontmatter.metadata || {}, null, 2));
        setBody(parsed.body);
        setDetail({
          ...detail()!,
          content: raw(),
          frontmatter: parsed.frontmatter,
          body: parsed.body,
        });
      }
      setMode(next);
    });
  const previewImport = async () =>
    execute(async () => {
      const options = {
        kind: importKind(),
        path: importPath(),
        url: importPath(),
        ref: importRef(),
        subdirectory: subdirectory(),
        installation_id: installationId() ? Number(installationId()) : null,
        scope: scope(),
        workspace: workspace(),
      };
      if (importKind() === "zip") {
        if (!zipFile()) throw new Error("Choose a ZIP file.");
        const form = new FormData();
        form.set("file", zipFile()!);
        form.set("options", JSON.stringify(options));
        setPreview(
          await apiFetch<Preview>(`${base}/imports/preview`, {
            method: "POST",
            body: form,
          }),
        );
      } else
        setPreview(await postJson<Preview>(`${base}/imports/preview`, options));
    });
  onMount(
    () =>
      void execute(async () => {
        const defaults = await apiFetch<{
          workspace: { execution: WorkspaceRef } | null;
        }>("/dashboard-api/workspace/default");
        if (defaults.workspace?.execution) {
          setWorkspace(defaults.workspace.execution);
          setDefaultDirectory(defaults.workspace.execution.locator);
        }
        const settings = await apiFetch<{
          settings: Record<string, { value: unknown }>;
        }>("/dashboard-api/skills");
        setRepositoryDirectory(
          String(
            settings.settings["skills.repository_dir"]?.value ||
              ".agent/skills",
          ),
        );
        setAdditionalRoots(
          (
            (settings.settings["skills.additional_roots"]?.value ||
              []) as string[]
          ).join("\n"),
        );
        setLocalExecutionMode(
          String(
            settings.settings["skills.local_execution_mode"]?.value ||
              "snapshot",
          ),
        );
        setCacheRoot(
          String(settings.settings["skills.cache_root"]?.value || ""),
        );
        await load();
      }),
  );

  return (
    <SettingsLayout
      title="Skills"
      breadcrumbLabel="Skills"
      contentWidth="wide"
      actions={
        <button
          class="btn"
          disabled={busy()}
          onClick={() => void execute(load)}
        >
          <RefreshCw size={14} class={busy() ? "animate-spin" : ""} /> Reload
        </button>
      }
    >
      <div class="stack">
        <Show when={error()}>
          <div
            class="panel"
            role="alert"
            style={{
              display: "flex",
              "align-items": "center",
              gap: "10px",
              padding: "12px 16px",
              "border-color":
                "color-mix(in srgb, var(--danger) 40%, var(--border))",
              background:
                "color-mix(in srgb, var(--danger) 8%, var(--surface))",
              color: "var(--danger)",
            }}
          >
            <TriangleAlert size={16} style={{ "flex-shrink": 0 }} />
            <span>{error()}</span>
          </div>
        </Show>
        <section class="panel">
          <div class="panel-body stack">
            <div class="skills-toolbar">
              <div class="skills-toolbar-filters">
                <select
                  class="select"
                  aria-label="Skill scope"
                  value={scope()}
                  disabled={busy()}
                  onChange={(event) => {
                    setScope(event.currentTarget.value);
                    void execute(load);
                  }}
                  style={{ width: "auto", "min-width": "150px" }}
                >
                  <option value="global">Shared skills</option>
                  <option value="project">Workspace skills</option>
                </select>
                <Show when={scope() === "project"}>
                  <WorkspaceSelector
                    workingDir={workspace()?.locator || ""}
                    defaultWorkingDir={defaultDirectory()}
                    workspace={workspace()}
                    locked={false}
                    disabled={busy()}
                    onSelectionChange={(draft) => void selectWorkspace(draft)}
                  />
                </Show>
              </div>
              <div class="skills-toolbar-search">
                <div class="settings-resource-search" style={{ width: "100%" }}>
                  <Search size={15} />
                  <input
                    class="input"
                    type="search"
                    aria-label="Search skills"
                    placeholder="Search skills by name, description, or path..."
                    value={search()}
                    onInput={(event) => setSearch(event.currentTarget.value)}
                  />
                </div>
              </div>
              <div class="skills-toolbar-actions">
                <button
                  class="btn btn-primary"
                  disabled={busy()}
                  onClick={() => setCreateOpen(true)}
                >
                  <Plus size={14} /> New skill
                </button>
                <button
                  class="btn"
                  disabled={busy()}
                  onClick={() => {
                    setPreview(null);
                    setOverwrite(false);
                    setImportOpen(true);
                  }}
                >
                  <Upload size={14} /> Import
                </button>
              </div>
            </div>
          </div>
        </section>
        <section class="panel">
          <div class="panel-body stack">
            <div
              class="settings-section-header"
              style={{ "margin-bottom": "4px" }}
            >
              <h3
                class="settings-section-title"
                style={{
                  display: "inline-flex",
                  "align-items": "center",
                  gap: "8px",
                  margin: 0,
                  "font-size": "0.9375rem",
                }}
              >
                <Sliders size={15} />
                Environment & Storage Configuration
              </h3>
            </div>
            <div class="field">
              <label>Repository skills directory</label>
              <input
                class="input"
                value={repositoryDirectory()}
                disabled={busy()}
                onInput={(event) =>
                  setRepositoryDirectory(event.currentTarget.value)
                }
              />
            </div>
            <div class="field">
              <label>Additional shared skill directories</label>
              <textarea
                class="textarea"
                rows={2}
                disabled={busy()}
                value={additionalRoots()}
                onInput={(event) =>
                  setAdditionalRoots(event.currentTarget.value)
                }
                placeholder="One directory per line"
              />
            </div>
            <div class="field">
              <label for="local-skill-execution">Local execution</label>
              <select
                class="select"
                id="local-skill-execution"
                disabled={busy()}
                value={localExecutionMode()}
                onChange={(event) =>
                  setLocalExecutionMode(event.currentTarget.value)
                }
              >
                <option value="snapshot">
                  Shared snapshot (pinned version)
                </option>
                <option value="source">Source (live local files)</option>
              </select>
            </div>
            <div class="field">
              <label for="skill-cache-root">
                Local execution cache directory
              </label>
              <input
                class="input"
                id="skill-cache-root"
                disabled={busy()}
                value={cacheRoot()}
                onInput={(event) => setCacheRoot(event.currentTarget.value)}
                placeholder="Automatic short directory in the managed user folder"
              />
            </div>
            <div>
              <button
                class="btn btn-primary"
                disabled={busy()}
                onClick={() =>
                  void execute(async () => {
                    await putJson("/settings", {
                      values: {
                        "skills.repository_dir": repositoryDirectory(),
                        "skills.local_execution_mode": localExecutionMode(),
                        "skills.cache_root": cacheRoot(),
                        "skills.additional_roots": additionalRoots()
                          .split("\n")
                          .map((value) => value.trim())
                          .filter(Boolean),
                      },
                    });
                    await load();
                    showToast("Sources saved.");
                  })
                }
              >
                <Save size={14} /> Save sources
              </button>
            </div>
          </div>
        </section>
        <Show
          when={shown().length}
          fallback={
            <div class="panel">
              <div
                class="panel-body"
                style={{ padding: "32px 16px", "text-align": "center" }}
              >
                <div
                  style={{
                    display: "flex",
                    "flex-direction": "column",
                    "align-items": "center",
                    gap: "12px",
                  }}
                >
                  <BookOpen size={32} style={{ color: "var(--muted-2)" }} />
                  <div style={{ "font-weight": 600, color: "var(--fg)" }}>
                    No skills found in this scope.
                  </div>
                  <button
                    class="btn btn-primary"
                    disabled={busy()}
                    onClick={() => setCreateOpen(true)}
                  >
                    <Plus size={14} /> New skill
                  </button>
                </div>
              </div>
            </div>
          }
        >
          <For each={shown()}>
            {(item) => (
              <section class="panel skill-card">
                <div class="panel-body stack">
                  <div class="skill-card-header">
                    <div class="skill-card-title-group">
                      <strong class="skill-card-title">
                        <BookOpen size={16} />
                        {item.name}
                      </strong>
                      <span
                        class={`badge ${item.source.scope === "project" ? "badge-info" : ""}`}
                      >
                        {item.source.scope === "project"
                          ? "Workspace"
                          : "Shared"}
                      </span>
                      <Show when={item.shadowed}>
                        <span
                          class="badge badge-warning"
                          title="Shadowed by workspace skill with same name"
                        >
                          Shadowed
                        </span>
                      </Show>
                      <Show when={!item.enabled}>
                        <span class="badge badge-danger">
                          Disabled or invalid
                        </span>
                      </Show>
                      <Show when={item.enabled}>
                        <span class="badge badge-success">Enabled</span>
                      </Show>
                    </div>
                  </div>
                  <p class="skill-card-description">{item.description}</p>
                  <div class="skill-card-meta">
                    <Folder size={13} />
                    <span class="mono" style={{ color: "var(--fg-muted)" }}>
                      {item.source.root}/{item.source.directory}
                    </span>
                  </div>
                  <Show when={item.diagnostics && item.diagnostics.length > 0}>
                    <div class="stack" style={{ gap: "4px" }}>
                      <For each={item.diagnostics}>
                        {(diagnostic) => (
                          <div
                            class="badge badge-warning"
                            style={{
                              "white-space": "normal",
                              "text-align": "left",
                              padding: "4px 8px",
                              width: "fit-content",
                            }}
                          >
                            <TriangleAlert size={12} />
                            <span>{diagnostic.message}</span>
                          </div>
                        )}
                      </For>
                    </div>
                  </Show>
                  <div class="skill-card-actions">
                    <button
                      class="btn"
                      disabled={busy()}
                      onClick={() => void open(item)}
                    >
                      Open package
                    </button>
                    <button
                      class="btn"
                      disabled={busy()}
                      onClick={() =>
                        void execute(async () => {
                          await apiFetch(url(`/${item.id}`), {
                            method: "PATCH",
                            json: { enabled: !item.enabled },
                          });
                          await load();
                        })
                      }
                    >
                      {item.enabled ? "Disable" : "Enable"}
                    </button>
                    <a class="btn" href={url(`/${item.id}/export`)}>
                      <Download size={14} /> Export ZIP
                    </a>
                    <button
                      class="btn btn-danger"
                      disabled={busy()}
                      onClick={() => setDeleteTarget(item)}
                    >
                      Delete package
                    </button>
                  </div>
                </div>
              </section>
            )}
          </For>
        </Show>
      </div>
      <Dialog
        open={detail() !== null}
        title={detail()?.name || "Skill package"}
        size="xl"
        onClose={() => setDetail(null)}
      >
        <Show when={detail()}>
          {(current) => (
            <div class="stack">
              <div class="field">
                <label>Package name</label>
                <div
                  class="row-wrap"
                  style={{ "align-items": "center", gap: "8px" }}
                >
                  <input
                    class="input"
                    style={{ "max-width": "320px" }}
                    aria-label="Package name"
                    value={packageName()}
                    onInput={(event) =>
                      setPackageName(event.currentTarget.value)
                    }
                  />
                  <button
                    class="btn"
                    disabled={
                      busy() ||
                      packageName() === current().name ||
                      !packageName().trim()
                    }
                    onClick={() =>
                      void execute(async () => {
                        await apiFetch(url(`/${current().id}`), {
                          method: "PATCH",
                          json: {
                            name: packageName(),
                            expected_version: current().file_version,
                          },
                        });
                        setDetail(null);
                        await load();
                      })
                    }
                  >
                    Rename package
                  </button>
                </div>
              </div>
              <div
                class="settings-tabs"
                role="tablist"
                style={{ "margin-bottom": "8px" }}
              >
                <button
                  type="button"
                  role="tab"
                  class={`settings-tab ${mode() === "form" ? "active" : ""}`}
                  aria-selected={mode() === "form"}
                  disabled={busy()}
                  onClick={() => void switchMode("form")}
                >
                  Form
                </button>
                <button
                  type="button"
                  role="tab"
                  class={`settings-tab ${mode() === "raw" ? "active" : ""}`}
                  aria-selected={mode() === "raw"}
                  disabled={busy()}
                  onClick={() => void switchMode("raw")}
                >
                  SKILL.md source
                </button>
                <button
                  type="button"
                  role="tab"
                  class={`settings-tab ${mode() === "preview" ? "active" : ""}`}
                  aria-selected={mode() === "preview"}
                  disabled={busy()}
                  onClick={() => void switchMode("preview")}
                >
                  Preview
                </button>
              </div>
              <Show when={mode() === "form"}>
                <div class="stack">
                  <div class="field">
                    <label>Description</label>
                    <textarea
                      class="textarea"
                      rows={3}
                      value={description()}
                      onInput={(event) =>
                        setDescription(event.currentTarget.value)
                      }
                    />
                  </div>
                  <div class="field">
                    <label>Compatibility</label>
                    <input
                      class="input"
                      value={compatibility()}
                      onInput={(event) =>
                        setCompatibility(event.currentTarget.value)
                      }
                    />
                  </div>
                  <div class="field">
                    <label>Metadata (JSON)</label>
                    <textarea
                      class="textarea mono"
                      rows={3}
                      value={metadata()}
                      onInput={(event) =>
                        setMetadata(event.currentTarget.value)
                      }
                    />
                  </div>
                  <div class="field">
                    <label>Instructions</label>
                    <textarea
                      class="textarea mono"
                      rows={12}
                      value={body()}
                      onInput={(event) => setBody(event.currentTarget.value)}
                    />
                  </div>
                </div>
              </Show>
              <Show when={mode() === "raw"}>
                <textarea
                  aria-label="SKILL.md source"
                  class="textarea mono"
                  rows={18}
                  value={raw()}
                  onInput={(event) => setRaw(event.currentTarget.value)}
                />
              </Show>
              <Show when={mode() === "preview"}>
                <div
                  class="panel"
                  style={{
                    padding: "16px",
                    "max-height": "480px",
                    "overflow-y": "auto",
                    background: "var(--surface)",
                  }}
                >
                  <Markdown text={body()} />
                </div>
              </Show>
              <Show when={diagnostics().length > 0}>
                <div class="stack" style={{ gap: "4px" }}>
                  <For each={diagnostics()}>
                    {(diagnostic) => (
                      <div
                        class="badge badge-warning"
                        style={{
                          "white-space": "normal",
                          "text-align": "left",
                          padding: "6px 10px",
                          width: "100%",
                        }}
                      >
                        <TriangleAlert size={14} style={{ "flex-shrink": 0 }} />
                        <span>{diagnostic.message}</span>
                      </div>
                    )}
                  </For>
                </div>
              </Show>
              <div>
                <button
                  class="btn btn-primary"
                  disabled={busy() || mode() === "preview"}
                  onClick={() => void saveDocument()}
                >
                  <Save size={14} /> Save SKILL.md
                </button>
              </div>
              <div
                class="skill-file-header"
                style={{
                  display: "flex",
                  "align-items": "center",
                  "justify-content": "space-between",
                  gap: "10px",
                  "margin-top": "12px",
                  "padding-top": "12px",
                  "border-top": "1px solid var(--border)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    "align-items": "center",
                    gap: "8px",
                  }}
                >
                  <strong>Package files</strong>
                  <span class="mono" style={{ "font-size": "0.8125rem", color: "var(--fg-muted)" }}>
                    /{directory()}
                  </span>
                </div>
                <button
                  class="btn"
                  disabled={busy() || !directory()}
                  onClick={() =>
                    void execute(() =>
                      browse(directory().split("/").slice(0, -1).join("/")),
                    )
                  }
                >
                  Parent directory
                </button>
              </div>
              <div class="skill-file-list">
                <Show when={entries().length === 0}>
                  <div
                    style={{
                      padding: "12px",
                      "text-align": "center",
                      color: "var(--fg-muted)",
                      "font-size": "0.8125rem",
                    }}
                  >
                    No files in this directory.
                  </div>
                </Show>
                <For each={entries()}>
                  {(entry) => (
                    <div class="skill-file-row">
                      <div class="skill-file-info">
                        <button
                          class="btn"
                          style={{
                            "justify-content": "flex-start",
                            gap: "6px",
                          }}
                          onClick={() => void openFile(entry)}
                        >
                          <Show when={entry.kind === "directory"}>
                            <Folder size={14} />
                          </Show>
                          <Show when={entry.kind !== "directory"}>
                            <FileText size={14} />
                          </Show>
                          {entry.name}
                        </button>
                        <span style={{ "font-size": "0.8125rem", color: "var(--fg-muted)" }}>
                          {entry.kind === "file"
                            ? `${entry.size} bytes`
                            : "Directory"}
                        </span>
                      </div>
                      <Show when={entry.kind === "directory"}>
                        <button
                          class="btn"
                          disabled={busy()}
                          onClick={() => {
                            setFile({
                              path: entry.path,
                              content: null,
                              version: "",
                              mime_type: "directory",
                              size: 0,
                            });
                            setRenamePath(entry.path);
                          }}
                        >
                          Manage
                        </button>
                      </Show>
                    </div>
                  )}
                </For>
              </div>
              <Show when={nextOffset() !== null}>
                <button
                  class="btn"
                  disabled={busy()}
                  onClick={() =>
                    void execute(() => browse(directory(), nextOffset()!))
                  }
                >
                  Load more files
                </button>
              </Show>
              <div
                class="row-wrap"
                style={{ "align-items": "center", gap: "8px" }}
              >
                <input
                  class="input"
                  style={{ "max-width": "260px" }}
                  aria-label="New file or directory path"
                  placeholder="Relative file or directory path"
                  value={entryPath()}
                  onInput={(event) => setEntryPath(event.currentTarget.value)}
                />
                <button
                  class="btn"
                  disabled={busy() || !entryPath().trim()}
                  onClick={() =>
                    void execute(() => mutateFile("mkdir", entryPath()))
                  }
                >
                  Create directory
                </button>
                <button
                  class="btn"
                  disabled={busy() || !entryPath().trim()}
                  onClick={() =>
                    void execute(async () => {
                      await writeFile(entryPath(), "");
                      setEntryPath("");
                    })
                  }
                >
                  Create text file
                </button>
                <label class="btn" style={{ cursor: "pointer" }}>
                  <Upload size={14} /> Upload file
                  <input
                    type="file"
                    hidden
                    disabled={busy()}
                    onChange={(event) => {
                      const upload = event.currentTarget.files?.[0];
                      if (upload)
                        void execute(async () => {
                          const path = [directory(), upload.name]
                            .filter(Boolean)
                            .join("/");
                          if (entries().some((entry) => entry.path === path))
                            throw new Error(
                              "Open the existing file to replace it with a version check.",
                            );
                          await writeFile(path, upload);
                        });
                      event.currentTarget.value = "";
                    }}
                  />
                </label>
              </div>
              <Show when={file()}>
                {(selected) => (
                  <div
                    class="panel"
                    style={{ "border-color": "var(--border-strong)" }}
                  >
                    <div class="panel-body stack">
                      <div
                        style={{
                          display: "flex",
                          "align-items": "center",
                          "justify-content": "space-between",
                          gap: "8px",
                        }}
                      >
                        <strong
                          class="mono"
                          style={{ "font-size": "0.875rem" }}
                        >
                          {selected().path}
                        </strong>
                        <Show when={selected().mime_type !== "directory"}>
                          <span style={{ "font-size": "0.8125rem", color: "var(--fg-muted)" }}>
                            {selected().mime_type} • {selected().size} bytes
                          </span>
                        </Show>
                      </div>
                      <Show when={selected().content !== null}>
                        <textarea
                          class="textarea mono"
                          aria-label="Resource content"
                          rows={10}
                          value={fileText()}
                          onInput={(event) =>
                            setFileText(event.currentTarget.value)
                          }
                        />
                        <div>
                          <button
                            class="btn btn-primary"
                            disabled={busy()}
                            onClick={() =>
                              void execute(async () => {
                                await writeFile(
                                  selected().path,
                                  fileText(),
                                  selected().version,
                                );
                                setFile(
                                  await apiFetch<FileDetail>(
                                    fileUrl(selected().path),
                                  ),
                                );
                                showToast("File saved.");
                              })
                            }
                          >
                            <Save size={14} /> Save file
                          </button>
                        </div>
                      </Show>
                      <Show when={selected().mime_type.startsWith("image/")}>
                        <div
                          style={{
                            "text-align": "center",
                            padding: "12px",
                            background: "var(--surface-2)",
                            "border-radius": "8px",
                          }}
                        >
                          <img
                            alt={selected().path}
                            src={fileUrl(selected().path, true)}
                            style={{
                              "max-width": "100%",
                              "max-height": "360px",
                              "object-fit": "contain",
                              "border-radius": "4px",
                            }}
                          />
                        </div>
                      </Show>
                      <Show
                        when={
                          selected().content === null &&
                          selected().mime_type !== "directory"
                        }
                      >
                        <div
                          style={{
                            padding: "8px 12px",
                            "border-radius": "6px",
                            background: "var(--surface-2)",
                            color: "var(--fg-muted)",
                            "font-size": "0.8125rem",
                          }}
                        >
                          Binary or large resource. Download it or process it
                          using the skill scripts.
                        </div>
                      </Show>
                      <Show when={selected().mime_type !== "directory"}>
                        <div class="row-wrap" style={{ gap: "8px" }}>
                          <a class="btn" href={fileUrl(selected().path, true)}>
                            <Download size={14} /> Download file
                          </a>
                          <label class="btn" style={{ cursor: "pointer" }}>
                            <Upload size={14} /> Replace file
                            <input
                              type="file"
                              hidden
                              disabled={busy()}
                              onChange={(event) => {
                                const upload = event.currentTarget.files?.[0];
                                if (upload)
                                  void execute(async () => {
                                    await writeFile(
                                      selected().path,
                                      upload,
                                      selected().version,
                                    );
                                    const updated = await apiFetch<FileDetail>(
                                      fileUrl(selected().path),
                                    );
                                    setFile(updated);
                                    setFileText(updated.content || "");
                                  });
                              }}
                            />
                          </label>
                        </div>
                      </Show>
                      <div
                        class="row-wrap"
                        style={{
                          "align-items": "center",
                          gap: "8px",
                          "padding-top": "8px",
                          "border-top": "1px solid var(--border)",
                        }}
                      >
                        <input
                          class="input"
                          style={{ "max-width": "260px" }}
                          aria-label="Rename resource path"
                          value={renamePath()}
                          onInput={(event) =>
                            setRenamePath(event.currentTarget.value)
                          }
                        />
                        <button
                          class="btn"
                          disabled={
                            busy() ||
                            renamePath() === selected().path ||
                            !renamePath().trim()
                          }
                          onClick={() =>
                            void execute(async () => {
                              await mutateFile(
                                "rename",
                                selected().path,
                                renamePath(),
                                selected().version || undefined,
                              );
                              setFile(null);
                            })
                          }
                        >
                          Rename
                        </button>
                        <button
                          class="btn btn-danger"
                          disabled={busy()}
                          onClick={() => setEntryDeleteTarget(selected())}
                        >
                          <Trash2 size={14} /> Delete
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </Show>
            </div>
          )}
        </Show>
      </Dialog>
      <Dialog
        open={createOpen()}
        title="Create skill package"
        size="lg"
        onClose={() => setCreateOpen(false)}
        footer={
          <div
            class="row-wrap"
            style={{
              "justify-content": "flex-end",
              gap: "8px",
              width: "100%",
            }}
          >
            <button
              class="btn"
              type="button"
              onClick={() => setCreateOpen(false)}
            >
              Cancel
            </button>
            <button
              class="btn btn-primary"
              type="button"
              disabled={busy() || !newName().trim()}
              onClick={() =>
                void execute(async () => {
                  await postJson(base, {
                    name: newName(),
                    content: newDocument(),
                    scope: scope(),
                    workspace: workspace(),
                  });
                  setCreateOpen(false);
                  await load();
                })
              }
            >
              <Plus size={14} /> Create package
            </button>
          </div>
        }
      >
        <div class="stack">
          <div class="field">
            <label>Skill name</label>
            <input
              class="input"
              value={newName()}
              onInput={(event) => setNewName(event.currentTarget.value)}
              placeholder="e.g. data-analyzer"
            />
          </div>
          <div class="field">
            <label>SKILL.md initial content</label>
            <textarea
              aria-label="New skill document"
              class="textarea mono"
              rows={14}
              value={newDocument()}
              onInput={(event) => setNewDocument(event.currentTarget.value)}
            />
          </div>
        </div>
      </Dialog>
      <Dialog
        open={importOpen()}
        title="Import skill packages"
        size="lg"
        onClose={() => setImportOpen(false)}
      >
        <div class="stack">
          <div class="field">
            <label>Import source</label>
            <select
              class="select"
              aria-label="Import source"
              value={importKind()}
              onChange={(event) => {
                setImportKind(event.currentTarget.value);
                setPreview(null);
              }}
            >
              <option value="zip">ZIP upload</option>
              <option value="directory">Local directory</option>
              <option value="git">Git repository</option>
            </select>
          </div>
          <Show when={importKind() === "zip"}>
            <div class="field">
              <label>ZIP file</label>
              <input
                class="input"
                aria-label="Skill ZIP"
                type="file"
                accept=".zip"
                onChange={(event) => {
                  setZipFile(event.currentTarget.files?.[0] || null);
                  setPreview(null);
                }}
              />
            </div>
          </Show>
          <Show when={importKind() !== "zip"}>
            <div class="field">
              <label>
                {importKind() === "git"
                  ? "Git repository URL"
                  : "Directory path"}
              </label>
              <input
                class="input"
                aria-label="Import location"
                placeholder={
                  importKind() === "git"
                    ? "HTTPS repository URL (e.g. https://github.com/org/repo)"
                    : "Directory on the backend host"
                }
                value={importPath()}
                onInput={(event) => {
                  setImportPath(event.currentTarget.value);
                  setPreview(null);
                }}
              />
            </div>
          </Show>
          <Show when={importKind() === "git"}>
            <div class="field">
              <label>Git branch, tag or commit</label>
              <input
                class="input"
                aria-label="Git ref"
                placeholder="Branch, tag or commit"
                value={importRef()}
                onInput={(event) => {
                  setImportRef(event.currentTarget.value);
                  setPreview(null);
                }}
              />
            </div>
            <div class="field">
              <label>Subdirectory</label>
              <input
                class="input"
                aria-label="Git skill directory"
                placeholder="Directory within repository"
                value={subdirectory()}
                onInput={(event) => {
                  setSubdirectory(event.currentTarget.value);
                  setPreview(null);
                }}
              />
            </div>
            <div class="field">
              <label>GitHub installation ID (optional)</label>
              <input
                class="input"
                aria-label="GitHub installation ID"
                placeholder="Connected GitHub installation ID (private repository)"
                value={installationId()}
                onInput={(event) => setInstallationId(event.currentTarget.value)}
              />
            </div>
          </Show>
          <div>
            <button
              class="btn"
              disabled={busy()}
              onClick={() => void previewImport()}
            >
              {busy() ? "Preparing preview..." : "Preview import"}
            </button>
          </div>
          <Show when={preview()}>
            {(result) => (
              <div
                class="stack"
                style={{
                  "margin-top": "12px",
                  "padding-top": "12px",
                  "border-top": "1px solid var(--border)",
                }}
              >
                <Show when={result().commit}>
                  <span class="mono" style={{ "font-size": "0.8125rem", color: "var(--fg-muted)" }}>
                    Commit: {result().commit}
                  </span>
                </Show>
                <For each={result().skills}>
                  {(item) => (
                    <div class="panel">
                      <div class="panel-body stack">
                        <div
                          class="row-wrap"
                          style={{ "align-items": "center", gap: "8px" }}
                        >
                          <strong>{item.name}</strong>
                          <Show when={item.conflict}>
                            <span class="badge badge-warning">
                              Existing package conflict
                            </span>
                          </Show>
                        </div>
                        <p style={{ margin: 0 }}>{item.description}</p>
                        <For each={item.diagnostics}>
                          {(diagnostic) => (
                            <p
                              style={{
                                margin: "4px 0 0",
                                color: "var(--warning)",
                                "font-size": "0.8125rem",
                              }}
                            >
                              {diagnostic.message}
                            </p>
                          )}
                        </For>
                      </div>
                    </div>
                  )}
                </For>
                <label
                  style={{
                    display: "inline-flex",
                    "align-items": "center",
                    gap: "8px",
                    cursor: "pointer",
                  }}
                >
                  <input
                    type="checkbox"
                    checked={overwrite()}
                    onChange={(event) =>
                      setOverwrite(event.currentTarget.checked)
                    }
                  />{" "}
                  Replace existing destination packages
                </label>
                <div>
                  <button
                    class="btn btn-primary"
                    disabled={
                      busy() ||
                      (result().skills.some((item) => item.conflict) &&
                        !overwrite())
                    }
                    onClick={() =>
                      void execute(async () => {
                        await postJson(`${base}/imports`, {
                          preview_id: result().preview_id,
                          scope: scope(),
                          workspace: workspace(),
                          overwrite: overwrite(),
                        });
                        setImportOpen(false);
                        await load();
                        showToast("Skill packages imported.");
                      })
                    }
                  >
                    Import {result().skills.length} packages
                  </button>
                </div>
              </div>
            )}
          </Show>
        </div>
      </Dialog>
      <ConfirmDialog
        open={entryDeleteTarget() !== null}
        title="Delete package entry"
        message={
          <p>
            Delete {entryDeleteTarget()?.path}
            {entryDeleteTarget()?.mime_type === "directory"
              ? " and all its files"
              : ""}
            ?
          </p>
        }
        confirmLabel="Delete entry"
        confirmVariant="danger"
        onClose={() => setEntryDeleteTarget(null)}
        onConfirm={() =>
          void execute(async () => {
            const target = entryDeleteTarget()!;
            await mutateFile(
              "delete",
              target.path,
              "",
              target.version || undefined,
            );
            setEntryDeleteTarget(null);
            setFile(null);
          })
        }
      />
      <ConfirmDialog
        open={deleteTarget() !== null}
        title="Delete skill package"
        message={
          <p>Delete {deleteTarget()?.name} and all files in this package?</p>
        }
        confirmLabel="Delete package"
        confirmVariant="danger"
        onClose={() => setDeleteTarget(null)}
        onConfirm={() =>
          void execute(async () => {
            await apiFetch(url(`/${deleteTarget()!.id}`), { method: "DELETE" });
            setDeleteTarget(null);
            await load();
          })
        }
      />
    </SettingsLayout>
  );
}
