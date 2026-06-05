import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import { Edit3, Eye, Plus, RefreshCw, Save, Trash2, X } from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DashboardTable } from "@/components/DashboardTable";
import { Dialog } from "@/components/Dialog";
import { Markdown } from "@/components/Markdown";
import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import {
  parseSkillMarkdown,
  serializeSkillMarkdown,
  type ParsedSkill,
  type SkillFrontmatter,
} from "@/lib/skillMarkdown";
import { truncateText } from "@/lib/utils";
import type { SkillInfo, SkillsPayload } from "@/types";

import { SettingsLayout } from "./SettingsLayout";

type MetadataEntry = { key: string; value: string };

type SkillForm = {
  name: string;
  description: string;
  license: string;
  compatibility: string;
  body: string;
  allowedTools: string[];
  metadata: MetadataEntry[];
  resources: string[];
};

const repositoryDirKey = "skills.repository_dir";

const blankForm = (): SkillForm => ({
  name: "",
  description: "",
  license: "",
  compatibility: "",
  body: "",
  allowedTools: [],
  metadata: [],
  resources: [],
});

const normalizeMetadata = (
  raw: Record<string, string> | null | undefined,
): MetadataEntry[] =>
  Object.entries(raw ?? {})
    .map(([key, value]) => ({ key, value: value ?? "" }))
    .sort((a, b) => a.key.localeCompare(b.key));

const parsedToForm = (parsed: ParsedSkill, resources: string[]): SkillForm => ({
  name: parsed.frontmatter.name,
  description: parsed.frontmatter.description,
  license: parsed.frontmatter.license ?? "",
  compatibility: parsed.frontmatter.compatibility ?? "",
  body: parsed.body,
  allowedTools: [...(parsed.frontmatter.allowed_tools ?? [])],
  metadata: normalizeMetadata(parsed.frontmatter.metadata),
  resources,
});

const formToFrontmatter = (form: SkillForm): SkillFrontmatter => {
  const frontmatter: SkillFrontmatter = {
    name: form.name,
    description: form.description,
  };
  if (form.license.trim()) {
    frontmatter.license = form.license;
  }
  if (form.compatibility.trim()) {
    frontmatter.compatibility = form.compatibility;
  }
  const allowedTools = form.allowedTools.map((tool) => tool.trim()).filter(Boolean);
  if (allowedTools.length > 0) {
    frontmatter.allowed_tools = allowedTools;
  }
  const metadataEntries = form.metadata
    .map((entry) => ({ key: entry.key.trim(), value: entry.value }))
    .filter((entry) => entry.key.length > 0);
  if (metadataEntries.length > 0) {
    frontmatter.metadata = Object.fromEntries(
      metadataEntries.map((entry) => [entry.key, entry.value]),
    );
  }
  return frontmatter;
};

