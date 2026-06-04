import { createMemo, createSignal, Show } from "solid-js";
import { Edit3, Plus, RefreshCw, Trash2 } from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { CopyButton } from "@/components/CopyButton";
import { DashboardTable } from "@/components/DashboardTable";
import { Dialog } from "@/components/Dialog";
import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
import { getPromptVariableNamePattern, getSystemVariableNames } from "@/lib/catalogStore";
import { useCatalogAndLoad } from "@/lib/useCatalogAndLoad";
import { truncateText } from "@/lib/utils";
import type { PromptVariable, PromptVariablesPayload } from "@/types";

import { SettingsLayout } from "./SettingsLayout";

type PromptVariableForm = {
  name: string;
  value: string;
};

const blankForm = (): PromptVariableForm => ({
  name: "",
  value: "",
});

function variableToForm(variable: PromptVariable): PromptVariableForm {
  return {
    name: variable.name,
    value: variable.value || "",
  };
}

function formatDate(value: string | null): string {
  if (!value) {
    return "-";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}

export function PromptVariablesPage() {
  const [data, setData] = createSignal<PromptVariablesPayload>();
  const [error, setError] = createSignal("");
  const [query, setQuery] = createSignal("");
  const [modalMode, setModalMode] = createSignal<"create" | "edit" | null>(null);
  const [currentName, setCurrentName] = createSignal("");
  const [form, setForm] = createSignal<PromptVariableForm>(blankForm());
  const [deleteTargetName, setDeleteTargetName] = createSignal<string | null>(null);
  const { showToast } = useToast();

  const load = async () => {
    setError("");
    try {
      setData(await apiFetch<PromptVariablesPayload>("/dashboard-api/prompt-variables"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load prompt variables");
    }
  };

  const filteredVariables = createMemo(() => {
    const payload = data();
    if (!payload) {
      return [];
    }
    const needle = query().trim().toLowerCase();
    if (!needle) {
      return payload.variables;
    }
    return payload.variables.filter((variable) =>
      [variable.name, variable.placeholder, variable.value]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  });

  const updateForm = <K extends keyof PromptVariableForm>(
    key: K,
    value: PromptVariableForm[K],
  ) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const openCreate = () => {
    setCurrentName("");
    setForm(blankForm());
    setModalMode("create");
  };

  const openEdit = (variable: PromptVariable) => {
    setCurrentName(variable.name);
    setForm(variableToForm(variable));
    setModalMode("edit");
  };

  const closeModal = () => setModalMode(null);

  const saveVariable = async () => {
    const payload = form();
    const pattern = new RegExp(getPromptVariableNamePattern());
    if (!pattern.test(payload.name.trim())) {
      showToast("Prompt variable name is invalid.", "error");
      return;
    }
    if (getSystemVariableNames().includes(payload.name.trim())) {
      showToast("That name is reserved for a system prompt variable.", "error");
      return;
    }

    try {
      if (modalMode() === "create") {
        await postJson("/prompt-variables", payload);
        showToast("Prompt variable created.");
      } else {
        await putJson(`/prompt-variables/${encodeURIComponent(currentName())}`, payload);
        showToast("Prompt variable updated.");
      }
      closeModal();
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to save prompt variable",
        "error",
      );
    }
  };

  const confirmDeleteVariable = async () => {
    const name = deleteTargetName();
    if (!name) {
      return;
    }
    try {
      await deleteJson(`/prompt-variables/${encodeURIComponent(name)}`);
      showToast("Prompt variable deleted.");
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to delete prompt variable",
        "error",
      );
    } finally {
      setDeleteTargetName(null);
    }
  };

  useCatalogAndLoad(load);

  return (
    <SettingsLayout
      title="Prompt Variables"
      breadcrumbLabel="Prompt Variables"
      contentWidth="wide"
      actions={
        <button class="btn" type="button" onClick={load}>
          <RefreshCw size={14} />
          Reload
        </button>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {() => (
          <div class="stack">
            <SettingsResourceToolbar
              searchValue={query()}
              searchPlaceholder="Search prompt variables..."
              onSearchInput={setQuery}
              actions={
                <button class="btn btn-primary" type="button" onClick={openCreate}>
                  <Plus size={14} />
                  New Variable
                </button>
              }
            />

            <section class="panel">
              <DashboardTable
                columns={[
                  { header: "Name" },
                  { header: "Placeholder" },
                  { header: "Value" },
                  { header: "Updated" },
                  { header: "Actions" },
                ]}
                rows={filteredVariables()}
                emptyMessage="No prompt variables found."
              >
                {(variable) => (
                  <tr>
                    <td>
                      <div class="row-wrap" style="gap: 0.5rem; align-items: center;">
                        <div class="mono">{variable.name}</div>
                        <Show when={variable.is_system}>
                          <span class="badge" style="background-color: var(--color-bg-subtle, rgba(255,255,255,0.05)); color: var(--color-text-muted, #a6adc8); font-size: 0.7rem; padding: 0.1rem 0.35rem; border-radius: 4px; border: 1px solid var(--color-border, rgba(255,255,255,0.1)); line-height: 1.2;">System</span>
                        </Show>
                      </div>
                    </td>
                    <td>
                      <CopyButton
                        value={variable.placeholder}
                        class="btn btn-sm"
                        title="Copy placeholder"
                        ariaLabel="Copy placeholder"
                        successMessage="Placeholder copied."
                        failureMessage="Failed to copy placeholder."
                        showIcon
                        iconSize={13}
                      >
                        <span class="mono">{variable.placeholder}</span>
                      </CopyButton>
                    </td>
                    <td>
                      <div class="prompt-variable-preview">
                        {truncateText(variable.value || "", 180) || "-"}
                      </div>
                    </td>
                    <td>{formatDate(variable.updated_at || variable.created_at)}</td>
                    <td>
                      <div class="row-wrap">
                        <Show
                          when={!variable.is_system}
                          fallback={
                            <span style="font-size: 0.8rem; font-style: italic; color: var(--color-text-muted, #a6adc8); opacity: 0.7;">
                              Read-Only
                            </span>
                          }
                        >
                          <button class="btn btn-sm" type="button" onClick={() => openEdit(variable)}>
                            <Edit3 size={13} />
                            Edit
                          </button>
                          <button
                            class="btn btn-sm btn-danger"
                            type="button"
                            onClick={() => setDeleteTargetName(variable.name)}
                          >
                            <Trash2 size={13} />
                            Delete
                          </button>
                        </Show>
                      </div>
                    </td>
                  </tr>
                )}
              </DashboardTable>
            </section>

            <Dialog
              open={modalMode() !== null}
              title={modalMode() === "create" ? "New Prompt Variable" : `Edit ${currentName()}`}
              wide
              onClose={closeModal}
              footer={
                <>
                  <button class="btn" type="button" onClick={closeModal}>
                    Close
                  </button>
                  <button class="btn btn-primary" type="button" onClick={saveVariable}>
                    Save
                  </button>
                </>
              }
            >
              <div class="stack">
                <div class="field">
                  <label>Name</label>
                  <input
                    class="input mono"
                    value={form().name}
                    onInput={(event) => updateForm("name", event.currentTarget.value)}
                  />
                </div>
                <Show when={form().name.trim()}>
                  <div class="field">
                    <label>Placeholder</label>
                    <div class="row-wrap">
                      <span class="chip">{`{{${form().name.trim()}}}`}</span>
                    </div>
                  </div>
                </Show>
                <div class="field">
                  <label>Value</label>
                  <textarea
                    class="textarea mono"
                    rows={14}
                    value={form().value}
                    onInput={(event) => updateForm("value", event.currentTarget.value)}
                  />
                </div>
              </div>
            </Dialog>

            <ConfirmDialog
              open={deleteTargetName() !== null}
              title="Delete Prompt Variable"
              message={<p>Are you sure you want to delete prompt variable <span class="mono">{deleteTargetName()}</span>?</p>}
              confirmLabel="Delete"
              confirmVariant="danger"
              onClose={() => setDeleteTargetName(null)}
              onConfirm={() => void confirmDeleteVariable()}
            />
          </div>
        )}
      </DataGate>
    </SettingsLayout>
  );
}
