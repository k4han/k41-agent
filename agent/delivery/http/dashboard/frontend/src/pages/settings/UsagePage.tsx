import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import { RefreshCw } from "lucide-solid";

import { MetricGrid } from "@/components/Metrics";
import { SelectControl } from "@/components/SelectControl";
import { DataGate } from "@/components/State";
import { apiFetch } from "@/lib/api";
import type { UsagePayload, UsageRow } from "@/types";

import { SettingsLayout } from "./SettingsLayout";

const PAGE_SIZE = 50;

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function dateInputValue(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function defaultStartDate(): string {
  const date = new Date();
  date.setDate(date.getDate() - 6);
  return dateInputValue(date);
}

function defaultEndDate(): string {
  return dateInputValue(new Date());
}

function startIso(value: string): string {
  return new Date(`${value}T00:00:00`).toISOString();
}

function endIso(value: string): string {
  return new Date(`${value}T23:59:59.999`).toISOString();
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value || 0);
}

function formatDateTime(value: string | null): string {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function optionKey(parts: Array<string | undefined>): string {
  return parts.map((part) => part || "").join("\t");
}

function parseOptionKey(value: string): string[] {
  return value.split("\t");
}

function resetOffsetOnChange(setter: (value: string) => void, setOffset: (value: number) => void) {
  return (value: string) => {
    setter(value);
    setOffset(0);
  };
}

export function UsagePage() {
  const [data, setData] = createSignal<UsagePayload>();
  const [error, setError] = createSignal("");
  const [startDate, setStartDate] = createSignal(defaultStartDate());
  const [endDate, setEndDate] = createSignal(defaultEndDate());
  const [platform, setPlatform] = createSignal("");
  const [userId, setUserId] = createSignal("");
  const [channelId, setChannelId] = createSignal("");
  const [agent, setAgent] = createSignal("");
  const [provider, setProvider] = createSignal("");
  const [model, setModel] = createSignal("");
  const [offset, setOffset] = createSignal(0);
  const [activeTab, setActiveTab] = createSignal<"users" | "workspaces" | "threads">("users");

  const load = async () => {
    setError("");
    try {
      const params = new URLSearchParams({
        start: startIso(startDate()),
        end: endIso(endDate()),
        limit: String(PAGE_SIZE),
        offset: String(offset()),
      });
      if (platform()) params.set("platform", platform());
      if (userId()) params.set("user_id", userId());
      if (channelId()) params.set("channel_id", channelId());
      if (agent()) params.set("agent", agent());
      if (provider()) params.set("provider", provider());
      if (model()) params.set("model", model());
      setData(await apiFetch<UsagePayload>(`/dashboard-api/usage?${params.toString()}`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load usage");
    }
  };

  const selectedUser = createMemo(() =>
    userId() ? optionKey([platform(), userId()]) : optionKey(["", ""]),
  );
  const selectedChannel = createMemo(() =>
    channelId() ? optionKey([platform(), userId(), channelId()]) : optionKey(["", "", ""]),
  );

  const setSelectedUser = (value: string) => {
    const [nextPlatform, nextUserId] = parseOptionKey(value);
    setPlatform(nextPlatform || "");
    setUserId(nextUserId || "");
    setChannelId("");
    setOffset(0);
  };

  const setSelectedChannel = (value: string) => {
    const [nextPlatform, nextUserId, nextChannelId] = parseOptionKey(value);
    setPlatform(nextPlatform || "");
    setUserId(nextUserId || "");
    setChannelId(nextChannelId || "");
    setOffset(0);
  };

  const clearFilters = () => {
    setPlatform("");
    setUserId("");
    setChannelId("");
    setAgent("");
    setProvider("");
    setModel("");
    setOffset(0);
  };

  const nextPage = async () => {
    const next = data()?.pagination.next_offset;
    if (next === null || next === undefined) {
      return;
    }
    setOffset(next);
    await load();
  };

  const previousPage = async () => {
    setOffset(Math.max(0, offset() - PAGE_SIZE));
    await load();
  };

  onMount(load);

  return (
    <SettingsLayout
      title="Usage"
      subtitle="Inspect token usage by user, channel, provider, model, and agent."
      breadcrumbLabel="Usage"
      contentWidth="wide"
      actions={
        <button class="btn btn-primary" type="button" onClick={load}>
          <RefreshCw size={14} />
          Refresh
        </button>
      }
    >
      <DataGate data={data()} error={error()} onRetry={load}>
        {(payload) => (
          <div class="stack">
            <MetricGrid
              items={[
                { label: "Total tokens", value: formatNumber(payload.summary.total_tokens) },
                { label: "Input tokens", value: formatNumber(payload.summary.input_tokens) },
                { label: "Output tokens", value: formatNumber(payload.summary.output_tokens) },
              ]}
            />
            <MetricGrid
              items={[
                { label: "LLM calls", value: formatNumber(payload.summary.event_count) },
                { label: "Missing usage", value: formatNumber(payload.summary.missing_usage_count) },
                { label: "Internal calls", value: formatNumber(payload.summary.internal_event_count) },
              ]}
            />

            <section class="panel">
              <div class="panel-header">
                <div class="panel-title">Filters</div>
                <button class="btn btn-sm" type="button" onClick={clearFilters}>
                  Clear
                </button>
              </div>
              <div class="panel-body usage-filter-grid">
                <label class="field">
                  <span>Start</span>
                  <input
                    class="input"
                    type="date"
                    value={startDate()}
                    onInput={(event) => {
                      setStartDate(event.currentTarget.value);
                      setOffset(0);
                    }}
                  />
                </label>
                <label class="field">
                  <span>End</span>
                  <input
                    class="input"
                    type="date"
                    value={endDate()}
                    onInput={(event) => {
                      setEndDate(event.currentTarget.value);
                      setOffset(0);
                    }}
                  />
                </label>
                <label class="field">
                  <span>Platform</span>
                  <SelectControl
                    value={platform()}
                    options={[
                      { value: "", label: "All platforms" },
                      ...payload.filters.platforms.map((item) => ({ value: item, label: item })),
                    ]}
                    onChange={(value) => {
                      setPlatform(value);
                      setUserId("");
                      setChannelId("");
                      setOffset(0);
                    }}
                    ariaLabel="Platform"
                  />
                </label>
                <label class="field">
                  <span>User</span>
                  <SelectControl
                    value={selectedUser()}
                    options={[
                      { value: optionKey(["", ""]), label: "All users" },
                      ...payload.filters.users.map((item) => ({
                        value: optionKey([item.platform, item.user_id]),
                        label: item.label,
                      })),
                    ]}
                    onChange={setSelectedUser}
                    ariaLabel="User"
                  />
                </label>
                <label class="field">
                  <span>Channel</span>
                  <SelectControl
                    value={selectedChannel()}
                    options={[
                      { value: optionKey(["", "", ""]), label: "All channels" },
                      ...payload.filters.channels.map((item) => ({
                        value: optionKey([item.platform, item.user_id, item.channel_id]),
                        label: item.label,
                      })),
                    ]}
                    onChange={setSelectedChannel}
                    ariaLabel="Channel"
                  />
                </label>
                <label class="field">
                  <span>Agent</span>
                  <SelectControl
                    value={agent()}
                    options={[
                      { value: "", label: "All agents" },
                      ...payload.filters.agents.map((item) => ({ value: item, label: item })),
                    ]}
                    onChange={resetOffsetOnChange(setAgent, setOffset)}
                    ariaLabel="Agent"
                  />
                </label>
                <label class="field">
                  <span>Provider</span>
                  <SelectControl
                    value={provider()}
                    options={[
                      { value: "", label: "All providers" },
                      ...payload.filters.providers.map((item) => ({ value: item, label: item })),
                    ]}
                    onChange={resetOffsetOnChange(setProvider, setOffset)}
                    ariaLabel="Provider"
                  />
                </label>
                <label class="field">
                  <span>Model</span>
                  <SelectControl
                    value={model()}
                    options={[
                      { value: "", label: "All models" },
                      ...payload.filters.models.map((item) => ({ value: item, label: item })),
                    ]}
                    onChange={resetOffsetOnChange(setModel, setOffset)}
                    ariaLabel="Model"
                  />
                </label>
              </div>
              <div class="panel-body">
                <button class="btn" type="button" onClick={load}>
                  Apply Filters
                </button>
              </div>
            </section>

            <div class="workspace-tabs" role="tablist" style="margin-bottom: 16px; border-bottom: 1px solid rgba(255, 255, 255, 0.08); display: flex; gap: 8px;">
              <button
                class={`workspace-tab ${activeTab() === "users" ? "active" : ""}`}
                type="button"
                role="tab"
                onClick={() => setActiveTab("users")}
                style={`padding: 10px 16px; font-weight: 500; font-size: 13px; color: ${activeTab() === "users" ? "var(--color-primary, #3b82f6)" : "#888"}; border-bottom: 2px solid ${activeTab() === "users" ? "var(--color-primary, #3b82f6)" : "transparent"}; background: none; border-top: none; border-left: none; border-right: none; cursor: pointer; transition: all 0.2s;`}
              >
                User & Channel
              </button>
              <button
                class={`workspace-tab ${activeTab() === "workspaces" ? "active" : ""}`}
                type="button"
                role="tab"
                onClick={() => setActiveTab("workspaces")}
                style={`padding: 10px 16px; font-weight: 500; font-size: 13px; color: ${activeTab() === "workspaces" ? "var(--color-primary, #3b82f6)" : "#888"}; border-bottom: 2px solid ${activeTab() === "workspaces" ? "var(--color-primary, #3b82f6)" : "transparent"}; background: none; border-top: none; border-left: none; border-right: none; cursor: pointer; transition: all 0.2s;`}
              >
                Workspaces
              </button>
              <button
                class={`workspace-tab ${activeTab() === "threads" ? "active" : ""}`}
                type="button"
                role="tab"
                onClick={() => setActiveTab("threads")}
                style={`padding: 10px 16px; font-weight: 500; font-size: 13px; color: ${activeTab() === "threads" ? "var(--color-primary, #3b82f6)" : "#888"}; border-bottom: 2px solid ${activeTab() === "threads" ? "var(--color-primary, #3b82f6)" : "transparent"}; background: none; border-top: none; border-left: none; border-right: none; cursor: pointer; transition: all 0.2s;`}
              >
                Conversations / Threads
              </button>
            </div>

            <Show when={activeTab() === "users"}>
              <UsageTable rows={payload.rows} />
            </Show>
            <Show when={activeTab() === "workspaces"}>
              <WorkspaceUsageTable list={payload.workspaces || []} />
            </Show>
            <Show when={activeTab() === "threads"}>
              <ThreadUsageTable list={payload.threads || []} />
            </Show>

            <div class="usage-pagination">
              <span class="hint">
                Showing {payload.pagination.offset + 1}-
                {Math.min(payload.pagination.offset + payload.rows.length, payload.pagination.total)} of{" "}
                {payload.pagination.total}
              </span>
              <div class="row-wrap">
                <button class="btn btn-sm" type="button" disabled={payload.pagination.offset === 0} onClick={previousPage}>
                  Previous
                </button>
                <button class="btn btn-sm" type="button" disabled={!payload.pagination.has_more} onClick={nextPage}>
                  Next
                </button>
              </div>
            </div>
          </div>
        )}
      </DataGate>
    </SettingsLayout>
  );
}

function UsageTable(props: { rows: UsageRow[] }) {
  return (
    <section class="panel">
      <div class="panel-header">
        <div class="panel-title">Usage by User and Channel</div>
      </div>
      <div class="table-wrap">
        <table class="table usage-table">
          <thead>
            <tr>
              <th>User / Channel</th>
              <th>Calls</th>
              <th>Total</th>
              <th>Input</th>
              <th>Output</th>
              <th>Missing</th>
              <th>Last used</th>
            </tr>
          </thead>
          <tbody>
            <For each={props.rows} fallback={<tr><td colSpan={7} class="empty">No usage recorded.</td></tr>}>
              {(row) => (
                <tr>
                  <td>
                    <div>{row.identity_label}</div>
                    <div class="mono hint">
                      {row.platform}:{row.user_id}
                      <Show when={row.channel_id}>:{row.channel_id}</Show>
                    </div>
                  </td>
                  <td>{formatNumber(row.event_count)}</td>
                  <td>{formatNumber(row.total_tokens)}</td>
                  <td>{formatNumber(row.input_tokens)}</td>
                  <td>{formatNumber(row.output_tokens)}</td>
                  <td>{formatNumber(row.missing_usage_count)}</td>
                  <td>{formatDateTime(row.last_used_at)}</td>
                </tr>
              )}
            </For>
          </tbody>
        </table>
      </div>
    </section>
  );
}

import type { WorkspaceUsageDetail, ThreadUsageDetail } from "@/types";

function WorkspaceUsageTable(props: { list: WorkspaceUsageDetail[] }) {
  return (
    <section class="panel">
      <div class="panel-header">
        <div class="panel-title">Usage by Workspace / Project</div>
      </div>
      <div class="table-wrap">
        <table class="table usage-table">
          <thead>
            <tr>
              <th style="width: 35%;">Workspace Directory</th>
              <th style="width: 10%;">Threads</th>
              <th style="width: 10%;">Calls</th>
              <th style="width: 15%;">Total Tokens</th>
              <th style="width: 20%;">Model Breakdown</th>
              <th style="width: 10%;">Last used</th>
            </tr>
          </thead>
          <tbody>
            <For each={props.list} fallback={<tr><td colSpan={6} class="empty">No workspace usage recorded.</td></tr>}>
              {(row) => (
                <tr>
                  <td>
                    <div style="font-weight: 600; color: #fff;">{row.label}</div>
                    <div class="mono hint" style="font-size: 10px; color: #888; text-overflow: ellipsis; overflow: hidden; white-space: nowrap; max-width: 280px;" title={row.locator}>
                      {row.backend}:{row.locator}
                    </div>
                  </td>
                  <td>{formatNumber(row.thread_count)}</td>
                  <td>{formatNumber(row.event_count)}</td>
                  <td>
                    <div style="font-weight: 600; color: #3b82f6;">{formatNumber(row.total_tokens)}</div>
                    <div class="hint" style="font-size: 10px; color: #888;">In: {formatNumber(row.input_tokens)} / Out: {formatNumber(row.output_tokens)}</div>
                  </td>
                  <td>
                    <div class="stack" style="gap: 4px; padding: 4px 0;">
                      <div class="row-wrap" style="height: 4px; border-radius: 2px; overflow: hidden; background: rgba(255, 255, 255, 0.08); gap: 1px; width: 100%;">
                        <For each={row.models}>
                          {(item, index) => {
                            const colors = ["#3b82f6", "#10b981", "#8b5cf6", "#f59e0b", "#ec4899"];
                            const color = colors[index() % colors.length];
                            return (
                              <div
                                style={`width: ${item.percentage}%; background-color: ${color}; height: 100%;`}
                                title={`${item.model}: ${item.percentage}%`}
                              />
                            );
                          }}
                        </For>
                      </div>
                      <div style="display: flex; flex-direction: column; gap: 2px; font-size: 10px; color: #aaa;">
                        <For each={row.models.slice(0, 3)}>
                          {(item) => (
                            <div class="row-wrap" style="justify-content: space-between;">
                              <span style="text-overflow: ellipsis; overflow: hidden; white-space: nowrap; max-width: 120px;" title={item.model}>{item.model}</span>
                              <span style="font-weight: 600;">{item.percentage}%</span>
                            </div>
                          )}
                        </For>
                        <Show when={row.models.length > 3}>
                          <div style="color: #666; font-size: 9px;">+ {row.models.length - 3} more models</div>
                        </Show>
                      </div>
                    </div>
                  </td>
                  <td>{formatDateTime(row.last_used_at)}</td>
                </tr>
              )}
            </For>
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ThreadUsageTable(props: { list: ThreadUsageDetail[] }) {
  return (
    <section class="panel">
      <div class="panel-header">
        <div class="panel-title">Usage by Conversation / Thread</div>
      </div>
      <div class="table-wrap">
        <table class="table usage-table">
          <thead>
            <tr>
              <th style="width: 35%;">Conversation Thread</th>
              <th style="width: 10%;">Agent</th>
              <th style="width: 10%;">Calls</th>
              <th style="width: 15%;">Total Tokens</th>
              <th style="width: 20%;">Model Breakdown</th>
              <th style="width: 10%;">Last used</th>
            </tr>
          </thead>
          <tbody>
            <For each={props.list} fallback={<tr><td colSpan={6} class="empty">No conversation usage recorded.</td></tr>}>
              {(row) => (
                <tr>
                  <td>
                    <div style="font-weight: 600; color: #fff; text-overflow: ellipsis; overflow: hidden; white-space: nowrap; max-width: 250px;" title={row.title}>
                      {row.title}
                    </div>
                    <div class="mono hint" style="font-size: 10px; color: #888;">{row.thread_id}</div>
                  </td>
                  <td><span class="badge" style="font-size: 10px; padding: 2px 6px;">{row.agent_name}</span></td>
                  <td>{formatNumber(row.event_count)}</td>
                  <td>
                    <div style="font-weight: 600; color: #3b82f6;">{formatNumber(row.total_tokens)}</div>
                    <div class="hint" style="font-size: 10px; color: #888;">In: {formatNumber(row.input_tokens)} / Out: {formatNumber(row.output_tokens)}</div>
                  </td>
                  <td>
                    <div class="stack" style="gap: 4px; padding: 4px 0;">
                      <div class="row-wrap" style="height: 4px; border-radius: 2px; overflow: hidden; background: rgba(255, 255, 255, 0.08); gap: 1px; width: 100%;">
                        <For each={row.models}>
                          {(item, index) => {
                            const colors = ["#3b82f6", "#10b981", "#8b5cf6", "#f59e0b", "#ec4899"];
                            const color = colors[index() % colors.length];
                            return (
                              <div
                                style={`width: ${item.percentage}%; background-color: ${color}; height: 100%;`}
                                title={`${item.model}: ${item.percentage}%`}
                              />
                            );
                          }}
                        </For>
                      </div>
                      <div style="display: flex; flex-direction: column; gap: 2px; font-size: 10px; color: #aaa;">
                        <For each={row.models.slice(0, 3)}>
                          {(item) => (
                            <div class="row-wrap" style="justify-content: space-between;">
                              <span style="text-overflow: ellipsis; overflow: hidden; white-space: nowrap; max-width: 120px;" title={item.model}>{item.model}</span>
                              <span style="font-weight: 600;">{item.percentage}%</span>
                            </div>
                          )}
                        </For>
                        <Show when={row.models.length > 3}>
                          <div style="color: #666; font-size: 9px;">+ {row.models.length - 3} more models</div>
                        </Show>
                      </div>
                    </div>
                  </td>
                  <td>{formatDateTime(row.last_used_at)}</td>
                </tr>
              )}
            </For>
          </tbody>
        </table>
      </div>
    </section>
  );
}
