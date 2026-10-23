import { createEffect, createMemo, createSignal, For, on, onCleanup, Show } from "solid-js";
import { Dialog } from "@/components/Dialog";
import { Markdown } from "@/components/Markdown";
import { apiFetch } from "@/lib/api";
import { skillPackageUrl, skillResourcePath } from "@/lib/skillPackages";
import type { SkillEntry, SkillFileDetail, SkillPackageDetail, SkillPackageSummary } from "@/lib/skillPackages";
import type { WorkspaceRef } from "@/types";

const errorMessage = (error: unknown) => error instanceof Error ? error.message : "Failed to load skills.";

export function ProjectSkills(props: { workspace: WorkspaceRef }) {
  const [packages, setPackages] = createSignal<SkillPackageSummary[]>([]);
  const [search, setSearch] = createSignal("");
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");
  const [selected, setSelected] = createSignal<SkillPackageSummary | null>(null);
  let revision = 0;
  const load = async () => {
    const version = ++revision;
    setLoading(true);
    setError("");
    try {
      const result = await apiFetch<{ packages: SkillPackageSummary[] }>(skillPackageUrl(props.workspace));
      if (version === revision) setPackages(result.packages);
    } catch (error) {
      if (version === revision) setError(errorMessage(error));
    } finally { if (version === revision) setLoading(false); }
  };
  createEffect(on(() => skillPackageUrl(props.workspace), () => void load()));
  onCleanup(() => { revision += 1; });
  const shown = createMemo(() => {
    const needle = search().trim().toLowerCase();
    return packages().filter((item) => `${item.name} ${item.description} ${item.source.root}/${item.source.directory}`.toLowerCase().includes(needle));
  });

  return <section class="project-skills" aria-label="Project skills">
    <div class="projects-toolbar">
      <input class="input" aria-label="Search skills" placeholder="Search skills by name, description, or path" value={search()} onInput={(event) => setSearch(event.currentTarget.value)} />
      <button class="btn" disabled={loading()} onClick={() => void load()}>Refresh skills</button>
    </div>
    <Show when={error()}><div class="project-error" role="alert">{error()} <button class="btn" onClick={() => void load()}>Retry skills</button></div></Show>
    <Show when={loading()}><p role="status">Loading skills...</p></Show>
    <div class="projects-list" aria-busy={loading()}>
      <For each={shown()}>{(item) => <article class="panel project-resource-panel project-skill-card">
        <div class="row-wrap"><h2>{item.name}</h2><span class="badge">{item.source.scope === "project" ? "Project" : "Shared"}</span>
          <span class="badge">{item.enabled ? "Enabled" : "Disabled or invalid"}</span>
          <Show when={item.shadowed}><span class="badge">Shadowed</span></Show>
        </div>
        <p>{item.description}</p>
        <p class="project-resource-path mono">{item.source.root}/{item.source.directory}</p>
        <For each={item.diagnostics}>{(diagnostic) => <p class="hint">{diagnostic.message}</p>}</For>
        <button class="btn" onClick={() => setSelected(item)}>View skill</button>
      </article>}</For>
    </div>
    <Show when={!loading() && !error() && !shown().length}><p class="empty">{search().trim() ? "No matching skills." : "No project or shared skills found."}</p></Show>
    <Show when={selected()} keyed>{(item) => <SkillPackageViewer workspace={props.workspace} item={item} onClose={() => setSelected(null)} />}</Show>
  </section>;
}

