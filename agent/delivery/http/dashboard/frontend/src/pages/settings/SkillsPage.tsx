import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import { Edit3, Eye, Plus, RefreshCw, Save, Trash2 } from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DashboardTable } from "@/components/DashboardTable";
import { Dialog } from "@/components/Dialog";
import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import { truncateText } from "@/lib/utils";
import type { SkillInfo, SkillsPayload } from "@/types";

import { SettingsLayout } from "./SettingsLayout";

type SkillForm = {
  name: string;
  content: string;
  resources: string[];
};

const repositoryDirKey = "skills.repository_dir";

const blankSkillContent = (name: string) => `---
name: ${name || "new-skill"}
description: Describe when this skill should be used.
---
# Instructions

Add the skill instructions here.
`;

const blankForm = (): SkillForm => ({
  name: "",
  content: blankSkillContent(""),
  resources: [],
});

function skillToForm(skill: SkillInfo): SkillForm {
  return {
    name: skill.name,
    content: skill.content || blankSkillContent(skill.name),
    resources: skill.resources || [],
  };
}

export function SkillsPage() {
  const [data, setData] = createSignal<SkillsPayload>();
  const [error, setError] = createSignal("");
  const [query, setQuery] = createSignal("");
  const [modalMode, setModalMode] = createSignal<"create" | "edit" | "view" | null>(null);
  const [currentName, setCurrentName] = createSignal("");
  const [form, setForm] = createSignal<SkillForm>(blankForm());
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
    setModalMode("create");
  };

  const openSkill = async (skill: SkillInfo, mode: "edit" | "view") => {
    try {
      const detail = await apiFetch<{ skill: SkillInfo }>(
        `/dashboard-api/skills/${encodeURIComponent(skill.name)}`,
      );
      setCurrentName(skill.name);
      setForm(skillToForm(detail.skill));
      setModalMode(mode);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to load skill", "error");
    }
  };

  const closeModal = () => setModalMode(null);

  const saveSkill = async () => {
    const current = form();
    if (!current.name.trim()) {
      showToast("Skill name is required.", "error");
      return;
    }
    if (!current.content.trim()) {
      showToast("SKILL.md content is required.", "error");
      return;
    }
    const payload = {
      name: current.name,
      content: current.content,
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
                <button
                  class="btn btn-primary"
                  type="button"
                  disabled={busy() === "setting"}
                  onClick={saveRepositoryDir}
                >
                  <Save size={14} />
                  {busy() === "setting" ? "Saving..." : "Save"}
                </button>
              </div>
              <div class="panel-body">
                <div class="field">
                  <label>Repository skills directory</label>
                  <input
                    class="input mono"
                    value={repositoryDirDraft()}
                    onInput={(event) => setRepositoryDirDraft(event.currentTarget.value)}
                  />
                  <span class="hint">Relative path inside each repository. Repo-local skills override global skills with the same name.</span>
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
              title={modalMode() === "create" ? "New Skill" : modalMode() === "edit" ? `Edit ${currentName()}` : `View ${currentName()}`}
              wide
              onClose={closeModal}
              footer={
                <>
                  <button class="btn" type="button" onClick={closeModal}>
                    Close
                  </button>
                  <Show when={modalMode() !== "view"}>
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
                <div class="field">
                  <label>Name</label>
                  <input
                    class="input mono"
                    value={form().name}
                    disabled={modalMode() === "view"}
                    onInput={(event) => {
                      const nextName = event.currentTarget.value;
                      updateForm("name", nextName);
                      if (modalMode() === "create") {
                        updateForm("content", blankSkillContent(nextName));
                      }
                    }}
                  />
                </div>
                <div class="field">
                  <label>SKILL.md</label>
                  <textarea
                    class="textarea mono"
                    rows={18}
                    value={form().content}
                    disabled={modalMode() === "view"}
                    onInput={(event) => updateForm("content", event.currentTarget.value)}
                  />
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
