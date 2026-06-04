import { createEffect, createMemo, createSignal, onCleanup, onMount, Show } from "solid-js";
import { useBeforeLeave, useNavigate, useSearchParams } from "@solidjs/router";
import { ArrowLeft, Bot, Save } from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorPanel } from "@/components/State";
import { useToast } from "@/components/Toast";
import { apiFetch, postJson, putJson } from "@/lib/api";
import { uniqueSorted } from "@/lib/utils";
import { SettingsLayout } from "@/pages/settings/SettingsLayout";
import type { AgentsPayload, PromptVariable, PromptVariablesPayload } from "@/types";

import { AgentEditSkeleton } from "./AgentEditSkeleton";
import { AgentEditTabs } from "./AgentEditTabs";
import {
  type AgentForm,
  type AgentTab,
  blankForm,
  cardToForm,
  defaultWorkflow,
  isAgentTab,
  isFormDirty,
} from "./agentForm";
import { buildToolGroups } from "./AgentToolsTab";

type Mode = "create" | "edit" | "view";

const AGENTS_LIST_HREF = "/settings/agents";

export function AgentEditPage(props: { agentName?: string }) {
  const isCreate = !props.agentName;
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams<{ tab?: string }>();
  const { showToast } = useToast();

  const [payload, setPayload] = createSignal<AgentsPayload>();
  const [error, setError] = createSignal("");
  const [form, setForm] = createSignal<AgentForm>(blankForm(""));
  const [initialForm, setInitialForm] = createSignal<AgentForm>(blankForm(""));
  const [mode, setMode] = createSignal<Mode>(isCreate ? "create" : "view");
  const [saving, setSaving] = createSignal(false);
  const [promptVariables, setPromptVariables] = createSignal<PromptVariable[]>([]);
  const [confirmDiscardOpen, setConfirmDiscardOpen] = createSignal(false);
  const [savedRef, setSavedRef] = createSignal(false);
  const [createInitialized, setCreateInitialized] = createSignal(false);

  let pendingRetry: (() => void) | null = null;

  const activeTab = (): AgentTab => {
    const t = searchParams.tab;
    return isAgentTab(t) ? t : "general";
  };

  const setActiveTab = (tab: AgentTab) => {
    if (tab === activeTab()) {
      return;
    }
    setSearchParams({ tab }, { replace: true });
  };

  const readOnly = () => mode() === "view";

  const updateForm = <K extends keyof AgentForm>(key: K, value: AgentForm[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const toggleListValue = (
    key: "tools" | "sub_agents" | "mcp_servers",
    value: string,
    checked: boolean,
  ) => {
    setForm((current) => {
      const values = new Set(current[key]);
      if (checked) {
        values.add(value);
      } else {
        values.delete(value);
      }
      return { ...current, [key]: Array.from(values).sort() };
    });
  };

  const toggleToolGroup = (tools: string[], checked: boolean) => {
    setForm((current) => {
      const values = new Set(current.tools);
      for (const tool of tools) {
        if (checked) {
          values.add(tool);
        } else {
          values.delete(tool);
        }
      }
      return { ...current, tools: Array.from(values).sort() };
    });
  };

  const load = async () => {
    setError("");
    try {
      const data = await apiFetch<AgentsPayload>("/dashboard-api/agents");
      setPayload(data);

      if (isCreate) {
        return;
      }

      const target = data.cards.find((card) => card.name === props.agentName);
      if (!target) {
        setError(`Agent "${props.agentName}" was not found.`);
        return;
      }
      const initial = cardToForm(target);
      setForm(initial);
      setInitialForm(initial);
      setMode(target.editable ? "edit" : "view");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load agent");
    }
  };

  const loadPromptVariables = async () => {
    try {
      const data = await apiFetch<PromptVariablesPayload>("/dashboard-api/prompt-variables");
      setPromptVariables(data.variables || []);
    } catch {
      setPromptVariables([]);
    }
  };

  const isDirty = createMemo(() => isFormDirty(form(), initialForm()));

  const subAgentOptions = createMemo(() =>
    uniqueSorted([
      ...(payload()?.agent_names || []).filter((name) => name !== form().name),
      ...form().sub_agents,
    ]),
  );
  const mcpServerOptions = createMemo(() =>
    uniqueSorted([...(payload()?.mcp_server_options || []), ...form().mcp_servers]),
  );
  const toolGroups = createMemo(() => {
    const p = payload();
    if (!p) {
      return [];
    }
    return buildToolGroups(p, form().tools);
  });
  const totalBuiltInTools = createMemo(() =>
    toolGroups().reduce((total, group) => total + group.tools.length, 0),
  );

  // Initialize create form once payload arrives
  createEffect(() => {
    const p = payload();
    if (isCreate && p && !createInitialized()) {
      const initial = blankForm(defaultWorkflow(p.workflows));
      setForm(initial);
      setInitialForm(initial);
      setCreateInitialized(true);
    }
  });

  const notFound = createMemo(() => {
    const p = payload();
    if (!p || isCreate) {
      return false;
    }
    return !p.cards.some((c) => c.name === props.agentName);
  });

  const handleInsertVariable = (varName: string) => {
    const node = document.querySelector<HTMLTextAreaElement>(
      ".prompt-variable-textarea textarea",
    );
    if (!node) {
      return;
    }
    const value = form().system_prompt;
    const start = node.selectionStart;
    const end = node.selectionEnd;
    const textToInsert = `{{${varName}}}`;
    const nextValue = value.slice(0, start) + textToInsert + value.slice(end);
    updateForm("system_prompt", nextValue);
    const newCursorPos = start + textToInsert.length;
    queueMicrotask(() => {
      node.focus();
      node.setSelectionRange(newCursorPos, newCursorPos);
    });
  };

  const tryNavigateBack = () => {
    if (!isDirty()) {
      navigate(AGENTS_LIST_HREF);
      return;
    }
    setConfirmDiscardOpen(true);
  };

  const saveAgent = async () => {
    const current = form();
    if (mode() === "create" && !current.name.trim()) {
      showToast("Agent name is required.", "error");
      return;
    }
    if (!/^[A-Za-z0-9_-]+$/.test(current.name)) {
      showToast("Agent name is invalid.", "error");
      return;
    }
    if (!current.system_prompt.trim()) {
      showToast("System prompt is required.", "error");
      return;
    }

    setSaving(true);
    try {
      if (mode() === "create") {
        await postJson("/agents/cards", current);
        showToast("Agent created.");
      } else {
        await putJson(`/agents/cards/${encodeURIComponent(current.name)}`, current);
        showToast("Agent updated.");
      }
      setSavedRef(true);
      navigate(AGENTS_LIST_HREF, { replace: true });
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to save agent", "error");
    } finally {
      setSaving(false);
    }
  };

  // In-app navigation guard (back button, sidebar link, etc.)
  useBeforeLeave((e) => {
    if (savedRef() || !isDirty() || confirmDiscardOpen()) {
      return;
    }
    e.preventDefault();
    pendingRetry = e.retry;
    setConfirmDiscardOpen(true);
  });

  // Browser tab close / hard refresh guard
  const beforeUnload = (event: BeforeUnloadEvent) => {
    if (savedRef() || !isDirty()) {
      return;
    }
    event.preventDefault();
    event.returnValue = "";
  };

  onMount(() => {
    window.addEventListener("beforeunload", beforeUnload);
    void load();
    void loadPromptVariables();
  });
  onCleanup(() => {
    window.removeEventListener("beforeunload", beforeUnload);
  });

  const title = () => {
    if (isCreate) {
      return "New Agent";
    }
    const p = payload();
    const card = p?.cards.find((c) => c.name === props.agentName);
    return card?.display_name || props.agentName || "Agent";
  };

  const breadcrumbSegments = createMemo(() => {
    if (isCreate) {
      return [
        { label: "Agents", href: AGENTS_LIST_HREF },
        { label: "New" },
      ];
    }
    return [
      { label: "Agents", href: AGENTS_LIST_HREF },
      { label: props.agentName || title() },
    ];
  });

  const body = createMemo(() => {
    const p = payload();
    const err = error();
    if (!p && !err) {
      return <AgentEditSkeleton loading />;
    }
    if (!p && err) {
      return <ErrorPanel message={err} onRetry={load} />;
    }
    if (notFound()) {
      return (
        <AgentEditSkeleton
          icon={<Bot size={20} />}
          title={`Agent "${props.agentName}" was not found`}
          description="The agent may have been deleted or renamed."
          actions={
            <button class="btn" type="button" onClick={() => navigate(AGENTS_LIST_HREF)}>
              <ArrowLeft size={14} />
              Back to agents
            </button>
          }
        />
      );
    }
    return (
      <AgentEditTabs
        form={form()}
        readOnly={readOnly()}
        payload={p!}
        promptVariables={promptVariables()}
        activeTab={activeTab()}
        onTabChange={setActiveTab}
        toolGroups={toolGroups()}
        totalBuiltInTools={totalBuiltInTools()}
        mcpServerOptions={mcpServerOptions()}
        subAgentOptions={subAgentOptions()}
        onUpdate={updateForm}
        onToggleListValue={toggleListValue}
        onToggleToolGroup={toggleToolGroup}
        onInsertVariable={handleInsertVariable}
      />
    );
  });

  return (
    <SettingsLayout
      title={title()}
      breadcrumbSegments={breadcrumbSegments()}
      contentWidth="wide"
      actions={
        <Show when={!readOnly() && payload()}>
          <button
            class="btn btn-primary"
            type="button"
            onClick={saveAgent}
            disabled={saving() || !isDirty()}
          >
            <Save size={14} />
            {saving() ? "Saving..." : "Save"}
          </button>
        </Show>
      }
    >
      {body()}

      <ConfirmDialog
        open={confirmDiscardOpen()}
        title="Discard unsaved changes?"
        message={<p>You have unsaved changes. Leaving this page will discard them.</p>}
        confirmLabel="Discard"
        confirmVariant="danger"
        onClose={() => {
          setConfirmDiscardOpen(false);
          pendingRetry = null;
        }}
        onConfirm={() => {
          setConfirmDiscardOpen(false);
          setSavedRef(true);
          if (pendingRetry) {
            const retry = pendingRetry;
            pendingRetry = null;
            retry();
          } else {
            navigate(AGENTS_LIST_HREF);
          }
        }}
      />
    </SettingsLayout>
  );
}
