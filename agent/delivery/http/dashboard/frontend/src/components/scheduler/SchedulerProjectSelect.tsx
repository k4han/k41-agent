import { createSignal, onCleanup, onMount, Show } from "solid-js";

import { SelectControl } from "@/components/SelectControl";
import { apiFetch } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import { NO_WORKSPACE_LABEL } from "@/lib/workspaceConstants";
import type { ProjectsPayload, ProjectSummary } from "@/types";

export function SchedulerProjectSelect(props: {
  value: number | null;
  onChange: (value: number | null) => void;
}) {
  const [projects, setProjects] = createSignal<ProjectSummary[]>([]);
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");
  const [hasMore, setHasMore] = createSignal(true);
  let cursor: string | null = null;
  let disposed = false;
  onCleanup(() => { disposed = true; });

  const merge = (rows: ProjectSummary[]) => setProjects(current =>
    [...new Map([...current, ...rows].map(project => [project.id, project])).values()]);
  const load = async () => {
    if (loading()) return;
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({ status: "active", limit: "100" });
      if (cursor) query.set("after", cursor);
      const page = await apiFetch<ProjectsPayload>(`${API_PATHS.projects}?${query}`);
      if (disposed) return;
      merge(page.projects);
      cursor = page.next_cursor;
      setHasMore(page.has_more);
    } catch (err) {
      if (!disposed) setError(err instanceof Error ? err.message : "Failed to load projects.");
    } finally {
      if (!disposed) setLoading(false);
    }
  };

  onMount(async () => {
    await load();
    const selectedId = props.value;
    if (disposed || selectedId === null || projects().some(project => project.id === selectedId)) return;
    try {
      const selected = await apiFetch<ProjectSummary>(`${API_PATHS.projects}/${selectedId}`);
      if (!disposed) merge([selected]);
    } catch {
      // Preserve an unavailable selection until the user explicitly replaces it.
    }
  });

  const options = () => [
    { value: "", label: NO_WORKSPACE_LABEL },
    ...projects().map(project => ({
      value: String(project.id),
      label: project.status === "active" ? project.label : `${project.label} (${project.status})`,
      disabled: project.status !== "active",
    })),
    ...(props.value !== null && !projects().some(project => project.id === props.value)
      ? [{ value: String(props.value), label: `Project #${props.value} (unavailable)`, disabled: true }]
      : []),
  ];

  return <div class="field">
    <label>Project</label>
    <SelectControl value={props.value === null ? "" : String(props.value)} options={options()}
      ariaLabel="Scheduled task project" onChange={value => props.onChange(value ? Number(value) : null)} />
    <p class="hint">Each run starts a separate conversation. No workspace uses a private session folder.</p>
    <Show when={error()}><p class="error-text" role="alert">{error()}</p></Show>
    <Show when={hasMore() || error()}>
      <button class="btn btn-sm" type="button" disabled={loading()} onClick={() => void load()}>
        {loading() ? "Loading projects…" : error() ? "Retry loading projects" : "Load more projects"}
      </button>
    </Show>
  </div>;
}
