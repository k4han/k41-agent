import { createMemo, createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { Check, Copy, Edit3, Plus, RefreshCw, Trash2 } from "lucide-solid";

import { Dialog } from "@/components/Dialog";
import { SettingsResourceToolbar } from "@/components/SettingsResourceToolbar";
import { DataGate } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, deleteJson, postJson, putJson } from "@/lib/api";
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
  const [copyingPlaceholder, setCopyingPlaceholder] = createSignal<string | null>(null);
  const [copiedPlaceholder, setCopiedPlaceholder] = createSignal<string | null>(null);
  const [copyFailedPlaceholder, setCopyFailedPlaceholder] = createSignal<string | null>(null);
  const { showToast } = useToast();

  let copyStatusResetTimer: number | undefined;
  let copyGeneration = 0;

  const clearCopyStatusResetTimer = () => {
    if (copyStatusResetTimer === undefined) {
      return;
    }
    window.clearTimeout(copyStatusResetTimer);
    copyStatusResetTimer = undefined;
  };

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

  const copyPlaceholder = async (placeholder: string) => {
    const generation = (copyGeneration += 1);
    clearCopyStatusResetTimer();
    setCopyingPlaceholder(placeholder);
    setCopiedPlaceholder(null);
    setCopyFailedPlaceholder(null);
    try {
      await navigator.clipboard.writeText(placeholder);
      if (generation !== copyGeneration) {
        return;
      }
      setCopiedPlaceholder(placeholder);
      showToast("Placeholder copied.");
    } catch {
      if (generation !== copyGeneration) {
        return;
      }
      setCopyFailedPlaceholder(placeholder);
      showToast("Failed to copy placeholder.", "error");
    } finally {
      if (generation !== copyGeneration) {
        return;
      }
      setCopyingPlaceholder(null);
      copyStatusResetTimer = window.setTimeout(() => {
        if (generation !== copyGeneration) {
          return;
        }
        setCopiedPlaceholder(null);
        setCopyFailedPlaceholder(null);
        copyStatusResetTimer = undefined;
      }, 2400);
    }
  };

  const saveVariable = async () => {
    const payload = form();
    if (!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(payload.name.trim())) {
      showToast("Prompt variable name is invalid.", "error");
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

  const deleteVariable = async (name: string) => {
    if (!window.confirm(`Delete prompt variable "${name}"?`)) {
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
    }
  };

  onMount(load);
  onCleanup(clearCopyStatusResetTimer);

  return (
    <SettingsLayout
      title="Prompt Variables"
      subtitle="Manage shared text blocks for agent system prompts."
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
              <div class="table-wrap">
                <table class="table">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Placeholder</th>
                      <th>Value</th>
                      <th>Updated</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    <For
                      each={filteredVariables()}
                      fallback={
                        <tr>
                          <td colSpan={5}>
                            <div class="empty">No prompt variables found.</div>
                          </td>
                        </tr>
                      }
                    >
                      {(variable) => (
                        <tr>
                          <td>
                            <div class="mono">{variable.name}</div>
                          </td>
                          <td>
                            <button
                              class="btn btn-sm"
                              type="button"
                              title={
                                copiedPlaceholder() === variable.placeholder
                                  ? "Copied"
                                  : copyFailedPlaceholder() === variable.placeholder
                                    ? "Copy failed"
                                    : "Copy placeholder"
                              }
                              aria-label={
                                copiedPlaceholder() === variable.placeholder
                                  ? "Copied"
                                  : "Copy placeholder"
                              }
                              disabled={
                                copyingPlaceholder() === variable.placeholder ||
                                copiedPlaceholder() === variable.placeholder ||
                                copyFailedPlaceholder() === variable.placeholder
                              }
                              onClick={() => copyPlaceholder(variable.placeholder)}
                            >
                              <Show
                                when={copiedPlaceholder() === variable.placeholder}
                                fallback={<Copy size={13} />}
                              >
                                <Check size={13} />
                              </Show>
                              <span class="mono">{variable.placeholder}</span>
                            </button>
                          </td>
                          <td>
                            <div class="prompt-variable-preview">
                              {truncateText(variable.value || "", 180) || "-"}
                            </div>
                          </td>
                          <td>{formatDate(variable.updated_at || variable.created_at)}</td>
                          <td>
                            <div class="row-wrap">
                              <button class="btn btn-sm" type="button" onClick={() => openEdit(variable)}>
                                <Edit3 size={13} />
                                Edit
                              </button>
                              <button
                                class="btn btn-sm btn-danger"
                                type="button"
                                onClick={() => deleteVariable(variable.name)}
                              >
                                <Trash2 size={13} />
                                Delete
                              </button>
                            </div>
                          </td>
                        </tr>
                      )}
                    </For>
                  </tbody>
                </table>
              </div>
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
          </div>
        )}
      </DataGate>
    </SettingsLayout>
  );
}
