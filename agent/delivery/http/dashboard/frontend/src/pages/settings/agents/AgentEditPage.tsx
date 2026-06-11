import { createEffect, createMemo, createSignal, onCleanup, onMount, Show } from "solid-js";
import { useBeforeLeave, useNavigate, useSearchParams } from "@solidjs/router";
import { ArrowLeft, Bot, Save } from "lucide-solid";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorPanel } from "@/components/State";
import { useToast } from "@/components/Toast";
import { API_PATHS } from "@/lib/endpoints";
import { apiFetch, postJson, putJson } from "@/lib/api";
import { uniqueSorted } from "@/lib/utils";
import { SettingsLayout } from "@/pages/settings/SettingsLayout";
import type { AgentsPayload, PromptVariable, PromptVariablesPayload } from "@/types";

import { AgentEditSkeleton } from "./AgentEditSkeleton";
import { AgentEditTabs } from "./AgentEditTabs";
import {
  type AgentForm,
  type AgentTab,
  type ToolConfigValue,
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
  const [mcpUpdating, setMcpUpdating] = createSignal(false);

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
    key: "tools" | "sub_agents" | "plan_approval_targets",
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
      if (key !== "tools" || checked) {
        return { ...current, [key]: Array.from(values).sort() };
      }
      const tool_configs = { ...current.tool_configs };
      delete tool_configs[value];
      return { ...current, [key]: Array.from(values).sort(), tool_configs };
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
      if (checked) {
        return { ...current, tools: Array.from(values).sort() };
      }
      const tool_configs = { ...current.tool_configs };
      for (const tool of tools) {
        delete tool_configs[tool];
      }
      return { ...current, tools: Array.from(values).sort(), tool_configs };
    });
  };

  const updateToolConfig = (
    toolName: string,
    fieldName: string,
    value: ToolConfigValue,
  ) => {
    setForm((current) => {
      const toolConfigs = { ...current.tool_configs };
      const fields = { ...(toolConfigs[toolName] || {}) };
      fields[fieldName] = value;
      toolConfigs[toolName] = fields;
      return { ...current, tool_configs: toolConfigs };
    });
  };

  const resetToolConfigField = (toolName: string, fieldName: string) => {
    setForm((current) => {
      const toolConfigs = { ...current.tool_configs };
      const fields = { ...(toolConfigs[toolName] || {}) };
      delete fields[fieldName];
      if (Object.keys(fields).length > 0) {
        toolConfigs[toolName] = fields;
      } else {
        delete toolConfigs[toolName];
      }
      return { ...current, tool_configs: toolConfigs };
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
  const planApprovalTargetOptions = createMemo(() =>
    uniqueSorted([
      ...(payload()?.cards || [])
        .filter((card) => card.valid && !card.hidden && card.name !== form().name)
        .map((card) => card.name),
      ...form().plan_approval_targets.filter((name) => name !== form().name),
    ]),
  );
  const mcpInstalls = createMemo(() => {
    const name = props.agentName || form().name;
    if (!name) {
      return [];
    }
    return payload()?.mcp_installs?.[name] || [];
  });
  const mcpServerOptions = createMemo(() =>
    uniqueSorted([
      ...(payload()?.mcp_server_options || []),
      ...(mcpInstalls().map((install) => install.server_name) || []),
    ]),
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
    const currentForm = form();
    const current: AgentForm = {
      ...currentForm,
      tool_configs: Object.fromEntries(
        Object.entries(currentForm.tool_configs).filter(
          ([toolName, fields]) => currentForm.tools.includes(toolName) && Object.keys(fields).length > 0,
        ),
      ),
    };
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

  const toggleMcpInstall = async (serverName: string, checked: boolean) => {
    const name = props.agentName || form().name;
    if (!name) {
      showToast("Save the agent before enabling MCP servers.", "warning");
      return;
    }
    const install = mcpInstalls().find((item) => item.server_name === serverName);
    setMcpUpdating(true);
    try {
      if (install) {
        await putJson(API_PATHS.mcpAgentInstallToggle(name, install.install_id), {
          enabled: checked,
        });
      } else if (checked) {
        await postJson(API_PATHS.mcpAgentInstallBind(name), {
          server_name: serverName,
          enabled: true,
        });
      }
      showToast(`${checked ? "Enabled" : "Disabled"} MCP server "${serverName}".`);
      await load();
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Failed to update MCP server binding",
        "error",
      );
    } finally {
      setMcpUpdating(false);
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
        mcpInstalls={mcpInstalls()}
        mcpUpdating={mcpUpdating()}
        subAgentOptions={subAgentOptions()}
        planApprovalTargetOptions={planApprovalTargetOptions()}
        onUpdate={updateForm}
        onToggleListValue={toggleListValue}
        onToggleToolGroup={toggleToolGroup}
        onToggleMcpInstall={toggleMcpInstall}
        onUpdateToolConfig={updateToolConfig}
        onResetToolConfigField={resetToolConfigField}
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
