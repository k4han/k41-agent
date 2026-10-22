import { A, useNavigate, useParams, useSearchParams } from "@solidjs/router";
import { createEffect, createSignal, onCleanup, Show } from "solid-js";
import { AppShell } from "@/components/AppShell";
import { ProjectInstructions } from "@/components/ProjectInstructions";
import { ProjectSkills } from "@/components/ProjectSkills";
import { useToast } from "@/components/Toast";
import { apiFetch } from "@/lib/api";
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
  let revision = 0;
  const tab = () => query.tab === "skills" ? "skills" : "instructions";
  const load = async () => {
    const version = ++revision;
    setProject(null); setLoading(true); setError("");
    try {
      const result = await apiFetch<ProjectSummary>(`${API_PATHS.projects}/${encodeURIComponent(params.projectId)}`);
      if (version === revision) setProject(result);
    } catch (error) {
      if (version === revision) setError(error instanceof Error ? error.message : "Failed to load project.");
    } finally { if (version === revision) setLoading(false); }
  };
  createEffect(() => { params.projectId; void load(); });
  onCleanup(() => { revision += 1; });
  const openChat = async () => {
    if (opening() || !project()) return;
    const current = project()!;
    const version = revision;
    setOpening(true);
    try {
      await verifyProjectWorkspace(current.workspace.execution);
      if (version === revision) navigate("/chat", { state: projectChatState(current.workspace.execution) });
    } catch (error) {
      if (version === revision) showToast(error instanceof Error ? error.message : "Workspace is unavailable.", "error");
    } finally { setOpening(false); }
  };

  return <AppShell title={project()?.label || "Project"} subtitle={project()?.workspace.execution.locator}
    actions={<Show when={project()}>{(current) => <div class="row-wrap">
      <A class="btn" href={`/history?workspace=${encodeURIComponent(current().key)}`}>View history</A>
      <button class="btn btn-primary" disabled={opening()} onClick={() => void openChat()}>New chat</button>
    </div>}</Show>}>
    <div class="projects-page project-detail">
      <A href="/projects">Back to projects</A>
      <Show when={loading()}><p role="status">Loading project...</p></Show>
      <Show when={error()}><div class="project-error" role="alert">{error()} <button class="btn" onClick={() => void load()}>Retry project</button></div></Show>
      <Show when={project()} keyed>{(current) => <>
        <p class="project-status">{current.workspace.execution.backend} · {current.status}</p>
        <nav class="project-resource-tabs" aria-label="Project resources">
          <A class="btn" classList={{ "btn-primary": tab() === "instructions" }} aria-current={tab() === "instructions" ? "page" : undefined} href={`/projects/${current.id}?tab=instructions`}>AGENTS.md</A>
          <A class="btn" classList={{ "btn-primary": tab() === "skills" }} aria-current={tab() === "skills" ? "page" : undefined} href={`/projects/${current.id}?tab=skills`}>Skills</A>
        </nav>
        <Show when={tab() === "instructions"} fallback={<ProjectSkills workspace={current.workspace.execution} />}>
          <ProjectInstructions projectId={current.id} />
        </Show>
      </>}</Show>
    </div>
  </AppShell>;
}
