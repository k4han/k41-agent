import "@/styles/routes/projects.css";

import { A, useNavigate } from "@solidjs/router";
import { Folder, GitBranch, MessageSquarePlus, Pin, Plus, RefreshCw } from "lucide-solid";
import { createEffect, createSignal, For, onCleanup, onMount, Show } from "solid-js";

import { AppShell } from "@/components/AppShell";
import { EmptyState } from "@/components/EmptyState";
import { ProjectActions } from "@/components/ProjectActions";
import { ProjectCreateDialog } from "@/components/ProjectCreateDialog";
import { useToast } from "@/components/Toast";
import { apiFetch } from "@/lib/api";
import { API_PATHS } from "@/lib/endpoints";
import { CUSTOM_DOM_EVENTS } from "@/lib/eventConstants";
import { projectChatState, prepareProjectChatWorkspace } from "@/lib/projects";
import type { ProjectSummary, ProjectsPayload } from "@/types";

export function ProjectsPage() {
  const navigate = useNavigate();
  const { showToast } = useToast();
  const [opening, setOpening] = createSignal<number | null>(null);
  let disposed = false;
  const [projects, setProjects] = createSignal<ProjectSummary[]>([]);
  const [search, setSearch] = createSignal("");
  const [status, setStatus] = createSignal("active");
  const [loading, setLoading] = createSignal(false);
  const [loaded, setLoaded] = createSignal(false);
  const [error, setError] = createSignal("");
  const [adding, setAdding] = createSignal(false);
  const [cursor, setCursor] = createSignal<string | null>(null);
  let revision = 0;

  const load = async (reset = true) => {
    const version = reset ? ++revision : revision;
    const params = new URLSearchParams({
      search: search().trim(),
      status: status(),
      limit: "20",
    });
    if (!reset && cursor()) {
      params.set("after", cursor()!);
    }

    setLoading(true);
    setError("");

    try {
      const data = await apiFetch<ProjectsPayload>(`${API_PATHS.projects}?${params}`);
      if (version !== revision) return;

      setProjects((previous) => [
        ...new Map(
          (reset ? data.projects : [...previous, ...data.projects]).map((project) => [
            project.id,
            project,
          ]),
        ).values(),
      ]);
      setCursor(data.has_more ? data.next_cursor : null);
      setLoaded(true);
    } catch (error) {
      if (version === revision) {
        setError(error instanceof Error ? error.message : "Failed to load projects.");
      }
    } finally {
      if (version === revision) {
        setLoading(false);
      }
    }
  };

  createEffect(() => {
    search();
    status();
    revision += 1;
    setCursor(null);
    const timer = window.setTimeout(() => void load(), 200);
    onCleanup(() => window.clearTimeout(timer));
  });

  onMount(() => {
    const changed = () => void load();
    window.addEventListener(CUSTOM_DOM_EVENTS.PROJECTS_CHANGED, changed);
    onCleanup(() => window.removeEventListener(CUSTOM_DOM_EVENTS.PROJECTS_CHANGED, changed));
  });

  onCleanup(() => {
    revision += 1;
    disposed = true;
  });

  const openChat = async (project: ProjectSummary) => {
    if (opening() !== null) return;
    setOpening(project.id);
    try {
      const workspace = await prepareProjectChatWorkspace(project.workspace.execution, project.id);
      if (!disposed) {
        navigate("/chat", { state: projectChatState(workspace) });
      }
    } catch (error) {
      if (!disposed) {
        showToast(error instanceof Error ? error.message : "Workspace is unavailable.", "error");
      }
    } finally {
      setOpening(null);
    }
  };

  return (
    <AppShell
      title="Projects"
      subtitle="Manage your project workspaces and conversations."
      actions={
        <button class="btn btn-primary" type="button" onClick={() => setAdding(true)}>
          <Plus size={16} />
          Add project
        </button>
      }
    >
      <div class="projects-page">
        <div class="projects-toolbar">
          <input
            class="input"
            aria-label="Search projects"
            placeholder="Search name, folder, or repository"
            value={search()}
            onInput={(event) => setSearch(event.currentTarget.value)}
          />
          <select
            class="input"
            aria-label="Project status"
            value={status()}
            onChange={(event) => setStatus(event.currentTarget.value)}
          >
            <option value="active">Active projects</option>
            <option value="archived">Archived projects</option>
            <option value="removed">Removed projects</option>
            <option value="all">All projects</option>
          </select>
          <button
            class="btn btn-icon"
            aria-label="Refresh projects"
            disabled={loading()}
            onClick={() => void load()}
          >
            <RefreshCw size={16} />
          </button>
        </div>

        <Show when={error()}>
          <div class="project-error" role="alert">
            {error()}{" "}
            <button class="btn" type="button" onClick={() => void load()}>
              Retry
            </button>
          </div>
        </Show>

        <Show when={loading() && !loaded()}>
          <div class="projects-list" aria-busy="true" aria-label="Loading projects">
            <For each={Array.from({ length: 6 })}>
              {() => (
                <div class="panel project-card project-card-skeleton">
                  <div class="project-card-main">
                    <span class="skeleton-line project-skeleton-icon" />
                    <div class="project-card-details">
                      <span class="skeleton-line project-skeleton-title" />
                      <span class="skeleton-line project-skeleton-locator" />
                      <span class="skeleton-line project-skeleton-status" />
                    </div>
                  </div>
                  <div class="project-card-footer">
                    <span class="skeleton-line project-skeleton-button" />
                    <span class="skeleton-line project-skeleton-button" />
                  </div>
                </div>
              )}
            </For>
          </div>
        </Show>

        <Show when={projects().length > 0}>
          <div class="projects-list" aria-label="Project catalog" aria-busy={loading()}>
            <For each={projects()}>
              {(project) => (
                <article class="panel project-card" aria-label={project.label}>
                  <div class="project-card-main">
                    <Show
                      when={project.workspace.scope.kind === "github"}
                      fallback={<Folder size={21} />}
                    >
                      <GitBranch size={21} />
                    </Show>
                    <div class="project-card-details">
                      <h2>
                        <A href={`/projects/${project.id}`}>{project.label}</A>{" "}
                        <Show when={project.is_pinned}>
                          <Pin size={13} aria-label="Pinned" />
                        </Show>
                      </h2>
                      <p class="project-location" title={project.workspace.execution.locator}>
                        {project.workspace.execution.locator}
                      </p>
                      <span class="project-status">
                        {project.status} · {project.workspace.execution.backend}
                      </span>
                    </div>
                    <ProjectActions project={project} />
                  </div>
                  <div class="project-card-footer">
                    <A class="btn" href={`/history?workspace=${encodeURIComponent(project.key)}`}>
                      View history
                    </A>
                    <button
                      class="btn"
                      type="button"
                      disabled={opening() !== null}
                      onClick={() => void openChat(project)}
                    >
                      <MessageSquarePlus size={15} />
                      New chat
                    </button>
                  </div>
                </article>
              )}
            </For>
          </div>
        </Show>

        <Show when={loaded() && !loading() && !error() && projects().length === 0}>
          <EmptyState
            icon={<Folder size={24} />}
            message={search().trim() ? "No matching projects." : "No projects found."}
            hint={
              search().trim()
                ? "Try searching for a different name, folder, or repository."
                : status() === "active"
                  ? "Add a workspace or start a conversation to create your first project."
                  : "No projects found in this view."
            }
            action={!search().trim() && status() === "active" ? "Add project" : undefined}
            onAction={() => setAdding(true)}
          />
        </Show>

        <Show when={cursor()}>
          <button
            class="btn"
            type="button"
            disabled={loading()}
            onClick={() => void load(false)}
          >
            {loading() ? "Loading..." : "Load more"}
          </button>
        </Show>

        <Show when={adding()}>
          <ProjectCreateDialog onClose={() => setAdding(false)} />
        </Show>
      </div>
    </AppShell>
  );
}
