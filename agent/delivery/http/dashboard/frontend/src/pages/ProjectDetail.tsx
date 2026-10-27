import { A, useNavigate, useParams, useSearchParams } from "@solidjs/router";
import { createEffect, createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { AppShell } from "@/components/AppShell";
import { ProjectInstructions } from "@/components/ProjectInstructions";
import { ProjectSkills } from "@/components/ProjectSkills";
import { ProjectLocalSync } from "@/components/ProjectLocalSync";
import { WorkspaceHostMirror } from "@/components/WorkspaceHostMirror";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { WorkspaceStorageStatus } from "@/components/WorkspaceStorageStatus";
import { runtimeTransitionBusy } from "@/lib/useWorkspaceRuntime";
import { useToast } from "@/components/Toast";
import { apiFetch, postJson } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import { projectChatState, verifyProjectWorkspace } from "@/lib/projects";
import type { ProjectSummary } from "@/types";

export function ProjectDetailPage() {
  const params = useParams<{ projectId: string }>();
  const [query] = useSearchParams();
  const navigate = useNavigate();
  const { showToast } = useToast();
  const [project, setProject] = createSignal<ProjectSummary | null>(null);
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");
  const [opening, setOpening] = createSignal(false);
  const [saving, setSaving] = createSignal(false);
  let revision = 0;
  const tab = () => query.tab === "skills" ? "skills" : "instructions";
  const load = async (keepProject = false) => {
    const version = ++revision;
    if (!keepProject) setProject(null);
    setLoading(true); setError("");
    try {
      const result = await apiFetch<ProjectSummary>(`${API_PATHS.projects}/${encodeURIComponent(params.projectId)}`);
      if (version === revision) setProject(result);
    } catch (error) {
      if (version === revision) setError(error instanceof Error ? error.message : "Failed to load project.");
    } finally { if (version === revision) setLoading(false); }
  };
  createEffect(() => { params.projectId; void load(); });
  onCleanup(() => { revision += 1; });
  onMount(() => {
    const refreshMirror = () => { if (!saving() && !loading()) void load(true); };
    window.addEventListener(CUSTOM_DOM_EVENTS.WORKSPACE_FILES_CHANGED, refreshMirror);
    onCleanup(() => window.removeEventListener(CUSTOM_DOM_EVENTS.WORKSPACE_FILES_CHANGED, refreshMirror));
    const timer = window.setInterval(async () => {
      if (!project() || saving() || loading()) return;
      const version = revision;
      try {
        const result = await apiFetch<ProjectSummary>(`${API_PATHS.projects}/${project()!.id}`);
        if (version === revision) setProject(result);
      } catch { /* Keep the current editor and last known storage state. */ }
    }, 5000);
    onCleanup(() => window.clearInterval(timer));
  });
  const openChat = async () => {
    if (opening() || !project()) return;
    const current = project()!;
    const version = revision;
    setOpening(true);
    try {
      const workspace = await verifyProjectWorkspace(current.workspace.execution, current.id);
      if (version === revision) navigate("/chat", { state: projectChatState(workspace) });
    } catch (error) {
      if (version === revision) {
        showToast(error instanceof Error ? error.message : "Workspace is unavailable.", "error");
        void load(true);
      }
    } finally { setOpening(false); }
  };

  const projectAction = async (action: string, body: Record<string, unknown> = {}) => {
    if (saving() || !project()) return;
    setSaving(true);
    try {
      await postJson(`${API_PATHS.projects}/${project()!.id}/${action}`, body);
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Project operation failed.", "error");
    } finally { setSaving(false); void load(true); }
  };

  return <AppShell title={project()?.label || "Project"} subtitle={project()?.workspace.execution.locator}
    actions={<Show when={project()}>{(current) => <div class="row-wrap">
      <A class="btn" href={`/history?workspace=${encodeURIComponent(current().key)}`}>View history</A>
      <button class="btn btn-primary" disabled={opening()} onClick={() => void openChat()}>{opening() ? "Preparing workspace..." : "New chat"}</button>
    </div>}</Show>}>
    <div class="projects-page project-detail">
      <A href="/projects">Back to projects</A>
      <Show when={loading()}><p role="status">Loading project...</p></Show>
      <Show when={error()}><div class="project-error" role="alert">{error()} <button class="btn" onClick={() => void load()}>Retry project</button></div></Show>
      <Show when={project()}>{(current) => <>
        <p class="project-status">{current().workspace.execution.backend} · {current().status}</p>
        <WorkspaceStorageStatus name={current().workspace_name} execution={current().workspace.execution}
          localLink={current().local_link} />
        <Show when={current().persistence}>{(saved) => <div class="project-storage-status" role="status">
          <strong>{current().runtime?.status === "restoring" ? "Restoring files" : saved().status === "clean" ? "Saved" : saved().status === "error" ? "Save failed" : "Unsaved changes"}</strong>
          <Show when={saved().last_saved_at}><span>Last saved: {new Date(saved().last_saved_at!).toLocaleString()}</span></Show>
          <Show when={saved().last_error}><p role="alert">{saved().last_error}</p></Show>
          <button class="btn" disabled={saving()} onClick={() => void projectAction("checkpoint")}>{saving() ? "Saving..." : saved().status === "error" ? "Retry save" : "Save now"}</button>
        </div>}</Show>
        <Show when={current().sync_policy !== "manual_apply" && current().host_mirror}>{(state) => <WorkspaceHostMirror state={state()} projectId={current().id}
          workspaceId={current().workspace.project_workspace_id} generation={current().runtime?.generation}
          disabled={saving() || runtimeTransitionBusy(current().runtime || null)} />}</Show>
        <Show when={(current().sync_policy === "manual_apply" || !current().host_mirror)
          && (current().local_link ? current().local_link!.status !== "active" : current().sync_policy === "manual_apply" && current().workspace.execution.backend !== "local")
          && current().runtime}>{(runtime) => <ProjectLocalSync
          projectId={current().id} workspaceId={current().workspace.project_workspace_id}
          generation={runtime().generation} localLink={current().local_link!}
          disabled={saving() || runtimeTransitionBusy(runtime())}
          onApplied={() => void load(true)} />}</Show>
        <Show when={current().migration_status && current().migration_status !== "ready"}>
          <section class="project-storage-status" aria-label="Workspace migration">
            <h2>Choose the main workspace</h2>
            <p>Existing workspace copies are preserved separately. Select the files all project conversations should share.</p>
            <button class="btn" disabled={saving()} onClick={() => void projectAction("runtime/ensure")}>{current().workspaces?.length ? "Retry inspecting files" : "Inspect existing workspaces"}</button>
            <For each={current().workspaces?.filter((item) => item.kind === "legacy")}>{(source) => <div class="project-migration-source">
              <span>{source.source.label || source.source.locator}</span><code>{source.source.locator}</code>
              <Show when={source.persistence.last_error}><p>{source.persistence.last_error}</p></Show>
              <button class="btn" disabled={saving() || !source.persistence.revision}
                onClick={() => void projectAction("migration/select", { source_id: source.id })}>Use these files</button>
            </div>}</For>
          </section>
        </Show>
        <nav class="project-resource-tabs" aria-label="Project resources">
          <A class="btn" classList={{ "btn-primary": tab() === "instructions" }} aria-current={tab() === "instructions" ? "page" : undefined} href={`/projects/${current().id}?tab=instructions`}>AGENTS.md</A>
          <A class="btn" classList={{ "btn-primary": tab() === "skills" }} aria-current={tab() === "skills" ? "page" : undefined} href={`/projects/${current().id}?tab=skills`}>Skills</A>
        </nav>
        <Show when={tab() === "instructions"} fallback={<ProjectSkills workspace={current().workspace.execution} />}>
          <ProjectInstructions projectId={current().id} workspaceId={current().workspace.project_workspace_id} />
        </Show>
      </>}</Show>
    </div>
  </AppShell>;
}
