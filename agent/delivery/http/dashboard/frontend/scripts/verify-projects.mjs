import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const staticRoot = fileURLToPath(new URL('../../static/', import.meta.url));
const executable = process.env.BROWSER_EXECUTABLE || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].find(candidate => fs.existsSync(candidate));
assert.ok(executable, 'Set BROWSER_EXECUTABLE to run browser checks.');
assert.ok(fs.existsSync(path.join(staticRoot, 'index.html')), 'Run pnpm build first.');
const makeProject = (id, label) => ({ id, key: `local:/projects/${id}`, label, status: 'active', is_pinned: false,
  workspace: { scope: { key: `local:/projects/${id}`, kind: 'local', label, metadata: {} },
    execution: { backend: 'local', locator: `/projects/${id}`, label, metadata: {} } },
    migration_status: 'ready', workspaces: [], runtime: { status: 'ready', generation: 1 },
    persistence: { status: 'clean', revision: 'a'.repeat(64), last_saved_at: '2026-01-01T00:00:00Z', last_error: '', blocked_for_writes: false },
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' });
let projects = [makeProject(1, 'Empty project'), makeProject(2, 'Other project')];
const mutations = [];
const schedulerJobs = [];
const schedulerWrites = [];
const schedulerDeletes = [];
let failSchedulerDelete = false;
let delaySchedulerDelete = false;
let finishSchedulerDelete;
const schedulerRuns = [{ run_id: 'run-1', job_id: 'job-1', task: 'Scheduled task', thread_id: 'scheduler-run-1',
  status: 'completed', trigger_source: 'schedule', platform: 'telegram', user_id: '123', agent_name: 'scheduler-executor',
  provider: 'test', model: 'demo', models_used: [], total_tokens: 0, response: 'Done', error: '', duration_ms: 100,
  started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:00:01Z' }];
let schedulerProjectPagination = false;
const localSyncRequests = [];
const runtimeRequests = [];
const switchRequests = [];
const newChatRequests = [];
const chatRequests = [];
let switchFailure = false;
let localSyncConflict = true;
let unavailable = false;
let delayRuntimeFailure = false;
let failPendingRuntime;
const detailRequests = [];
let navRequests = 0;
const instructionRequests = [];
let failCatalog = false;
let failInstructions = false;
let failSkills = false;
let failSecrets = false;
let delaySecrets = false;
let finishSecrets;
const secretRequests = [];
const projectSecrets = new Map();
const builtinBrowserProfile = { profile_id: 'd'.repeat(32), name: 'Default', is_builtin: true, open_thread_ids: [] };
const browserProfiles = [builtinBrowserProfile];
const browserProjectProfiles = new Map();
const browserProjectModes = new Map();
let defaultBrowserProfile = builtinBrowserProfile.profile_id;
let temporaryBrowserProfile = builtinBrowserProfile.profile_id;
let rejectDefaultProfile = false;
let rejectProjectProfile = false;
let instructionDocument = { path: 'AGENTS.md', exists: false, content: '', version: null };
const skillRequests = [];
const makeSkill = (id, scope, extra = {}) => ({ id, name: 'demo', description: `A ${scope} skill`, enabled: true,
  shadowed: scope === 'global', source: { scope, root: scope === 'project' ? '/projects/1' : '/shared', directory: '.agent/skills/demo' }, diagnostics: [], ...extra });
const skillPackages = [makeSkill('project-demo', 'project'), makeSkill('shared-demo', 'global'),
  makeSkill('invalid-demo', 'project', { name: 'broken', enabled: false, diagnostics: [{ message: 'Invalid document', severity: 'error' }] })];
const fixtures = {
  '/dashboard-api/browser/configuration': { settings: {}, by_category: { browser: {} }, settings_sources: {} },
  '/dashboard-api/browser/setup': { installed: true, installation_supported: false, installation_unavailable_reason: 'Test browser', job: null },
  '/dashboard-api/catalog': { backends: [{ name: 'local', title: 'Local', enabled: true }] },
  '/dashboard-api/agents/cards': { cards: [{ name: 'default', valid: true, display_name: 'Default', provider: 'test', model: 'demo' }] },
  '/dashboard-api/agents/providers': { provider_names: ['test'], default_provider: 'test', default_model: 'demo', model_catalogs: [] },
  '/dashboard-api/github': { repositories: [] },
  '/dashboard-api/workspace/default': { workspace: projects[0].workspace },
  '/dashboard-api/chat-history': { threads: [] },
};
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/v2/api/chat/events') {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    chatRequests.push(JSON.parse(raw));
    res.setHeader('Content-Type', 'application/x-ndjson');
    res.end();
    return;
  }
  if (url.pathname.endsWith('/sessions/events')) {
    res.setHeader('Content-Type', 'text/event-stream');
    res.write('event: snapshot\ndata: {"sessions":[]}\n\n');
    return;
  }
  if (url.pathname.startsWith('/dashboard-api') || url.pathname.startsWith('/scheduler/')) {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-CSRF-Token', 'projects-test');
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const body = raw ? JSON.parse(raw) : {};
    const send = data => res.end(JSON.stringify(data));
    if (url.pathname === '/dashboard-api/scheduler') {
      send({ jobs: schedulerJobs, identities: [{ platform: 'telegram', external_id: '123' }], scheduler_timezone: 'UTC' }); return;
    }
    if (url.pathname === '/scheduler/runs') {
      const filtered = schedulerRuns.filter(run => !url.searchParams.has('job_id') || run.job_id === url.searchParams.get('job_id'));
      const offset = Number(url.searchParams.get('offset') || 0);
      const limit = Number(url.searchParams.get('limit') || 25);
      send({ runs: filtered.slice(offset, offset + limit), total: filtered.length, limit, offset }); return;
    }
    if (req.method === 'DELETE' && /^\/scheduler\/(jobs|runs)\//.test(url.pathname)) {
      schedulerDeletes.push(url.pathname);
      if (failSchedulerDelete) { res.writeHead(503); send({ detail: 'Scheduler deletion unavailable.' }); return; }
      const remove = () => {
        const entries = url.pathname.includes('/runs/') ? schedulerRuns : schedulerJobs;
        const index = entries.findIndex(item => (item.run_id || item.id) === url.pathname.split('/').at(-1));
        if (index < 0) { res.writeHead(404); send({ detail: 'Entry not found.' }); return; }
        if (entries[index].status === 'running') { res.writeHead(409); send({ detail: 'Run is still running.' }); return; }
        entries.splice(index, 1); send({ status: 'deleted' });
      };
      if (delaySchedulerDelete) { delaySchedulerDelete = false; finishSchedulerDelete = remove; return; }
      remove(); return;
    }
    if (url.pathname.startsWith('/scheduler/jobs')) {
      schedulerWrites.push(body);
      const job = req.method === 'POST' ? { id: `job-${schedulerJobs.length + 1}`, paused: false, next_run_time: null }
        : schedulerJobs.find(item => item.id === url.pathname.split('/').at(-1));
      Object.assign(job, body);
      if (req.method === 'POST') schedulerJobs.push(job);
      send({ status: req.method === 'POST' ? 'created' : 'updated', job }); return;
    }
    if (url.pathname === '/dashboard-api/browser/default-profile') {
      if (req.method === 'PUT' && rejectDefaultProfile) {
        res.writeHead(403); send({ detail: 'Default profile rejected.' }); return;
      }
      if (req.method === 'PUT') defaultBrowserProfile = body.profile_id ?? builtinBrowserProfile.profile_id;
      send({ profile_id: defaultBrowserProfile }); return;
    }
    if (url.pathname === '/dashboard-api/browser/temporary-profile') {
      if (req.method === 'PUT') temporaryBrowserProfile = body.profile_id ?? builtinBrowserProfile.profile_id;
      send({ profile_id: temporaryBrowserProfile }); return;
    }
    const projectProfileRoute = url.pathname.match(/^\/dashboard-api\/browser\/projects\/(\d+)\/profile$/);
    if (projectProfileRoute) {
      const id = Number(projectProfileRoute[1]);
      if (req.method === 'PUT' && rejectProjectProfile) {
        res.writeHead(403); send({ detail: 'Browser profile not found for this account.' }); return;
      }
      if (req.method === 'PUT') {
        browserProjectProfiles.set(id, body.profile_id);
        browserProjectModes.set(id, body.mode);
      }
      const mode = browserProjectModes.get(id) ?? 'inherit';
      const profile_id = browserProjectProfiles.get(id) ?? null;
      send({ mode, profile_id, default_profile_id: defaultBrowserProfile,
        effective_profile_id: mode === 'inherit' ? defaultBrowserProfile : profile_id });
      return;
    }
    const browserProfileRoute = url.pathname.match(/^\/dashboard-api\/browser\/profiles(?:\/([a-f0-9]+))?$/);
    if (browserProfileRoute) {
      const profile = browserProfiles.find(entry => entry.profile_id === browserProfileRoute[1]);
      if (req.method === 'POST' || req.method === 'PATCH') {
        if (browserProfiles.some(entry => entry !== profile && entry.name.toLowerCase() === body.name.toLowerCase())) {
          res.writeHead(409); send({ detail: { code: 'profile_name_taken', message: 'A login profile with this name already exists.' } }); return;
        }
      }
      if (req.method === 'POST') {
        const created = { profile_id: `a${browserProfiles.length}`.padEnd(32, '0'), name: body.name, open_thread_id: null };
        browserProfiles.push(created); send(created); return;
      }
      if (req.method === 'PATCH') { profile.name = body.name; send(profile); return; }
      if (req.method === 'DELETE') {
        browserProfiles.splice(browserProfiles.indexOf(profile), 1);
        for (const [id, value] of browserProjectProfiles) if (value === profile.profile_id) browserProjectProfiles.delete(id);
        for (const [id, mode] of browserProjectModes) if (mode === 'profile' && !browserProjectProfiles.has(id)) browserProjectModes.delete(id);
        if (defaultBrowserProfile === profile.profile_id) defaultBrowserProfile = builtinBrowserProfile.profile_id;
        if (temporaryBrowserProfile === profile.profile_id) temporaryBrowserProfile = builtinBrowserProfile.profile_id;
        send({ deleted: true, saved_data_removed: false }); return;
      }
      send({ profiles: browserProfiles.map(profile => ({ ...profile, projects: projects.filter(project =>
        (browserProjectModes.get(project.id) ?? 'inherit') === 'inherit' ? defaultBrowserProfile === profile.profile_id
          : browserProjectProfiles.get(project.id) === profile.profile_id).map(project => ({ id: project.id, label: project.label })) })) }); return;
    }
    const secretRoute = url.pathname.match(/^\/dashboard-api\/projects\/(\d+)\/secrets(?:\/([A-Z_][A-Z0-9_]*))?$/);
    if (secretRoute) {
      const id = Number(secretRoute[1]);
      const name = secretRoute[2];
      const entries = projectSecrets.get(id) || [];
      projectSecrets.set(id, entries);
      const metadata = entry => ({ name: entry.name, enabled: entry.enabled, created_at: entry.created_at, updated_at: entry.updated_at });
      secretRequests.push({ id, name, method: req.method, body });
      if (failSecrets) { res.writeHead(503); send({ detail: 'Secrets temporarily unavailable.' }); return; }
      if (req.method === 'GET') {
        const reply = () => send({ secrets: entries.map(metadata) });
        if (delaySecrets) { delaySecrets = false; finishSecrets = reply; return; }
        reply(); return;
      }
      if (req.method === 'POST') {
        if (entries.some(entry => entry.name === body.name)) { res.writeHead(409); send({ detail: 'Secret already exists.' }); return; }
        const entry = { ...body, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' };
        entries.push(entry); res.writeHead(201); send(metadata(entry)); return;
      }
      const index = entries.findIndex(entry => entry.name === name);
      if (index < 0) { res.writeHead(404); send({ detail: 'Secret not found.' }); return; }
      if (req.method === 'DELETE') { entries.splice(index, 1); res.writeHead(204); res.end(); return; }
      Object.assign(entries[index], body); send(metadata(entries[index])); return;
    }
    const chatWorkspace = url.pathname.match(/^\/dashboard-api\/projects\/(\d+)\/chat-workspace$/);
    if (chatWorkspace) {
      assert.equal(req.method, 'POST');
      const project = projects.find(item => item.id === Number(chatWorkspace[1]));
      newChatRequests.push(project.id);
      if (delayRuntimeFailure) {
        delayRuntimeFailure = false;
        failPendingRuntime = () => { res.writeHead(503); send({ detail: 'Delayed workspace failure.' }); };
        return;
      }
      if (unavailable) {
        res.writeHead(409); send({ detail: 'Workspace is unavailable.' }); return;
      }
      if (project.workspace.execution.backend !== 'local') {
        project.sandbox = { workspace: project.workspace, runtime: project.runtime };
        project.workspace = { ...project.workspace, project_workspace_id: project.workspace.project_workspace_id + 1, runtime_generation: 1,
          execution: { backend: 'local', locator: `/projects/${project.id}`, label: project.label, metadata: {} } };
        project.runtime = { status: 'ready', generation: project.workspace.runtime_generation };
      }
      send({ workspace: project.workspace }); return;
    }
    const instructions = url.pathname.match(/^\/dashboard-api\/projects\/(\d+)\/instructions$/);
    const runtimeSwitch = url.pathname.match(/^\/dashboard-api\/projects\/(\d+)\/runtime\/switch$/);
    if (runtimeSwitch) {
      switchRequests.push({ projectId: Number(runtimeSwitch[1]), body });
      if (switchFailure) {
        res.writeHead(409); send({ detail: 'Another conversation is running.' }); return;
      }
      const project = projects.find(item => item.id === Number(runtimeSwitch[1]));
      project.runtime.transition = { operation_id: 'new-chat-switch', phase: 'saving', target_backend: body.backend };
      res.writeHead(202); send({ transition: project.runtime.transition }); return;
    }
    if (/\/runtime\/(ensure|recovery)$/.test(url.pathname)) {
      runtimeRequests.push({ path: url.pathname, body });
      res.writeHead(500); send({ detail: 'Chat navigation must not initialize execution.' }); return;
    }
    const checkpoint = url.pathname.match(/^\/dashboard-api\/projects\/(\d+)\/checkpoint$/);
    if (checkpoint) {
      const project = projects.find(item => item.id === Number(checkpoint[1]));
      project.persistence = { ...project.persistence, status: 'clean', last_error: '', blocked_for_writes: false };
      send({ status: 'clean', saved: true, revision: project.persistence.revision }); return;
    }
    const localSync = url.pathname.match(/^\/dashboard-api\/projects\/(\d+)\/local-sync$/);
    if (localSync) {
      localSyncRequests.push(body);
      const project = projects.find(item => item.id === Number(localSync[1]));
      if (body.preview_token) project.local_link = { ...project.local_link, status: 'applied', revision: project.persistence.revision };
      send({ path: project.local_link.path, revision: project.persistence.revision, preview_token: 'b'.repeat(64),
        changes: [{ path: 'src/app.py', action: 'update' }], conflicts: localSyncConflict ? ['src/app.py'] : [], applied: Boolean(body.preview_token) });
      return;
    }
    const selection = url.pathname.match(/^\/dashboard-api\/projects\/(\d+)\/migration\/select$/);
    if (selection) {
      const project = projects.find(item => item.id === Number(selection[1]));
      const source = project.workspaces.find(item => item.id === body.source_id);
      source.kind = 'shared'; project.migration_status = 'ready';
      project.workspace = { ...project.workspace, execution: source.source, project_id: project.id, project_workspace_id: source.id };
      send({ workspace: project.workspace }); return;
    }
    if (instructions) {
      if (req.method === 'GET') instructionRequests.push(Number(instructions[1]));
      if (failInstructions) { res.writeHead(410); send({ detail: 'Project workspace is unavailable.' }); return; }
      if (req.method === 'PUT') {
        if (body.expected_version !== instructionDocument.version) { res.writeHead(409); send({ detail: 'File changed; read its current version before changing it.' }); return; }
        instructionDocument = { path: 'AGENTS.md', exists: true, content: body.content, version: createHash('sha256').update(body.content).digest('hex') };
      }
      send(instructionDocument); return;
    }
    if (url.pathname.startsWith('/dashboard-api/skill-packages')) {
      skillRequests.push({ method: req.method, path: url.pathname, workspace: JSON.parse(url.searchParams.get('workspace')) });
      if (req.method !== 'GET') { res.writeHead(405); send({ detail: 'Read-only view' }); return; }
      if (failSkills) { res.writeHead(503); send({ detail: 'Skills unavailable.' }); return; }
      if (url.pathname === '/dashboard-api/skill-packages') { send({ packages: skillPackages }); return; }
      const match = url.pathname.match(/^\/dashboard-api\/skill-packages\/([^/]+)(.*)$/);
      const item = skillPackages.find(item => item.id === match[1]);
      const content = `---\nname: ${item.name}\ndescription: Guide\n---\n# Skill guide\nRead this first.`;
      if (!match[2]) { send({ package: { ...item, valid: item.enabled, content, body: '# Skill guide\nRead this first.', frontmatter: {}, file_version: 'version' } }); return; }
      if (match[2] === '/files') {
        const directory = url.searchParams.get('path') || '';
        if (directory === 'assets') { send({ entries: [{ name: 'template.bin', path: 'assets/template.bin', kind: 'file', size: 2 }, { name: 'large.txt', path: 'assets/large.txt', kind: 'file', size: 2000000 }], next_offset: null }); return; }
        if (url.searchParams.get('offset') === '2') { send({ entries: [{ name: 'notes.txt', path: 'notes.txt', kind: 'file', size: 14 }], next_offset: null }); return; }
        send({ entries: [{ name: 'assets', path: 'assets', kind: 'directory', size: 0 }, { name: 'SKILL.md', path: 'SKILL.md', kind: 'file', size: content.length }], next_offset: 2 }); return;
      }
      const file = decodeURIComponent(match[2].slice('/files/'.length));
      if (url.searchParams.get('download') === 'true') { res.end(file.endsWith('.bin') ? Buffer.from([0, 255]) : content); return; }
      send({ path: file, content: file.endsWith('.bin') || file.endsWith('large.txt') ? null : file === 'SKILL.md' ? content : 'Resource notes',
        version: 'version', size: file.endsWith('large.txt') ? 2000000 : 14, mime_type: file.endsWith('.bin') ? 'application/octet-stream' : 'text/plain', preview_truncated: file.endsWith('large.txt') }); return;
    }
    const ordered = rows => [...rows].sort((a, b) => Number(b.is_pinned) - Number(a.is_pinned) || a.key.localeCompare(b.key));
    if (url.pathname === '/dashboard-api/nav-projects') {
      navRequests++;
      send({ projects: ordered(projects.filter(project => project.status === 'active')).map(project => ({ ...project, threads: [] })), has_more: false, next_cursor: null }); return;
    }
    if (url.pathname === '/dashboard-api/projects' && req.method === 'GET') {
      if (schedulerProjectPagination && !failCatalog) {
        const offset = url.searchParams.has('after') ? 1 : 0;
        send({ projects: projects.slice(offset, offset + 1), has_more: offset === 0, next_cursor: offset === 0 ? 'next-project' : null }); return;
      }
      if (failCatalog) { res.writeHead(503); send({ detail: 'Catalog unavailable.' }); return; }
      const status = url.searchParams.get('status') || 'active';
      const needle = (url.searchParams.get('search') || '').toLowerCase();
      send({ projects: ordered(projects.filter(project => (status === 'all' || status === project.status) && project.label.toLowerCase().includes(needle))), has_more: false, next_cursor: null }); return;
    }
    if (url.pathname === '/dashboard-api/projects' && req.method === 'POST') {
      const project = { ...makeProject(3, body.label || 'Added project'), key: body.workspace.scope.key, workspace: body.workspace };
      projects.push(project); mutations.push({ method: req.method, body }); send(project); return;
    }
    const match = url.pathname.match(/^\/dashboard-api\/projects\/(\d+)$/);
    if (match) {
      const project = projects.find(project => project.id === Number(match[1]));
      if (req.method === 'GET') detailRequests.push(Number(match[1]));
      if (!project) { res.writeHead(404); send({ detail: 'Project not found.' }); return; }
      if (req.method === 'GET' && unavailable) { res.writeHead(503); send({ detail: 'Workspace is unavailable.' }); return; }
      if (req.method === 'GET') { send(project); return; }
      Object.assign(project, req.method === 'DELETE' ? { status: 'removed' } : body);
      mutations.push({ method: req.method, id: project.id, body }); send(project); return;
    }
    if (url.pathname === '/dashboard-api/workspace/resolve') {
      const workspace = structuredClone(projects[0].workspace);
      workspace.scope.key = `local:${body.locator}`;
      workspace.execution.locator = body.locator;
      send({ workspace }); return;
    }
    if (url.pathname === '/dashboard-api/workspace/tree' && unavailable) { res.writeHead(404); send({ detail: 'Workspace is unavailable.' }); return; }
    send(fixtures[url.pathname] || { tools: [], agents: [], sessions: [], entries: [], providers: [], settings: {}, by_category: {}, current_version: 'test' }); return;
  }
  const relative = url.pathname.startsWith('/dashboard-assets/') ? url.pathname.slice('/dashboard-assets/'.length) : 'index.html';
  const target = path.resolve(staticRoot, relative);
  if (!target.startsWith(path.resolve(staticRoot) + path.sep)) { res.writeHead(403); res.end(); return; }
  try {
    res.setHeader('Content-Type', { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' }[path.extname(target)] || 'application/octet-stream');
    res.end(fs.readFileSync(target));
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-projects-browser-'));
const browser = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-networking',
  '--disable-features=BackForwardCache',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(callback, label) {
  const started = Date.now();
  while (Date.now() - started < 15000) { if (await callback()) return; await delay(100); }
  throw new Error(`Timeout: ${label}`);
}
let ws, call;
try {
  const portFile = path.join(profile, 'DevToolsActivePort');
  await until(() => fs.existsSync(portFile), 'browser debugger');
  const port = Number(fs.readFileSync(portFile, 'utf8').split('\n')[0]);
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  ws = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
  await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }));
  let sequence = 0;
  const pending = new Map();
  const exceptions = [];
  let acceptDialog = false;
  let dialogCount = 0;
  call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params }));
  });
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const entry = pending.get(message.id); pending.delete(message.id);
      if (message.error) entry?.reject(new Error(message.error.message)); else entry?.resolve(message.result);
    }
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails);
    if (message.method === 'Page.javascriptDialogOpening') {
      dialogCount += 1;
      void call('Page.handleJavaScriptDialog', { accept: acceptDialog });
    }
  });
  const js = async expression => {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const clickText = text => js(`[...document.querySelectorAll('button')].find(button => button.textContent.trim() === ${JSON.stringify(text)})?.click()`);
  const input = (selector, value) => js(`(() => { const input = document.querySelector(${JSON.stringify(selector)}); input.value = ${JSON.stringify(value)}; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  const filter = value => js(`(() => { const select = document.querySelector('[aria-label="Project status"]'); select.value = '${value}'; select.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  const manage = label => js(`document.querySelector('.project-card[aria-label="${label}"] .project-actions-trigger').click()`);
  await call('Runtime.enable');
  await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await call('Page.navigate', { url: `${base}/projects` });
  await until(() => js(`document.querySelectorAll('.project-card').length === 2 && document.querySelectorAll('.nav-history-workspace').length === 2`), 'empty project catalog and navigation');
  assert.equal(await js(`document.querySelectorAll('.nav-history-item').length`), 0);
  const beforeThreadEvents = navRequests;
  await js(`for (let index = 0; index < 50; index++) window.dispatchEvent(new CustomEvent('k41:threads-changed'))`);
  await delay(100);
  assert.equal(navRequests, beforeThreadEvents, 'Thread events wait for the debounce delay.');
  await until(() => navRequests === beforeThreadEvents + 1, 'thread events coalesce into one navigation refresh');
  await delay(300);
  assert.equal(navRequests, beforeThreadEvents + 1);
  failCatalog = true;
  await js(`document.querySelector('[aria-label="Refresh projects"]').click()`);
  await until(() => js(`document.querySelector('[role="alert"]')?.textContent.includes('Catalog unavailable.')`), 'catalog error');
  assert.equal(await js(`document.querySelectorAll('.project-card').length`), 2);
  failCatalog = false; await clickText('Retry');
  await until(() => js(`!document.querySelector('[role="alert"]') && document.querySelector('.projects-list')?.getAttribute('aria-busy') === 'false'`), 'catalog retry');
  await manage('Empty project');
  await until(() => js(`!!document.querySelector('[role="menu"]')`), 'project menu');
  await js(`document.querySelector('[role="menu"]').dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }))`);
  assert.equal(await js('document.activeElement.textContent'), 'Remove from projects');
  await js(`document.querySelector('[role="menu"]').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))`);
  assert.equal(await js(`document.activeElement.getAttribute('aria-label')`), 'Manage Empty project');
  await manage('Empty project'); await clickText('Rename');
  await until(() => js(`!!document.querySelector('.project-form input')`), 'rename dialog');
  await input('.project-form input', 'Renamed project'); await clickText('Save');
  await until(() => js(`!!document.querySelector('.project-card[aria-label="Renamed project"]') && !!document.querySelector('.nav-history-workspace[aria-label="Renamed project"]')`), 'rename shared with navigation');
  assert.equal(projects[0].workspace.execution.locator, '/projects/1');
  await manage('Other project'); await clickText('Pin project');
  await until(() => js(`document.querySelector('.nav-history-workspace').getAttribute('aria-label') === 'Other project'`), 'pinned ordering');
  await manage('Renamed project'); await clickText('Archive project');
  await until(() => js(`document.querySelectorAll('.project-card').length === 1 && document.querySelectorAll('.nav-history-workspace').length === 1`), 'archive removes active row');
  await filter('archived');
  await until(() => js(`!!document.querySelector('.project-card[aria-label="Renamed project"]')`), 'archived filter');
  await manage('Renamed project'); await clickText('Restore project'); await filter('active');
  await until(() => js(`document.querySelectorAll('.project-card').length === 2`), 'restore archived project');
  await manage('Renamed project'); await clickText('Remove from projects');
  await until(() => js(`!!document.querySelector('.confirm-dialog-content')`), 'remove confirmation');
  assert.equal(await js(`document.querySelector('.confirm-dialog-content').textContent.includes('Conversations, files, and sandboxes will be kept.')`), true);
  await clickText('Remove project');
  await until(() => js(`document.querySelectorAll('.project-card').length === 1`), 'soft removal');
  await call('Page.reload');
  await until(() => js(`document.querySelectorAll('.project-card').length === 1 && document.querySelectorAll('.nav-history-workspace').length === 1`), 'removed state survives reload');
  await filter('removed');
  await until(() => js(`!!document.querySelector('.project-card[aria-label="Renamed project"]')`), 'removed filter');
  assert.equal(await js(`document.querySelector('.project-card-footer a').getAttribute('href')`), '/history?workspace=local%3A%2Fprojects%2F1');
  await manage('Renamed project'); await clickText('Restore project'); await filter('active');
  await until(() => js(`document.querySelectorAll('.project-card').length === 2`), 'restore removed project');
  await input('[aria-label="Search projects"]', 'Other');
  await until(() => js(`document.querySelectorAll('.project-card').length === 1 && document.querySelector('.project-card h2').textContent.includes('Other')`), 'search projects');
  await input('[aria-label="Search projects"]', '');
  await until(() => js(`document.querySelectorAll('.project-card').length === 2`), 'clear search');
  unavailable = true; await clickText('New chat');
  await until(() => js(`document.body.textContent.includes('Workspace is unavailable.')`), 'unavailable workspace error');
  assert.equal(await js('location.pathname'), '/projects');
  assert.equal(projects.length, 2);
  unavailable = false;
  await clickText('Add project');
  await until(() => js(`!!document.querySelector('.workspace-selector')`), 'add project dialog');
  assert.equal(await js(`[...document.querySelectorAll('[role="tab"]')].some(tab => tab.textContent.includes('Temporary'))`), false);
  await input('.project-form > .field input', 'Added project');
  await input('[placeholder="Path to project directory..."]', '/projects/new');
  await until(() => js(`!!document.querySelector('.workspace-apply-btn') && !document.querySelector('.workspace-apply-btn').disabled`), 'workspace default');
  await js(`document.querySelector('.workspace-apply-btn').click()`);
  await js(`document.querySelector('.project-form > button').click()`);
  await until(() => js(`document.querySelectorAll('.project-card').length === 3 && document.querySelectorAll('.nav-history-workspace').length === 3`), 'add project without conversations');
  console.log('PASS: empty projects, rename, pin, archive, removal, restoration, search, workspace errors, and creation.');
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await until(() => js(`!!document.querySelector('.topbar-menu-toggle')`), 'mobile layout');
  assert.equal(await js('document.documentElement.scrollWidth <= innerWidth'), true);
  await manage('Other project');
  await until(() => js(`!!document.querySelector('[role="menu"]')`), 'mobile actions');
  assert.equal(await js(`(() => { const r = document.querySelector('[role="menu"]').getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.bottom <= innerHeight; })()`), true);
  await clickText('Unpin project');
  await js(`document.querySelector('.topbar-menu-toggle').click()`);
  await until(() => js(`document.querySelector('.app-layout').classList.contains('app-layout--drawer-open')`), 'mobile navigation');
  assert.equal(await js(`document.querySelectorAll('.nav-history-workspace').length`), 3);
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  assert.ok(mutations.some(mutation => mutation.method === 'DELETE'));
  console.log('PASS: keyboard menu navigation and focus return, mobile layout, menu bounds, and project drawer.');
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await call('Page.navigate', { url: `${base}/projects` });
  await until(() => js(`!!document.querySelector('.project-card h2 a')`), 'project detail entry');
  await js(`document.querySelector('.project-card[aria-label="Renamed project"] h2 a').click()`);
  await until(() => js(`location.pathname === '/projects/1' && !![...document.querySelectorAll('button')].find(b => b.textContent === 'Create AGENTS.md')`), 'missing root instructions');
  assert.equal(instructionDocument.exists, false);
  await clickText('Create AGENTS.md');
  await until(() => js(`!!document.querySelector('[aria-label="AGENTS.md content"]')`), 'instruction editor');
  await input('[aria-label="AGENTS.md content"]', '# Project guide\nUse uv.');
  await clickText('Save instructions');
  await until(() => js(`document.querySelector('[aria-label="Project instructions"] h1')?.textContent === 'Project guide'`), 'markdown instructions');
  assert.equal(instructionDocument.content, '# Project guide\nUse uv.');
  await clickText('Source');
  assert.equal(await js(`document.querySelector('.project-resource-source').textContent`), instructionDocument.content);
  await clickText('Edit instructions');
  await input('[aria-label="AGENTS.md content"]', 'Unsaved changes');
  assert.equal(await js(`(() => { const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented; })()`), true);
  const beforeDialog = dialogCount;
  await js(`document.querySelector('.project-resource-tabs a[href$="tab=skills"]').click()`);
  await until(() => dialogCount > beforeDialog, 'unsaved navigation guard');
  assert.equal(await js(`document.querySelector('[aria-label="AGENTS.md content"]').value`), 'Unsaved changes');
  assert.equal(await js(`location.search.includes('skills')`), false);
  await clickText('Cancel editing');
  assert.equal(instructionDocument.content, '# Project guide\nUse uv.');
  await clickText('Edit instructions');
  await input('[aria-label="AGENTS.md content"]', 'Preserved draft');
  const beforeBackgroundDialog = dialogCount;
  const beforeBackgroundRead = instructionRequests.length;
  const beforeBackgroundPoll = detailRequests.length;
  await until(() => detailRequests.length > beforeBackgroundPoll, 'background project status poll');
  await delay(200);
  assert.equal(dialogCount, beforeBackgroundDialog, 'Status polling must not ask to discard the draft.');
  assert.equal(instructionRequests.length, beforeBackgroundRead, 'Status polling must not reload instructions.');
  const backgroundTarget = await call('Target.createTarget', { url: 'about:blank' });
  await until(() => js(`document.visibilityState === 'hidden'`), 'project page is hidden');
  await delay(100);
  const hiddenReads = detailRequests.length;
  await delay(5500);
  assert.equal(detailRequests.length, hiddenReads, 'Hidden project pages stop status polling.');
  await call('Target.closeTarget', { targetId: backgroundTarget.targetId });
  await call('Page.bringToFront');
  await until(() => detailRequests.length > hiddenReads, 'returning to the project immediately refreshes its status');
  assert.equal(await js(`document.querySelector('[aria-label="AGENTS.md content"]').value`), 'Preserved draft');
  const beforeCheckpointRefresh = detailRequests.length;
  await clickText('Save now');
  await until(async () => detailRequests.length > beforeCheckpointRefresh && await js(`!!document.querySelector('[aria-label="AGENTS.md content"]') && !document.querySelector('.projects-page [role="status"]')?.textContent.includes('Loading project')`), 'checkpoint keeps editor');
  await delay(200);
  assert.equal(dialogCount, beforeBackgroundDialog, 'Checkpoint refresh must not ask to discard the draft.');
  assert.equal(instructionRequests.length, beforeBackgroundRead, 'Checkpoint refresh must not reload instructions.');
  assert.equal(await js(`document.querySelector('[aria-label="AGENTS.md content"]').value`), 'Preserved draft');
  instructionDocument = { ...instructionDocument, content: 'External change', version: 'external' };
  await clickText('Save instructions');
  await until(() => js(`document.querySelector('[role="alert"]')?.textContent.includes('File changed')`), 'save conflict');
  assert.equal(await js(`document.querySelector('[aria-label="AGENTS.md content"]').value`), 'Preserved draft');
  acceptDialog = true;
  await clickText('Reload instructions');
  await until(() => js(`!document.querySelector('[aria-label="AGENTS.md content"]') && document.querySelector('.project-resource-source')?.textContent === 'External change'`), 'reload conflict');
  failInstructions = true;
  await clickText('Reload instructions');
  await until(() => js(`document.querySelector('[role="alert"]')?.textContent.includes('workspace is unavailable')`), 'workspace error separated from missing file');
  failSkills = true;
  await js(`document.querySelector('.project-resource-tabs a[href$="tab=skills"]').click()`);
  await until(() => js(`document.querySelector('[role="alert"]')?.textContent.includes('Skills unavailable')`), 'independent skills error');
  failSkills = false;
  await clickText('Retry skills');
  await until(() => js(`document.querySelectorAll('.project-skill-card').length === 3`), 'project shared and invalid skills');
  assert.equal(await js(`document.querySelectorAll('.project-skill-card')[1].textContent.includes('Shadowed')`), true);
  assert.equal(await js(`document.querySelectorAll('.project-skill-card')[2].textContent.includes('Invalid document')`), true);
  await input('[aria-label="Search skills"]', 'Shared');
  await until(() => js(`document.querySelectorAll('.project-skill-card').length === 1`), 'skill search');
  await clickText('View skill');
  await until(() => js(`document.querySelector('[aria-label="Skill instructions"] h1')?.textContent === 'Skill guide'`), 'read-only shared skill');
  assert.equal(await js(`document.querySelectorAll('[role="dialog"] input, [role="dialog"] textarea').length`), 0);
  await clickText('Skill source');
  assert.equal(await js(`document.querySelector('[aria-label="Skill instructions"] pre').textContent.startsWith('---')`), true);
  await clickText('Load more resources');
  await until(() => js(`document.querySelector('[aria-label="Skill resources"]').textContent.includes('notes.txt')`), 'resource pagination');
  await clickText('notes.txt');
  await until(() => js(`document.querySelector('[aria-label="Resource preview"] pre')?.textContent === 'Resource notes'`), 'text resource');
  await clickText('Folder: assets');
  await until(() => js(`document.querySelector('[aria-label="Skill resources"]').textContent.includes('template.bin')`), 'resource directory');
  await clickText('template.bin');
  await until(() => js(`document.querySelector('[aria-label="Resource preview"]')?.textContent.includes('No text preview')`), 'binary metadata');
  assert.deepEqual(await js(`fetch(document.querySelector('[aria-label="Resource preview"] a').href).then(r => r.arrayBuffer()).then(b => [...new Uint8Array(b)])`), [0, 255]);
  await clickText('large.txt');
  await until(() => js(`document.querySelector('[aria-label="Resource preview"]')?.textContent.includes('2000000 bytes')`), 'large resource metadata');
  await clickText('Parent directory');
  await until(() => js(`document.querySelector('[aria-label="Skill resources"]').textContent.includes('Folder: assets')`), 'resource parent');
  assert.ok(skillRequests.every(request => request.method === 'GET' && request.workspace.locator === '/projects/1'));
  await js(`document.querySelector('.dialog-close-btn').click()`);
  await input('[aria-label="Search skills"]', '');
  await until(() => js(`document.querySelectorAll('.project-skill-card').length === 3`), 'clear skill search');
  await js(`document.querySelector('.project-skill-card button').click()`);
  await until(() => js(`!!document.querySelector('[aria-label="Skill instructions"]')`), 'read-only project skill');
  assert.equal(await js(`document.querySelectorAll('[role="dialog"] input, [role="dialog"] textarea').length`), 0);
  await js(`document.querySelector('.dialog-close-btn').click()`);
  failInstructions = false;
  await call('Page.reload');
  await until(() => js(`location.search === '?tab=skills' && document.querySelectorAll('.project-skill-card').length === 3`), 'direct skill tab reload');
  await js(`document.querySelector('.project-resource-tabs a[href$="tab=secrets"]').click()`);
  await until(() => js(`document.querySelector('[aria-label="Project secrets"]')?.textContent.includes('This project has no secrets.')`), 'empty secret list');
  await clickText('Add secret');
  const secretProject = projects.find(project => project.id === 1);
  const beforeSecretPoll = detailRequests.length;
  const beforeSecretRefresh = secretRequests.length;
  secretProject.persistence.last_saved_at = '2026-01-02T00:00:00Z';
  await until(() => js(`document.querySelector('.project-storage-status')?.textContent.includes(new Date('2026-01-02T00:00:00Z').toLocaleString())`), 'project polling while empty secret form is open');
  assert.ok(detailRequests.length > beforeSecretPoll);
  assert.equal(await js(`!!document.querySelector('[aria-label="Secret name"]')`), true, 'polling keeps the empty secret form open');
  assert.equal(secretRequests.length, beforeSecretRefresh, 'polling the same project does not reload secrets');
  await input('[aria-label="Secret name"]', 'DEPLOY_TOKEN');
  await input('[aria-label="Secret value"]', 'browser-private-token');
  secretProject.persistence.last_saved_at = '2026-01-03T00:00:00Z';
  await js(`window.dispatchEvent(new CustomEvent('k41:workspace-files-changed'))`);
  await until(() => js(`document.querySelector('.project-storage-status')?.textContent.includes(new Date('2026-01-03T00:00:00Z').toLocaleString())`), 'workspace event refresh while secret draft is open');
  assert.equal(await js(`document.querySelector('[aria-label="Secret name"]')?.value`), 'DEPLOY_TOKEN');
  assert.equal(await js(`document.querySelector('[aria-label="Secret value"]')?.value`), 'browser-private-token');
  assert.equal(secretRequests.length, beforeSecretRefresh, 'workspace event refresh preserves the same secret editor');
  assert.equal(await js(`document.querySelector('[aria-label="Secret value"]').type`), 'password');
  assert.equal(await js(`(() => { const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented; })()`), true);
  acceptDialog = false;
  const beforeSecretDialog = dialogCount;
  await js(`document.querySelector('.project-resource-tabs a[href$="tab=skills"]').click()`);
  await until(() => dialogCount > beforeSecretDialog, 'unsaved secret tab guard');
  assert.equal(await js(`location.search`), '?tab=secrets');
  assert.equal(await js(`document.querySelector('[aria-label="Secret value"]').value`), 'browser-private-token');
  await js(`document.querySelector('a[href="/projects"]').click()`);
  await until(() => dialogCount > beforeSecretDialog + 1, 'unsaved secret project navigation guard');
  assert.equal(await js(`location.pathname`), '/projects/1');
  assert.equal(await js(`document.querySelector('[aria-label="Secret name"]').value`), 'DEPLOY_TOKEN');
  acceptDialog = true;
  await clickText('Save secret');
  await until(() => js(`!!document.querySelector('[aria-label="Disable DEPLOY_TOKEN"]') && !document.querySelector('[aria-label="Secret value"]')`), 'create and clear secret form');
  assert.equal(projectSecrets.get(1)[0].value, 'browser-private-token');
  assert.equal(await js(`document.body.textContent.includes('browser-private-token')`), false);
  assert.equal(await js(`JSON.stringify(localStorage).includes('browser-private-token') || JSON.stringify(sessionStorage).includes('browser-private-token')`), false);
  await js(`document.querySelector('[aria-label="Disable DEPLOY_TOKEN"]').click()`);
  await until(() => js(`!!document.querySelector('[aria-label="Enable DEPLOY_TOKEN"]')`), 'disable secret');
  assert.deepEqual(secretRequests.filter(request => request.method === 'PATCH').at(-1).body, { enabled: false });
  await js(`document.querySelector('[aria-label="Enable DEPLOY_TOKEN"]').click()`);
  await until(() => js(`!!document.querySelector('[aria-label="Disable DEPLOY_TOKEN"]')`), 'enable secret');
  await js(`document.querySelector('[aria-label="Edit DEPLOY_TOKEN"]').click()`);
  assert.equal(await js(`document.querySelector('[aria-label="Secret value"]').value`), '');
  await clickText('Save secret');
  await until(() => js(`!document.querySelector('[aria-label="Secret value"]')`), 'empty replacement preserves value');
  assert.equal(projectSecrets.get(1)[0].value, 'browser-private-token');
  await until(() => js(`document.querySelector('[aria-label="Edit DEPLOY_TOKEN"]')?.disabled === false`), 'secret save finishes before editing again');
  await js(`document.querySelector('[aria-label="Edit DEPLOY_TOKEN"]').click()`);
  await input('[aria-label="Secret value"]', 'replaced-private-token');
  secretProject.persistence.last_saved_at = '2026-01-04T00:00:00Z';
  await until(() => js(`document.querySelector('.project-storage-status')?.textContent.includes(new Date('2026-01-04T00:00:00Z').toLocaleString())`), 'project polling while replacing a secret');
  assert.equal(await js(`document.querySelector('[aria-label="Secret name"]')?.disabled`), true);
  assert.equal(await js(`document.querySelector('[aria-label="Secret name"]')?.value`), 'DEPLOY_TOKEN');
  assert.equal(await js(`document.querySelector('[aria-label="Secret value"]')?.value`), 'replaced-private-token');
  await clickText('Save secret');
  await until(() => projectSecrets.get(1)[0].value === 'replaced-private-token', 'replace secret');
  failSecrets = true;
  await until(() => js(`!document.querySelector('[aria-label="Secret value"]')`), 'replacement form closed');
  await clickText('Reload secrets');
  await until(() => js(`document.querySelector('[role="alert"]')?.textContent.includes('Secrets temporarily unavailable')`), 'secret loading failure');
  failSecrets = false;
  delaySecrets = true;
  await clickText('Reload secrets');
  await until(() => Boolean(finishSecrets), 'delayed secret response');
  await js(`history.pushState(null, '', '/projects/2?tab=secrets'); dispatchEvent(new PopStateEvent('popstate'));`);
  await until(() => js(`location.pathname === '/projects/2' && document.querySelector('[aria-label="Project secrets"]')?.textContent.includes('This project has no secrets.')`), 'switch secret project');
  finishSecrets();
  await delay(200);
  assert.equal(await js(`document.querySelectorAll('.project-secret-row').length`), 0);
  await clickText('Add secret');
  await input('[aria-label="Secret name"]', 'UNSAVED_TOKEN');
  await input('[aria-label="Secret value"]', 'unsaved-private-value');
  const beforeDiscardSecretDialog = dialogCount;
  await js(`document.querySelector('.project-resource-tabs a[href$="tab=skills"]').click()`);
  await until(() => js(`location.search === '?tab=skills' && !document.querySelector('[aria-label="Secret value"]')`), 'discard secret draft before leaving');
  assert.equal(dialogCount, beforeDiscardSecretDialog + 1);
  await js(`document.querySelector('.project-resource-tabs a[href$="tab=secrets"]').click()`);
  await until(() => js(`!!document.querySelector('[aria-label="Project secrets"]')`), 'return after discarding secret draft');
  await clickText('Add secret');
  assert.equal(await js(`document.querySelector('[aria-label="Secret value"]').value`), '');
  await input('[aria-label="Secret name"]', 'UNSAVED_TOKEN');
  await input('[aria-label="Secret value"]', 'unsaved-private-value');
  await js(`history.pushState(null, '', '/projects/1?tab=secrets'); dispatchEvent(new PopStateEvent('popstate'));`);
  await until(() => js(`!!document.querySelector('[aria-label="Delete DEPLOY_TOKEN"]')`), 'return to first secret project');
  assert.equal(await js(`document.querySelector('[aria-label="Secret value"]')?.value || ''`), '');
  await js(`document.querySelector('[aria-label="Delete DEPLOY_TOKEN"]').click()`);
  await until(() => js(`document.querySelector('[aria-label="Project secrets"]')?.textContent.includes('This project has no secrets.')`), 'delete secret');
  assert.equal(projectSecrets.get(1).length, 0);
  await clickText('Add secret');
  await input('[aria-label="Secret value"]', 'cancelled-private-value');
  await clickText('Cancel');
  await clickText('Add secret');
  assert.equal(await js(`document.querySelector('[aria-label="Secret value"]').value`), '');
  await clickText('Cancel');
  const profileSelect = `document.querySelector('[aria-label="Profile for conversations"]')`;
  const profilePanel = `document.querySelector('[aria-label="Browser login profiles"]')`;
  await js(`document.querySelector('.project-resource-tabs a[href$="tab=browser"]').click()`);
  await until(() => js(`${profileSelect} && !${profileSelect}.disabled`), 'project login selection loaded');
  assert.equal(await js(`${profileSelect}.value`), 'inherit', 'projects inherit the account default');
  assert.equal(await js(`!!document.querySelector('[aria-label="New profile name"]')`), false, 'project does not manage shared profiles');
  assert.equal(await js(`location.search`), '?tab=browser');
  await js(`document.querySelector('a[href="/settings/browser#login-profiles"]').click()`);
  await until(() => js(`${profilePanel}?.textContent.includes('Default (built-in)')`), 'profile management opens with the built-in default');
  assert.equal(await js(`!!document.querySelector('[aria-label="Delete Default"]')`), false, 'built-in profile cannot be deleted');
  assert.equal(await js(`!!document.querySelector('[aria-label="Rename Default"]')`), false, 'built-in profile cannot be renamed');
  assert.equal(await js('location.pathname'), '/settings/browser');
  await input('[aria-label="New profile name"]', 'Work');
  await clickText('Create profile');
  await until(() => js(`!!document.querySelector('[aria-label="Rename Work"]')`), 'login profile created');
  assert.equal(await js(`document.querySelector('[aria-label="New profile name"]').value`), '');
  await input('[aria-label="New profile name"]', 'work');
  await clickText('Create profile');
  await until(() => js(`${profilePanel}.querySelector('[role="alert"]')?.textContent.includes('already exists')`), 'duplicate profile name reports the server message');
  assert.equal(browserProfiles.length, 2);
  const workProfile = browserProfiles.find(profile => !profile.is_builtin);
  await input('[aria-label="New profile name"]', '');
  const defaultSelect = `document.querySelector('[aria-label="Default login profile"]')`;
  rejectDefaultProfile = true;
  await js(`{ const select = ${defaultSelect}; select.value = select.options[1].value; select.dispatchEvent(new Event('change', { bubbles: true })); }`);
  await until(() => js(`${profilePanel}.querySelector('[role="alert"]')?.textContent.includes('Default profile rejected.') && !${defaultSelect}.disabled`), 'rejected account default reported');
  assert.equal(await js(`${defaultSelect}.value`), builtinBrowserProfile.profile_id, 'failed default save restores the saved option');
  rejectDefaultProfile = false;
  await js(`{ const select = ${defaultSelect}; select.value = select.options[1].value; select.dispatchEvent(new Event('change', { bubbles: true })); }`);
  await until(() => js(`${profilePanel}.textContent.includes('Account default login profile updated.') && !${defaultSelect}.disabled`), 'account default saved');
  assert.equal(defaultBrowserProfile, workProfile.profile_id);
  const temporarySelect = `document.querySelector('[aria-label="No workspace login profile"]')`;
  assert.equal(await js(`${temporarySelect}.value`), builtinBrowserProfile.profile_id, 'temporary profile stays independent of the account default');
  await js(`{ const select = ${temporarySelect}; select.value = '${workProfile.profile_id}'; select.dispatchEvent(new Event('change', { bubbles: true })); }`);
  await until(() => js(`${profilePanel}.textContent.includes('No workspace login profile updated.') && !${temporarySelect}.disabled`), 'temporary profile saved');
  assert.equal(temporaryBrowserProfile, workProfile.profile_id);
  await call('Page.navigate', { url: `${base}/projects/1?tab=browser` });
  await until(() => js(`${profileSelect} && !${profileSelect}.disabled`), 'return to project profile selection');
  assert.equal(await js(`${profileSelect}.value`), 'inherit', 'project continues inheriting the changed account default');
  await js(`{ const select = ${profileSelect}; select.value = 'inherit'; select.dispatchEvent(new Event('change', { bubbles: true })); }`);
  await until(() => js(`${profileSelect}.value === 'inherit' && !${profileSelect}.disabled && ${profilePanel}.textContent.includes('Effective profile: Work')`), 'project inherits account default');
  assert.equal(browserProjectModes.get(1), 'inherit');
  await js(`{ const select = ${profileSelect}; select.value = '${builtinBrowserProfile.profile_id}'; select.dispatchEvent(new Event('change', { bubbles: true })); }`);
  await until(() => js(`${profileSelect}.value === '${builtinBrowserProfile.profile_id}' && !${profileSelect}.disabled && ${profilePanel}.textContent.includes('Effective profile: Default')`), 'project selects a specific saved profile');
  await js(`{ const select = ${profileSelect}; select.value = select.options[2].value; select.dispatchEvent(new Event('change', { bubbles: true })); }`);
  await until(() => browserProjectProfiles.get(1) === workProfile.profile_id, 'project login profile saved');
  await until(() => js(`${profilePanel}.textContent.includes('Project login profile updated.') && ${profileSelect}.value === '${workProfile.profile_id}' && !${profileSelect}.disabled`), 'project login profile shown');
  assert.equal(browserProjectProfiles.get(2), undefined, 'selecting a profile affects only this project');
  rejectProjectProfile = true;
  await js(`{ const select = ${profileSelect}; select.value = 'inherit'; select.dispatchEvent(new Event('change', { bubbles: true })); }`);
  await until(() => js(`${profilePanel}.querySelector('[role="alert"]')?.textContent.includes('not found') && !${profileSelect}.disabled`), 'rejected project profile reported');
  assert.equal(await js(`${profileSelect}.value`), workProfile.profile_id, 'a rejected selection restores the saved profile');
  assert.equal(browserProjectProfiles.get(1), workProfile.profile_id);
  rejectProjectProfile = false;
  await js(`document.querySelector('a[href="/settings/browser#login-profiles"]').click()`);
  await until(() => js(`!!document.querySelector('[aria-label="Rename Work"]')`), 'settings shows project profile usage');
  assert.ok(await js(`${profilePanel}.querySelector('a[href="/projects/1?tab=browser"]')`), 'profile links to the project using it');
  await js(`document.querySelector('[aria-label="Rename Work"]').click()`);
  await until(() => js(`!!document.querySelector('[aria-label="New name for Work"]')`), 'rename form');
  await input('[aria-label="New name for Work"]', 'Office');
  await clickText('Save name');
  await until(() => js(`!!document.querySelector('[aria-label="Rename Office"]')`), 'login profile renamed');
  workProfile.open_thread_ids = ['open-conversation', 'another-conversation'];
  await clickText('Reload profiles');
  await until(() => js(`${profilePanel}.textContent.includes('Open in conversation open-conversation') && document.querySelector('[aria-label="Delete Office"]').disabled`), 'open profile cannot be deleted');
  assert.ok(await js(`${profilePanel}.textContent.includes('Open in conversation another-conversation')`), 'all conversations sharing the browser are listed');
  workProfile.open_thread_ids = [];
  await clickText('Reload profiles');
  await until(() => js(`!document.querySelector('[aria-label="Delete Office"]').disabled`), 'closed profile can be deleted');
  await js(`window.confirm = () => false; document.querySelector('[aria-label="Delete Office"]').click()`);
  assert.equal(browserProfiles.length, 2, 'declining the confirmation keeps the profile');
  await js(`window.confirm = () => true; document.querySelector('[aria-label="Delete Office"]').click()`);
  await until(() => js(`${defaultSelect}.value === '${builtinBrowserProfile.profile_id}' && ${temporarySelect}.value === '${builtinBrowserProfile.profile_id}'`), 'deleted login profile restores built-in defaults');
  assert.ok(await js(`${profilePanel}.textContent.includes('K41 removes them automatically')`), 'files still in use are reported after deleting a profile');
  assert.equal(browserProjectProfiles.get(1), undefined);
  assert.equal(defaultBrowserProfile, builtinBrowserProfile.profile_id);
  assert.equal(temporaryBrowserProfile, builtinBrowserProfile.profile_id);
  await call('Page.navigate', { url: `${base}/projects/1?tab=browser` });
  await until(() => js(`${profileSelect} && !${profileSelect}.disabled && ${profileSelect}.value === 'inherit'`), 'deleted profile resets project selection');
  await js(`document.querySelector('.project-resource-tabs a[href$="tab=secrets"]').click()`);
  await until(() => js(`!!document.querySelector('[aria-label="Project secrets"]')`), 'return to project secrets');
  projects.find(project => project.id === 1).runtime.status = 'stopped';
  failInstructions = true;
  const beforeSecretReload = await js('performance.timeOrigin');
  await call('Page.reload');
  await until(() => js(`performance.timeOrigin !== ${beforeSecretReload} && document.querySelector('[aria-label="Project secrets"]')?.textContent.includes('This project has no secrets.')`), 'secret management with stopped runtime');
  projects.find(project => project.id === 1).runtime.status = 'ready';
  failInstructions = false;
  assert.equal(await js(`document.querySelector('[aria-label="Project secrets"]').textContent.includes('newly started commands')`), true);
  console.log('PASS: project secret creation, replacement, toggles, deletion, errors, stale responses, draft clearing, and stopped runtimes.');
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  assert.equal(await js('document.documentElement.scrollWidth <= innerWidth'), true);
  await call('Page.navigate', { url: `${base}/projects/999` });
  await until(() => js(`document.querySelector('[role="alert"]')?.textContent.includes('Project not found')`), 'missing project detail');
  const migrationProject = projects.find(item => item.id === 1);
  migrationProject.persistence = { ...migrationProject.persistence, status: 'error', last_error: 'Storage offline', blocked_for_writes: true };
  await call('Page.navigate', { url: `${base}/projects/1` });
  await until(() => js(`document.querySelector('.project-storage-status')?.textContent.includes('Save failed')`), 'durable storage error');
  await clickText('Retry save');
  await until(() => js(`document.querySelector('.project-storage-status strong')?.textContent === 'Saved'`), 'retry checkpoint');
  migrationProject.migration_status = 'needs_selection';
  migrationProject.workspaces = [101, 102].map(id => ({ id, kind: 'legacy', source: { ...migrationProject.workspace.execution, locator: `/copies/${id}` },
    persistence: { ...migrationProject.persistence, revision: String(id).padEnd(64, 'a') }, runtime: { status: 'ready', generation: 1 } }));
  await call('Page.reload');
  await until(() => js(`document.querySelectorAll('.project-migration-source').length === 2`), 'preserved migration copies');
  await until(() => js(`document.querySelector('[aria-label="Project instructions"]')?.getAttribute('aria-busy') === 'false' && document.querySelector('[aria-label="Project instructions"]')?.textContent.includes('External change')`), 'original migration instructions');
  const beforeSelectionRead = instructionRequests.length;
  instructionDocument = { ...instructionDocument, content: '# Selected workspace guide', version: 'selected-workspace' };
  await js(`document.querySelectorAll('.project-migration-source button')[1].click()`);
  await until(() => js(`!document.querySelector('[aria-label="Workspace migration"]')`), 'select main workspace');
  await until(async () => instructionRequests.length > beforeSelectionRead && await js(`document.querySelector('[aria-label="Project instructions"] h1')?.textContent === 'Selected workspace guide'`), 'selected workspace instructions');
  assert.equal(migrationProject.workspace.execution.locator, '/copies/102');
  assert.equal(migrationProject.workspaces.length, 2);
  migrationProject.workspace_name = 'main';
  migrationProject.local_link = { path: '/projects/1', revision: 'c'.repeat(64), status: 'pending' };
  await call('Page.reload');
  await until(() => js(`document.querySelector('.workspace-storage-summary')?.textContent.includes('/copies/102') &&
    document.querySelector('.workspace-storage-summary')?.textContent.includes('/projects/1')`), 'working location and linked directory are separate');
  await clickText('Sync to local');
  await until(() => js(`document.body.textContent.includes('1 conflicts')`), 'local synchronization conflict preview');
  assert.equal(await js(`[...document.querySelectorAll('button')].find(button => button.textContent === 'Apply previewed changes').disabled`), true);
  assert.equal(localSyncRequests.some(body => body.preview_token), false);
  localSyncConflict = false;
  await clickText('Sync to local');
  await until(() => js(`[...document.querySelectorAll('button')].some(button => button.textContent === 'Apply previewed changes' && !button.disabled)`), 'local sync ready to apply');
  const beforeSyncPoll = detailRequests.length;
  await until(() => detailRequests.length > beforeSyncPoll, 'status polling preserves the local sync preview');
  assert.equal(await js(`[...document.querySelectorAll('button')].some(button => button.textContent === 'Apply previewed changes' && !button.disabled)`), true);
  await clickText('Apply previewed changes');
  await until(() => js(`document.body.textContent.includes('Changes applied.')`), 'apply reviewed local changes');
  assert.equal(localSyncRequests.at(-1).preview_token, 'b'.repeat(64));
  assert.equal(localSyncRequests.at(-1).expected_generation, 1);
  assert.equal(migrationProject.workspace.execution.locator, '/copies/102');
  console.log('PASS: main workspace location, linked local directory, conflict preview, and explicit local synchronization.');
  await call('Page.navigate', { url: `${base}/projects/1` });
  await until(() => js(`location.pathname === '/projects/1' && !!document.querySelector('.project-storage-status')`), 'project before delayed failure');
  delayRuntimeFailure = true;
  await clickText('New chat');
  await until(() => !!failPendingRuntime, 'pending runtime request');
  await js(`history.pushState(null, '', '/projects/2'); dispatchEvent(new PopStateEvent('popstate'));`);
  await until(() => js(`location.pathname === '/projects/2' && document.querySelector('.page-title')?.textContent === 'Other project'`), 'switch project during runtime request');
  const beforeFailure = detailRequests.filter(id => id === 2).length;
  failPendingRuntime();
  await until(() => js(`![...document.querySelectorAll('button')].some(button => button.textContent === 'Preparing workspace...')`), 'delayed failure settled');
  assert.equal(detailRequests.filter(id => id === 2).length, beforeFailure);
  assert.equal(await js(`document.body.textContent.includes('Delayed workspace failure.')`), false);
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: project details, instruction editing and conflicts, unsaved guards, read-only skills, resources, downloads, errors, and direct links.');

  for (const entry of ['catalog', 'detail', 'sidebar']) {
    const project = makeProject(1, 'Sandbox switch project');
    project.workspace = { ...project.workspace, project_id: 1, project_workspace_id: 51, runtime_generation: 1 };
    projects = [project];
    fixtures['/dashboard-api/catalog'].backends = [
      { name: 'local', title: 'Local', enabled: true },
      { name: 'daytona', title: 'Daytona', enabled: true },
    ];
    await js(`localStorage.setItem('k41-dashboard-sidebar', 'expanded')`);
    await call('Page.navigate', { url: `${base}${entry === 'detail' ? '/projects/1' : '/projects'}` });
    await until(() => js(entry === 'detail' ? `document.querySelector('.page-title')?.textContent === 'Sandbox switch project'`
      : `!!document.querySelector('.project-card[aria-label="Sandbox switch project"]')`), `${entry} project entry`);
    if (entry === 'sidebar') {
      await until(() => js(`!!document.querySelector('[aria-label="New chat in Sandbox switch project"]')`), 'sidebar new chat');
      await js(`document.querySelector('[aria-label="New chat in Sandbox switch project"]').click()`);
    } else await clickText('New chat');
    await until(() => js(`location.pathname === '/chat' && !!document.querySelector('.composer-workspace-trigger')`), `${entry} draft chat`);
    await js(`document.querySelector('.composer-workspace-trigger').click()`);
    await until(() => js(`!!document.querySelector('.composer-runtime-picker')`), `${entry} project runtime before first message`);
    assert.equal(await js(`!!document.querySelector('.workspace-selector')`), false, 'project draft must retain its shared workspace');
    await js(`document.querySelector('[aria-label="Execution backend"]').click()`);
    await js(`[...document.querySelectorAll('[role="option"]')].find(el => el.textContent.trim() === 'Daytona').click()`);
    const beforeSwitch = switchRequests.length;
    await clickText('Use selected backend');
    await until(() => js(`document.querySelector('.composer-workspace-backend').textContent.includes('Daytona')`), `${entry} draft backend selected`);
    assert.equal(switchRequests.length, beforeSwitch, 'draft selection must not change a runtime or allocate a sandbox');
    assert.equal(project.workspace.execution.backend, 'local');
    assert.equal(project.runtime.generation, 1);
    await js(`dispatchEvent(new CustomEvent('k41:workspace-runtime-changed', { detail: { project_workspace_id: 51 } }))`);
    await until(() => js(`document.querySelector('.composer-workspace-backend').textContent.includes('Daytona') && !document.querySelector('.chat-prompt-input').disabled`), `${entry} draft retains selection after refresh`);
    await js(`document.querySelector('[aria-label="Close project picker"]').click()`);
    const beforeChat = chatRequests.length;
    await input('.chat-prompt-input', 'Explain this project.');
    await js(`document.querySelector('[aria-label="Send message"]').click()`);
    await until(() => chatRequests.length === beforeChat + 1, `${entry} first message`);
    assert.deepEqual(chatRequests.at(-1).workspace, project.workspace, 'first message retains the full source binding');
    assert.equal(chatRequests.at(-1).execution_backend, 'daytona');
  }
  console.log('PASS: project drafts select a session backend without changing any runtime before the first message.');

  const prepareLostProject = (modalEnabled = true) => {
    const project = makeProject(1, 'Lost Modal project');
    project.workspace = { ...project.workspace, project_id: 1, project_workspace_id: 51, runtime_generation: 7,
      execution: { backend: 'modal', locator: 'sb-expired', label: project.label, metadata: { root: '/workspace' } } };
    project.runtime = { status: 'unavailable', generation: 7, decision: { reason: 'sandbox_lost', backend: 'modal' } };
    projects = [project];
    fixtures['/dashboard-api/catalog'].backends = [{ name: 'local', title: 'Local', enabled: true }, { name: 'modal', title: 'Modal', enabled: modalEnabled }];
  };
  const assertLocalChat = async () => {
    await until(() => js(`location.pathname === '/chat' && !!document.querySelector('.composer-workspace-trigger')`), 'new chat on local');
    assert.equal(await js(`document.querySelector('[role="dialog"]')?.textContent.includes('Recover project workspace') || false`), false);
    assert.equal(runtimeRequests.length, 0, 'opening a conversation must not initialize or recover execution');
    assert.equal(projects[0].runtime.generation, 1);
    assert.equal(projects[0].sandbox.runtime.generation, 7);
    assert.equal(projects[0].sandbox.workspace.execution.backend, 'modal');
    assert.equal(await js(`document.querySelector('.composer-workspace-backend').textContent.includes('Local')`), true, 'new chats default to local execution');
    assert.equal(newChatRequests.at(-1), 1);
  };
  prepareLostProject();
  await call('Page.navigate', { url: `${base}/projects` });
  await until(() => js(`!!document.querySelector('.project-card[aria-label="Lost Modal project"]')`), 'lost Modal project catalog');
  await clickText('New chat');
  await assertLocalChat();

  prepareLostProject(false);
  await call('Page.navigate', { url: `${base}/projects/1` });
  await until(() => js(`document.querySelector('.page-title')?.textContent === 'Lost Modal project'`), 'lost project detail');
  await clickText('New chat');
  await assertLocalChat();

  prepareLostProject();
  await js(`localStorage.setItem('k41-dashboard-sidebar', 'expanded')`);
  await call('Page.navigate', { url: `${base}/projects` });
  await until(() => js(`!!document.querySelector('[aria-label="New chat in Lost Modal project"]')`), 'lost project sidebar');
  await js(`document.querySelector('[aria-label="New chat in Lost Modal project"]').click()`);
  await assertLocalChat();
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: catalog, project detail, and sidebar start new chats locally and allow explicit backend switching.');

  projects = [makeProject(1, 'First scheduled project'), makeProject(2, 'Second scheduled project')];
  schedulerProjectPagination = true;
  await call('Page.navigate', { url: `${base}/scheduler` });
  await until(() => js(`!!document.querySelector('#scheduler-run-history')`), 'scheduler page');
  await clickText('New job');
  const projectSelect = `[aria-label="Scheduled task project"]`;
  await until(() => js(`document.querySelector('${projectSelect}')?.textContent.includes('No workspace')`), 'default No workspace selection');
  await input('.scheduler-task-input', 'Independent scheduled task');
  await clickText('Create job');
  await until(() => schedulerWrites.length === 1, 'create scheduled job');
  assert.equal(schedulerWrites[0].project_id, null);
  await until(() => js(`!!document.querySelector('[aria-label="Edit job"]') && !document.querySelector('[role="dialog"]')`), 'created job card');
  await js(`document.querySelector('[aria-label="Edit job"]').click()`);
  await until(() => js(`!!document.querySelector('${projectSelect}')`), 'edit job project selector');
  await until(() => js(`[...document.querySelectorAll('button')].some(button => button.textContent.trim() === 'Load more projects')`), 'first project page loaded');
  failCatalog = true;
  await clickText('Load more projects');
  await until(() => js(`!!document.querySelector('[role="alert"]')`), 'project pagination error');
  failCatalog = false;
  await clickText('Retry loading projects');
  await until(() => js(`!document.querySelector('[role="alert"]') && ![...document.querySelectorAll('button')].some(button => button.textContent.trim() === 'Loading projects…')`), 'project pagination retry');
  await js(`document.querySelector('${projectSelect}').click()`);
  await until(() => js(`[...document.querySelectorAll('[role="option"]')].some(option => option.textContent.trim() === 'Second scheduled project')`), 'second page project option');
  await js(`[...document.querySelectorAll('[role="option"]')].find(option => option.textContent.trim() === 'Second scheduled project').click()`);
  await clickText('Save Changes');
  await until(() => schedulerWrites.length === 2, 'save selected project');
  assert.equal(schedulerWrites[1].project_id, 2);
  await until(() => js(`!document.querySelector('[role="dialog"]')`), 'project edit closed');
  await js(`document.querySelector('[aria-label="Edit job"]').click()`);
  await until(() => js(`document.querySelector('${projectSelect}')?.textContent.includes('Second scheduled project')`), 'selected project loaded outside first page');
  await js(`document.querySelector('${projectSelect}').click()`);
  await js(`[...document.querySelectorAll('[role="option"]')].find(option => option.textContent.trim() === 'No workspace').click()`);
  await clickText('Save Changes');
  await until(() => schedulerWrites.length === 3, 'reset scheduled project');
  assert.equal(schedulerWrites[2].project_id, null);
  await until(() => js(`!document.querySelector('[role="dialog"]')`), 'reset edit closed');
  await clickText('View');
  await until(() => js(`!!document.querySelector('[role="dialog"] a[href="/c/scheduler-run-1"]')`), 'run-specific conversation link');
  assert.equal(await js(`!!document.querySelector('.nav-history-link[href="/c/scheduler-run-1"]')`), false);
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: scheduler defaults to No workspace, paginates and retries project selection, saves and resets explicit projects, and links each run outside the sidebar.');
  await clickText('Close');
  schedulerRuns.push(...['failed', 'running'].map((status, index) => ({ ...schedulerRuns[0],
    run_id: `run-${index + 2}`, status, task: `${status} task`, finished_at: status === 'running' ? null : schedulerRuns[0].finished_at })));
  schedulerJobs.push({ ...schedulerJobs[0], id: 'job-keep', task: 'Keep this job' });
  await call('Page.navigate', { url: `${base}/scheduler` });
  await until(() => js(`document.querySelectorAll('[aria-label="Delete run"]').length === 3 && document.querySelectorAll('.scheduler-job-card').length === 2`), 'scheduler deletion fixtures');
  assert.equal(await js(`document.querySelectorAll('[aria-label="Delete run"]')[2].disabled`), true);
  await js(`document.querySelector('[aria-label="Delete run"]').click()`);
  await until(() => js(`!!document.querySelector('.confirm-dialog-content')`), 'single run confirmation');
  assert.equal(await js(`document.querySelector('.confirm-dialog-content').textContent.includes('Scheduled task')`), true);
  await clickText('Cancel');
  assert.equal(schedulerDeletes.length, 0);
  assert.equal(schedulerRuns.length, 3);
  await js(`document.querySelector('[aria-label="Delete run"]').click()`);
  failSchedulerDelete = true;
  await js(`document.querySelector('[role="dialog"] .btn-danger').click()`);
  await until(() => js(`document.querySelector('.toast.error')?.textContent.includes('Scheduler deletion unavailable.') && !document.querySelector('[role="dialog"] .btn-danger').disabled`), 'run deletion error and retry');
  assert.equal(schedulerRuns.length, 3);
  failSchedulerDelete = false;
  delaySchedulerDelete = true;
  await js(`document.querySelector('[role="dialog"] .btn-danger').click()`);
  await until(() => !!finishSchedulerDelete, 'pending run deletion');
  const beforeDuplicate = schedulerDeletes.length;
  assert.equal(await js(`document.querySelector('[role="dialog"] .btn-danger').disabled`), true);
  await js(`document.querySelector('[role="dialog"] .btn-danger').click(); window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));`);
  assert.equal(schedulerDeletes.length, beforeDuplicate);
  assert.equal(await js(`!!document.querySelector('.confirm-dialog-content')`), true);
  finishSchedulerDelete();
  await until(() => js(`!document.querySelector('[role="dialog"]') && document.querySelectorAll('[aria-label="Delete run"]').length === 2 && document.querySelector('#scheduler-run-history .panel-title').textContent.includes('2 runs')`), 'only selected run removed and count refreshed');
  assert.deepEqual(schedulerRuns.map(run => run.run_id), ['run-2', 'run-3']);
  assert.equal(schedulerJobs.length, 2);
  await js(`document.querySelector('[aria-label="Delete job"]').click()`);
  await until(() => js(`!!document.querySelector('.confirm-dialog-content')`), 'single job confirmation');
  await clickText('Cancel');
  assert.equal(schedulerJobs.length, 2);
  await js(`document.querySelector('[aria-label="Delete job"]').click()`);
  failSchedulerDelete = true;
  const beforeSchedulerFailure = schedulerDeletes.length;
  await js(`document.querySelector('[role="dialog"] .btn-danger').click()`);
  await until(() => schedulerDeletes.length > beforeSchedulerFailure && js(`!!document.querySelector('[role="dialog"] .btn-danger') && !document.querySelector('[role="dialog"] .btn-danger').disabled`), 'job deletion error preserves confirmation');
  assert.equal(schedulerJobs.length, 2);
  failSchedulerDelete = false;
  await js(`document.querySelector('[role="dialog"] .btn-danger').click()`);
  await until(() => js(`!document.querySelector('[role="dialog"]') && document.querySelectorAll('.scheduler-job-card').length === 1`), 'only selected job removed');
  assert.equal(schedulerJobs[0].id, 'job-keep');
  assert.equal(schedulerRuns.length, 2);
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: individual scheduler deletion, cancellation, errors, retry, duplicate prevention, running protection, and preservation of other jobs and history.');
} finally {
  if (call && ws?.readyState === WebSocket.OPEN) await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws?.close(); browser.kill(); server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  const resolved = path.resolve(profile);
  assert.equal(path.dirname(resolved), path.resolve(os.tmpdir()));
  assert.ok(path.basename(resolved).startsWith('k41-projects-browser-'));
  fs.rmSync(resolved, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
}