export function SkillsPage() {
  const [data, setData] = createSignal<SkillsPayload>();
  const [error, setError] = createSignal("");
  const [query, setQuery] = createSignal("");
  const [modalMode, setModalMode] = createSignal<"create" | "edit" | "view" | null>(null);
  const [currentName, setCurrentName] = createSignal("");
  const [form, setForm] = createSignal<SkillForm>(blankForm());
  const [allowedToolDraft, setAllowedToolDraft] = createSignal("");
  const [deleteTargetName, setDeleteTargetName] = createSignal<string | null>(null);
  const [repositoryDirDraft, setRepositoryDirDraft] = createSignal(".agent/skills");
  const [busy, setBusy] = createSignal("");
  const { showToast } = useToast();

  const load = async () => {
    setError("");
    try {
      const payload = await apiFetch<SkillsPayload>("/dashboard-api/skills");
      setData(payload);
      setRepositoryDirDraft(String(payload.settings[repositoryDirKey]?.value || ".agent/skills"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load skills");
    }
  };

  const reload = async () => {
    setBusy("reload");
    try {
      const payload = await postJson<SkillsPayload & { status: string }>("/dashboard-api/skills/reload");
      setData(payload);
      setRepositoryDirDraft(String(payload.settings[repositoryDirKey]?.value || ".agent/skills"));
      showToast("Skills reloaded.");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to reload skills", "error");
    } finally {
      setBusy("");
    }
  };

  const filteredSkills = createMemo(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    const needle = query().trim().toLowerCase();
    if (!needle) {
      return payload.skills;
    }
    return payload.skills.filter((skill) =>
      [skill.name, skill.description, skill.path, ...(skill.resources || [])]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  });

  const updateForm = <K extends keyof SkillForm>(key: K, value: SkillForm[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const openCreate = () => {
    setCurrentName("");
    setForm(blankForm());
    setAllowedToolDraft("");
    setModalMode("create");
  };

  const openSkill = async (skill: SkillInfo, mode: "edit" | "view") => {
    try {
      const detail = await apiFetch<{ skill: SkillInfo }>(
        `/dashboard-api/skills/${encodeURIComponent(skill.name)}`,
      );
      const content = detail.skill.content || "";
      const parsed = parseSkillMarkdown(content);
      setCurrentName(detail.skill.name);
      setForm(parsedToForm(parsed, detail.skill.resources || []));
      setAllowedToolDraft("");
      setModalMode(mode);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to load skill", "error");
    }
  };

  const closeModal = () => {
    setModalMode(null);
    setAllowedToolDraft("");
  };

  const addAllowedTool = () => {
    const raw = allowedToolDraft().trim();
    if (!raw) {
      return;
    }
    const current = form().allowedTools;
    if (current.includes(raw)) {
      setAllowedToolDraft("");
      return;
    }
    updateForm("allowedTools", [...current, raw]);
    setAllowedToolDraft("");
  };

  const removeAllowedTool = (tool: string) => {
    updateForm(
      "allowedTools",
      form().allowedTools.filter((entry) => entry !== tool),
    );
  };

  const addMetadataEntry = () => {
    updateForm("metadata", [...form().metadata, { key: "", value: "" }]);
  };

  const updateMetadataEntry = (index: number, patch: Partial<MetadataEntry>) => {
    updateForm(
      "metadata",
      form().metadata.map((entry, idx) => (idx === index ? { ...entry, ...patch } : entry)),
    );
  };

  const removeMetadataEntry = (index: number) => {
    updateForm(
      "metadata",
      form().metadata.filter((_, idx) => idx !== index),
    );
  };

  const saveSkill = async () => {
    const current = form();
    if (!current.name.trim()) {
      showToast("Skill name is required.", "error");
      return;
    }
    if (!current.description.trim()) {
      showToast("Description is required.", "error");
      return;
    }
    if (!current.body.trim()) {
      showToast("Instructions (body) are required.", "error");
      return;
    }

    const frontmatter = formToFrontmatter(current);
    const content = serializeSkillMarkdown(frontmatter, current.body);
    const payload = {
      name: current.name.trim(),
      content,
    };

    setBusy("skill");
    try {
      if (modalMode() === "create") {
        await postJson("/dashboard-api/skills", payload);
        showToast("Skill created.");
      } else {
        await putJson(`/dashboard-api/skills/${encodeURIComponent(currentName())}`, payload);
        showToast("Skill updated.");
      }
      closeModal();
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to save skill", "error");
    } finally {
      setBusy("");
    }
  };

  const saveRepositoryDir = async () => {
    setBusy("setting");
    try {
      await putJson("/settings", {
        values: {
          [repositoryDirKey]: repositoryDirDraft(),
        },
      });
      showToast("Repository skill directory saved.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to save setting", "error");
    } finally {
      setBusy("");
    }
  };

  const confirmDeleteSkill = async () => {
    const name = deleteTargetName();
    if (!name) {
      return;
    }
    setBusy("delete");
    try {
      await deleteJson(`/dashboard-api/skills/${encodeURIComponent(name)}`);
      showToast("Skill deleted.");
      await load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to delete skill", "error");
    } finally {
      setBusy("");
      setDeleteTargetName(null);
    }
  };

  onMount(load);

  const isReadOnly = () => modalMode() === "view";

  return (
    <SettingsLayout
      title="Skills"
      breadcrumbLabel="Skills"
      contentWidth="wide"
      actions={
        <button class="btn" type="button" disabled={busy() === "reload"} onClick={reload}>
          <RefreshCw size={14} />
          {busy() === "reload" ? "Reloading..." : "Reload"}
        </button>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <section class="panel">
              <div class="panel-header">
                <div>
                  <div class="panel-title">Repository-local skills</div>
                  <div class="hint">Global skills live in <span class="mono">{payload.skills_root}</span>.</div>
                </div>
              </div>
              <div class="panel-body">
                <div class="field">
                  <label>Repository skills directory</label>
                  <input
                    class="input mono"
                    value={repositoryDirDraft()}
                    onInput={(event) => setRepositoryDirDraft(event.currentTarget.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        void saveRepositoryDir();
                      }
                    }}
                  />
                  <span class="hint">Relative path inside each repository. Repo-local skills override global skills with the same name.</span>
                  <div class="row" style={{ "justify-content": "flex-end", "margin-top": "4px" }}>
                    <button
                      class="btn btn-primary"
                      type="button"
                      disabled={busy() === "setting"}
                      onClick={() => void saveRepositoryDir()}
                    >
                      <Save size={14} />
                      {busy() === "setting" ? "Saving..." : "Save"}
                    </button>
                  </div>
                </div>
              </div>
            </section>

            <SettingsResourceToolbar
              searchValue={query()}
              searchPlaceholder="Search skills..."
              onSearchInput={setQuery}
              actions={
                <button class="btn btn-primary" type="button" onClick={openCreate}>
                  <Plus size={14} />
                  New Skill
                </button>
              }
            />

            <section class="panel">
              <DashboardTable
                columns={[
                  { header: "Skill" },
                  { header: "Description" },
                  { header: "Resources" },
                  { header: "Actions" },
                ]}
                rows={filteredSkills()}
                emptyMessage="No skills found."
              >
                {(skill) => (
                  <tr>
                    <td>
                      <div class="mono">{skill.name}</div>
                      <div class="hint">{skill.path}</div>
                    </td>
                    <td>
                      <Show
                        when={skill.description}
                        fallback={<span class="hint">-</span>}
                      >
                        {(description) => (
                          <div class="hint">{truncateText(description(), 180)}</div>
                        )}
                      </Show>
                    </td>
                    <td>
                      <span class="badge">{skill.resources?.length || 0} files</span>
                    </td>
                    <td>
                      <div class="row-wrap">
                        <button class="btn btn-sm" type="button" onClick={() => void openSkill(skill, "view")}>
                          <Eye size={13} />
                          View
                        </button>
                        <button class="btn btn-sm" type="button" onClick={() => void openSkill(skill, "edit")}>
                          <Edit3 size={13} />
                          Edit
                        </button>
                        <button
                          class="btn btn-sm btn-danger"
                          type="button"
                          onClick={() => setDeleteTargetName(skill.name)}
                        >
                          <Trash2 size={13} />
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                )}
              </DashboardTable>
            </section>

            <Dialog
              open={modalMode() !== null}
              title={
                modalMode() === "create"
                  ? "New Skill"
                  : modalMode() === "edit"
                    ? `Edit ${currentName()}`
                    : `View ${currentName()}`
              }
              wide
              onClose={closeModal}
              footer={
                <>
                  <button class="btn" type="button" onClick={closeModal}>
                    Close
                  </button>
                  <Show when={!isReadOnly()}>
                    <button
                      class="btn btn-primary"
                      type="button"
                      disabled={busy() === "skill"}
                      onClick={saveSkill}
                    >
                      {busy() === "skill" ? "Saving..." : "Save"}
                    </button>
                  </Show>
                </>
              }
            >
              <div class="stack">
                <div class="grid-2">
                  <div class="field">
                    <label>Name</label>
                    <input
                      class="input mono"
                      value={form().name}
                      disabled={isReadOnly()}
                      placeholder="my-skill"
                      onInput={(event) => updateForm("name", event.currentTarget.value)}
                    />
                    <span class="hint">Lowercase letters, numbers, and single hyphens (1-64 chars).</span>
                  </div>
                  <div class="field">
                    <label>Description</label>
                    <input
                      class="input"
                      value={form().description}
                      disabled={isReadOnly()}
                      placeholder="Describe when this skill should be used."
                      onInput={(event) => updateForm("description", event.currentTarget.value)}
                    />
                    <span class="hint">Shown in the skill catalog so the LLM can decide when to load it.</span>
                  </div>
                </div>

                <div class="grid-2">
                  <div class="field">
                    <label>License <span class="hint">(optional)</span></label>
                    <input
                      class="input"
                      value={form().license}
                      disabled={isReadOnly()}
                      placeholder="MIT"
                      onInput={(event) => updateForm("license", event.currentTarget.value)}
                    />
                  </div>
                  <div class="field">
                    <label>Compatibility <span class="hint">(optional)</span></label>
                    <input
                      class="input"
                      value={form().compatibility}
                      disabled={isReadOnly()}
                      placeholder=">=1.0"
                      onInput={(event) => updateForm("compatibility", event.currentTarget.value)}
                    />
                  </div>
                </div>

                <div class="field">
                  <label>Allowed tools <span class="hint">(optional)</span></label>
                  <Show
                    when={!isReadOnly()}
                    fallback={
                      <Show
                        when={form().allowedTools.length > 0}
                        fallback={<span class="hint">No tools declared.</span>}
                      >
                        <div class="chips">
                          <For each={form().allowedTools}>
                            {(tool) => <span class="chip">{tool}</span>}
                          </For>
                        </div>
                      </Show>
                    }
                  >
                    <div class="row-wrap">
                      <Show when={form().allowedTools.length > 0}>
                        <div class="chips">
                          <For each={form().allowedTools}>
                            {(tool) => (
                              <span class="chip" style={{ display: "inline-flex", gap: "4px", "align-items": "center" }}>
                                {tool}
                                <button
                                  class="btn btn-icon btn-sm"
                                  type="button"
                                  style={{ "min-height": "18px", height: "18px", width: "18px", padding: "0", border: "0", background: "transparent", color: "var(--muted)" }}
                                  aria-label={`Remove ${tool}`}
                                  onClick={() => removeAllowedTool(tool)}
                                >
                                  <X size={11} />
                                </button>
                              </span>
                            )}
                          </For>
                        </div>
                      </Show>
                      <input
                        class="input mono"
                        style={{ "max-width": "260px" }}
                        placeholder="Type a tool name and press Enter"
                        value={allowedToolDraft()}
                        onInput={(event) => setAllowedToolDraft(event.currentTarget.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === ",") {
                            event.preventDefault();
                            addAllowedTool();
                          } else if (event.key === "Backspace" && allowedToolDraft() === "" && form().allowedTools.length > 0) {
                            const next = [...form().allowedTools];
                            next.pop();
                            updateForm("allowedTools", next);
                          }
                        }}
                      />
                      <button
                        class="btn btn-sm"
                        type="button"
                        disabled={!allowedToolDraft().trim()}
                        onClick={addAllowedTool}
                      >
                        <Plus size={12} />
                        Add
                      </button>
                    </div>
                    <span class="hint">Tools the skill may invoke. Press Enter or comma to add.</span>
                  </Show>
                </div>

                <div class="field">
                  <label>Metadata <span class="hint">(optional)</span></label>
                  <Show
                    when={!isReadOnly()}
                    fallback={
                      <Show
                        when={form().metadata.length > 0}
                        fallback={<span class="hint">No metadata.</span>}
                      >
                        <div class="stack" style={{ gap: "6px" }}>
                          <For each={form().metadata}>
                            {(entry) => (
                              <div class="row" style={{ gap: "8px" }}>
                                <span class="mono" style={{ "min-width": "120px" }}>{entry.key}</span>
                                <span class="hint">{entry.value || "(empty)"}</span>
                              </div>
                            )}
                          </For>
                        </div>
                      </Show>
                    }
                  >
                    <Show
                      when={form().metadata.length > 0}
                      fallback={<div class="hint" style={{ "margin-bottom": "6px" }}>No metadata entries yet.</div>}
                    >
                      <div class="stack" style={{ gap: "6px" }}>
                        <For each={form().metadata}>
                          {(entry, index) => (
                            <div class="row" style={{ gap: "6px" }}>
                              <input
                                class="input mono"
                                style={{ "max-width": "220px" }}
                                placeholder="key"
                                value={entry.key}
                                onInput={(event) =>
                                  updateMetadataEntry(index(), { key: event.currentTarget.value })
                                }
                              />
                              <input
                                class="input"
                                placeholder="value"
                                value={entry.value}
                                onInput={(event) =>
                                  updateMetadataEntry(index(), { value: event.currentTarget.value })
                                }
                              />
                              <button
                                class="btn btn-icon btn-sm"
                                type="button"
                                aria-label="Remove entry"
                                onClick={() => removeMetadataEntry(index())}
                              >
                                <Trash2 size={12} />
                              </button>
                            </div>
                          )}
                        </For>
                      </div>
                    </Show>
                    <button
                      class="btn btn-sm"
                      type="button"
                      style={{ "margin-top": "8px" }}
                      onClick={addMetadataEntry}
                    >
                      <Plus size={12} />
                      Add entry
                    </button>
                  </Show>
                </div>

                <div class="field">
                  <label>Instructions (markdown body)</label>
                  <Show
                    when={isReadOnly()}
                    fallback={
                      <textarea
                        class="textarea mono"
                        rows={14}
                        value={form().body}
                        placeholder="Add the skill instructions here. Markdown is supported."
                        onInput={(event) => updateForm("body", event.currentTarget.value)}
                      />
                    }
                  >
                    <Show
                      when={form().body.trim()}
                      fallback={<div class="hint">No instructions.</div>}
                    >
                      <div
                        class="panel"
                        style={{ padding: "12px", background: "var(--surface-2)", "border-radius": "8px" }}
                      >
                        <Markdown text={form().body} />
                      </div>
                    </Show>
                  </Show>
                </div>

                <Show when={form().resources.length > 0}>
                  <div class="field">
                    <label>Resources</label>
                    <div class="row-wrap">
                      <For each={form().resources}>
                        {(resource) => <span class="badge mono">{resource}</span>}
                      </For>
                    </div>
                    <span class="hint">Resource files are read-only from this screen.</span>
                  </div>
                </Show>
              </div>
            </Dialog>

            <ConfirmDialog
              open={deleteTargetName() !== null}
              title="Delete Skill"
              message={<p>Are you sure you want to delete skill <span class="mono">{deleteTargetName()}</span>?</p>}
              confirmLabel="Delete"
              confirmVariant="danger"
              onClose={() => setDeleteTargetName(null)}
              onConfirm={() => void confirmDeleteSkill()}
            />
          </div>
        )}
      </DataGate>
    </SettingsLayout>
  );
}
