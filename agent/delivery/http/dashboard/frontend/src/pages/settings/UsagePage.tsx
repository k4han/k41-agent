import { createMemo, createSignal, For, onMount, Show } from "solid-js";
import { RefreshCw } from "lucide-solid";

import { DashboardTable } from "@/components/DashboardTable";
import { MetricGrid } from "@/components/Metrics";
import { SelectControl } from "@/components/SelectControl";
import { DataGate } from "@/components/State";
import { apiFetch } from "@/lib/api";
import type { ThreadUsageDetail, UsagePayload, UsageRow, WorkspaceUsageDetail } from "@/types";

import { SettingsLayout } from "./SettingsLayout";

const PAGE_SIZE = 50;
type UsageTab = "users" | "workspaces" | "threads";

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

function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value || 0);
}

function formatDateTime(value: string | null, timeZone?: string): string {
  if (!value) {
    return "-";
  }
  const options: Intl.DateTimeFormatOptions = {
    dateStyle: "medium",
    timeStyle: "short",
  };
  if (timeZone) {
    options.timeZone = timeZone;
  }
  try {
    return new Intl.DateTimeFormat(undefined, options).format(new Date(value));
  } catch {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(value));
  }
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

function paginationStart(payload: UsagePayload): number {
  return payload.pagination.total > 0 ? payload.pagination.offset + 1 : 0;
}

function paginationEnd(payload: UsagePayload): number {
  return Math.min(payload.pagination.offset + payload.rows.length, payload.pagination.total);
}

function threadSearchText(row: ThreadUsageDetail): string {
  return [
    row.thread_id,
    row.title,
    row.agent_name,
    ...row.models.flatMap((item) => [item.provider, item.model]),
  ].join(" ").toLowerCase();
}

function filterThreads(rows: ThreadUsageDetail[], query: string): ThreadUsageDetail[] {
  const normalizedQuery = query.trim().toLowerCase();
  if (!normalizedQuery) {
    return rows;
  }
  return rows.filter((row) => threadSearchText(row).includes(normalizedQuery));
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
  const [callKind, setCallKind] = createSignal("");
  const [internalFilter, setInternalFilter] = createSignal("");
  const [threadSearch, setThreadSearch] = createSignal("");
  const [offset, setOffset] = createSignal(0);
  const [activeTab, setActiveTab] = createSignal<UsageTab>("users");

  const load = async (nextOffset = offset(), nextView: UsageTab = activeTab()) => {
    setError("");
    try {
      const params = new URLSearchParams({
        start: startDate(),
        end: endDate(),
        limit: String(PAGE_SIZE),
        offset: String(nextOffset),
        view: nextView,
      });
      if (platform()) params.set("platform", platform());
      if (userId()) params.set("user_id", userId());
      if (channelId()) params.set("channel_id", channelId());
      if (agent()) params.set("agent", agent());
      if (provider()) params.set("provider", provider());
      if (model()) params.set("model", model());
      if (callKind()) params.set("call_kind", callKind());
      if (internalFilter()) params.set("internal", internalFilter());
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
    setCallKind("");
    setInternalFilter("");
    setOffset(0);
  };

  const nextPage = async () => {
    const next = data()?.pagination.next_offset;
    if (next === null || next === undefined) {
      return;
    }
    setOffset(next);
    await load(next);
  };

  const previousPage = async () => {
    const previous = Math.max(0, offset() - PAGE_SIZE);
    setOffset(previous);
    await load(previous);
  };

  const switchTab = (tab: UsageTab) => {
    const scrollY = window.scrollY;
    setActiveTab(tab);
    window.requestAnimationFrame(() => window.scrollTo({ top: scrollY }));
    void load(tab === "users" ? offset() : 0, tab).finally(() => {
      window.requestAnimationFrame(() => window.scrollTo({ top: scrollY }));
    });
  };

  onMount(load);

  return (
    <SettingsLayout
      title="Usage"
      subtitle="Inspect token usage by user, channel, provider, model, and agent."
      breadcrumbLabel="Usage"
      contentWidth="wide"
      actions={
        <button class="btn btn-primary" type="button" onClick={() => load()}>
          <RefreshCw size={14} />
          Refresh
        </button>
      }
    >
      <DataGate data={data()} error={error()} onRetry={() => load()}>
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
                <label class="field">
                  <span>Call kind</span>
                  <SelectControl
                    value={callKind()}
                    options={[
                      { value: "", label: "All call kinds" },
                      ...(payload.filters.call_kinds || []).map((item) => ({ value: item, label: item })),
                    ]}
                    onChange={resetOffsetOnChange(setCallKind, setOffset)}
                    ariaLabel="Call kind"
                  />
                </label>
                <label class="field">
                  <span>Scope</span>
                  <SelectControl
                    value={internalFilter()}
                    options={[
                      { value: "", label: "All calls" },
                      { value: "false", label: "User-visible only" },
                      { value: "true", label: "Internal only" },
                    ]}
                    onChange={resetOffsetOnChange(setInternalFilter, setOffset)}
                    ariaLabel="Scope"
                  />
                </label>
              </div>
              <div class="panel-body">
                <button class="btn" type="button" onClick={() => load()}>
                  Apply Filters
                </button>
              </div>
            </section>

            <div class="workspace-tabs" role="tablist" style="margin-bottom: 16px; border-bottom: 1px solid rgba(255, 255, 255, 0.08); display: flex; gap: 8px;">
              <button
                class={`workspace-tab ${activeTab() === "users" ? "active" : ""}`}
                type="button"
                role="tab"
                onClick={() => switchTab("users")}
                style={`padding: 10px 16px; font-weight: 500; font-size: 13px; color: ${activeTab() === "users" ? "var(--color-primary, #3b82f6)" : "#888"}; border-bottom: 2px solid ${activeTab() === "users" ? "var(--color-primary, #3b82f6)" : "transparent"}; background: none; border-top: none; border-left: none; border-right: none; cursor: pointer; transition: all 0.2s;`}
              >
                User & Channel
              </button>
              <button
                class={`workspace-tab ${activeTab() === "workspaces" ? "active" : ""}`}
                type="button"
                role="tab"
                onClick={() => switchTab("workspaces")}
                style={`padding: 10px 16px; font-weight: 500; font-size: 13px; color: ${activeTab() === "workspaces" ? "var(--color-primary, #3b82f6)" : "#888"}; border-bottom: 2px solid ${activeTab() === "workspaces" ? "var(--color-primary, #3b82f6)" : "transparent"}; background: none; border-top: none; border-left: none; border-right: none; cursor: pointer; transition: all 0.2s;`}
              >
                Workspaces
              </button>
              <button
                class={`workspace-tab ${activeTab() === "threads" ? "active" : ""}`}
                type="button"
                role="tab"
                onClick={() => switchTab("threads")}
                style={`padding: 10px 16px; font-weight: 500; font-size: 13px; color: ${activeTab() === "threads" ? "var(--color-primary, #3b82f6)" : "#888"}; border-bottom: 2px solid ${activeTab() === "threads" ? "var(--color-primary, #3b82f6)" : "transparent"}; background: none; border-top: none; border-left: none; border-right: none; cursor: pointer; transition: all 0.2s;`}
              >
                Conversations / Threads
              </button>
            </div>

            <Show when={activeTab() === "users"}>
              <UsageTable rows={payload.rows} displayTimezone={payload.display_timezone} />
            </Show>
            <Show when={activeTab() === "workspaces"}>
              <WorkspaceUsageTable
                list={payload.workspaces || []}
                displayTimezone={payload.display_timezone}
              />
            </Show>
            <Show when={activeTab() === "threads"}>
              <ThreadUsageTable
                list={filterThreads(payload.threads || [], threadSearch())}
                search={threadSearch()}
                onSearch={setThreadSearch}
                displayTimezone={payload.display_timezone}
              />
            </Show>

            <Show when={activeTab() === "users"}>
              <div class="usage-pagination">
                <span class="hint">
                  Showing {paginationStart(payload)}-{paginationEnd(payload)} of {payload.pagination.total}
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
            </Show>
          </div>
        )}
      </DataGate>
    </SettingsLayout>
  );
}