function SkillPackageViewer(props: { workspace: WorkspaceRef; item: SkillPackageSummary; onClose: () => void }) {
  const [detail, setDetail] = createSignal<SkillPackageDetail | null>(null);
  const [source, setSource] = createSignal(false);
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");
  const [directory, setDirectory] = createSignal("");
  const [entries, setEntries] = createSignal<SkillEntry[]>([]);
  const [nextOffset, setNextOffset] = createSignal<number | null>(null);
  const [treeLoading, setTreeLoading] = createSignal(false);
  const [treeError, setTreeError] = createSignal("");
  const [file, setFile] = createSignal<SkillFileDetail | null>(null);
  const [fileLoading, setFileLoading] = createSignal(false);
  const [fileError, setFileError] = createSignal("");
  const [filePath, setFilePath] = createSignal("");
  let alive = true;
  let treeRevision = 0;
  let fileRevision = 0;
  const url = (path: string, options: Record<string, string> = {}) => skillPackageUrl(props.workspace, `/${props.item.id}${path}`, options);
  const loadDetail = async () => {
    setLoading(true); setError("");
    try {
      const result = await apiFetch<{ package: SkillPackageDetail }>(url(""));
      if (alive) setDetail(result.package);
    } catch (error) { if (alive) setError(errorMessage(error)); }
    finally { if (alive) setLoading(false); }
  };
  const browse = async (path: string, offset = 0) => {
    const version = ++treeRevision;
    if (offset === 0) {
      setDirectory(path); setEntries([]); setNextOffset(null); setFile(null); setFilePath(""); setFileError("");
      fileRevision += 1; setFileLoading(false);
    }
    setTreeLoading(true); setTreeError("");
    try {
      const result = await apiFetch<{ entries: SkillEntry[]; next_offset: number | null }>(url("/files", { path, offset: String(offset) }));
      if (!alive || version !== treeRevision) return;
      setEntries((previous) => offset ? [...previous, ...result.entries] : result.entries);
      setNextOffset(result.next_offset);
    } catch (error) { if (alive && version === treeRevision) setTreeError(errorMessage(error)); }
    finally { if (alive && version === treeRevision) setTreeLoading(false); }
  };
  const openFile = async (path: string) => {
    const version = ++fileRevision;
    setFilePath(path); setFile(null); setFileLoading(true); setFileError("");
    try {
      const result = await apiFetch<SkillFileDetail>(skillPackageUrl(props.workspace, skillResourcePath(props.item.id, path)));
      if (alive && version === fileRevision) setFile(result);
    } catch (error) { if (alive && version === fileRevision) setFileError(errorMessage(error)); }
    finally { if (alive && version === fileRevision) setFileLoading(false); }
  };
  const downloadUrl = (path: string) => skillPackageUrl(props.workspace, skillResourcePath(props.item.id, path), { download: "true" });
  createEffect(() => { void loadDetail(); void browse(""); });
  onCleanup(() => { alive = false; });

  return <Dialog open title={props.item.name} subtitle="Read-only skill package" size="xl" onClose={props.onClose}>
    <div class="project-skills">
      <Show when={loading()}><p role="status">Loading skill...</p></Show>
      <Show when={error()}><div class="project-error" role="alert">{error()} <button class="btn" onClick={() => void loadDetail()}>Retry document</button></div></Show>
      <Show when={detail()}>{(current) => <section aria-label="Skill instructions">
        <div class="project-resource-toolbar"><h3>SKILL.md</h3><div class="row-wrap">
          <button class="btn" aria-pressed={source()} onClick={() => setSource(!source())}>{source() ? "Preview skill" : "Skill source"}</button>
          <a class="btn" href={downloadUrl("SKILL.md")} download="SKILL.md">Download SKILL.md</a>
        </div></div>
        <Show when={source() || !current().valid} fallback={<Markdown text={current().body} class="message-markdown" />}>
          <pre class="project-resource-source">{current().content}</pre>
        </Show>
      </section>}</Show>
      <section aria-label="Skill resources" class="project-skills">
        <div class="project-resource-toolbar"><h3>Resources</h3>
          <Show when={directory()}><button class="btn" disabled={treeLoading()} onClick={() => void browse(directory().split("/").slice(0, -1).join("/"))}>Parent directory</button></Show>
        </div>
        <p class="project-resource-path mono">/{directory()}</p>
        <Show when={treeLoading()}><p role="status">Loading resources...</p></Show>
        <Show when={treeError()}><div class="project-error" role="alert">{treeError()} <button class="btn" onClick={() => void browse(directory())}>Retry resources</button></div></Show>
        <div class="project-resource-files">
          <For each={entries()}>{(entry) => <button class="btn" onClick={() => entry.kind === "directory" ? void browse(entry.path) : void openFile(entry.path)}>{entry.kind === "directory" ? "Folder: " : ""}{entry.name}</button>}</For>
          <Show when={!treeLoading() && !treeError() && !entries().length}><p class="empty compact">This directory is empty.</p></Show>
        </div>
        <Show when={nextOffset() !== null}><button class="btn" disabled={treeLoading()} onClick={() => void browse(directory(), nextOffset()!)}>Load more resources</button></Show>
        <Show when={fileLoading()}><p role="status">Loading file...</p></Show>
        <Show when={fileError()}><div class="project-error" role="alert">{fileError()} <button class="btn" onClick={() => void openFile(filePath())}>Retry file</button></div></Show>
        <Show when={file()}>{(current) => <section class="project-resource-file" aria-label="Resource preview">
          <div class="project-resource-toolbar"><h4 class="project-resource-path">{current().path}</h4><a class="btn" href={downloadUrl(current().path)} download={current().path.split("/").pop()}>Download file</a></div>
          <p class="hint">{current().mime_type} · {current().size} bytes</p>
          <Show when={current().content !== null} fallback={<p>No text preview is available. Download the file to view its complete contents.</p>}>
            <pre class="project-resource-source">{current().content}</pre>
          </Show>
        </section>}</Show>
      </section>
    </div>
  </Dialog>;
}