function UsageTable(props: { rows: UsageRow[]; displayTimezone?: string }) {
  return (
    <section class="panel">
      <div class="panel-header">
        <div class="panel-title">Usage by User and Channel</div>
      </div>
      <DashboardTable
        columns={[
          { header: "User / Channel" },
          { header: "Calls" },
          { header: "Total" },
          { header: "Input" },
          { header: "Output" },
          { header: "Missing" },
          { header: "Last used" },
        ]}
        rows={props.rows}
        tableClass="usage-table"
        emptyMessage="No usage recorded."
      >
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
            <td>{formatDateTime(row.last_used_at, props.displayTimezone)}</td>
          </tr>
        )}
      </DashboardTable>
    </section>
  );
}

function WorkspaceUsageTable(props: { list: WorkspaceUsageDetail[]; displayTimezone?: string }) {
  return (
    <section class="panel">
      <div class="panel-header">
        <div class="panel-title">Usage by Workspace / Project</div>
      </div>
      <DashboardTable
        columns={[
          { header: "Workspace Directory", style: "width: 30%;" },
          { header: "Threads", style: "width: 10%;" },
          { header: "Calls", style: "width: 10%;" },
          { header: "Total Tokens", style: "width: 15%;" },
          { header: "Model Breakdown", style: "width: 20%;" },
          { header: "Last used", style: "width: 15%;" },
        ]}
        rows={props.list}
        tableClass="usage-table"
        emptyMessage="No workspace usage recorded."
      >
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
            <td>{formatDateTime(row.last_used_at, props.displayTimezone)}</td>
          </tr>
        )}
      </DashboardTable>
    </section>
  );
}

function ThreadUsageTable(props: {
  list: ThreadUsageDetail[];
  search: string;
  onSearch: (value: string) => void;
  displayTimezone?: string;
}) {
  return (
    <section class="panel">
      <div class="panel-header">
        <div class="panel-title">Usage by Conversation / Thread</div>
        <label class="field" style="min-width: 260px; margin: 0;">
          <span>Search thread</span>
          <input
            class="input"
            type="search"
            value={props.search}
            placeholder="Title, thread id, agent, or model"
            onInput={(event) => props.onSearch(event.currentTarget.value)}
          />
        </label>
      </div>
      <DashboardTable
        columns={[
          { header: "Conversation Thread", style: "width: 30%;" },
          { header: "Agent", style: "width: 10%;" },
          { header: "Calls", style: "width: 10%;" },
          { header: "Total Tokens", style: "width: 15%;" },
          { header: "Model Breakdown", style: "width: 20%;" },
          { header: "Last used", style: "width: 15%;" },
        ]}
        rows={props.list}
        tableClass="usage-table"
        emptyMessage="No conversation usage recorded."
      >
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
            <td>{formatDateTime(row.last_used_at, props.displayTimezone)}</td>
          </tr>
        )}
      </DashboardTable>
    </section>
  );
}
