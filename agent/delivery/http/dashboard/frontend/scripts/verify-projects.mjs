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
let unavailable = false;
let delayRuntimeFailure = false;
let failPendingRuntime;
const detailRequests = [];
const instructionRequests = [];
let failCatalog = false;
let failInstructions = false;
let failSkills = false;
let instructionDocument = { path: 'AGENTS.md', exists: false, content: '', version: null };
const skillRequests = [];
const makeSkill = (id, scope, extra = {}) => ({ id, name: 'demo', description: `A ${scope} skill`, enabled: true,
  shadowed: scope === 'global', source: { scope, root: scope === 'project' ? '/projects/1' : '/shared', directory: '.agent/skills/demo' }, diagnostics: [], ...extra });
const skillPackages = [makeSkill('project-demo', 'project'), makeSkill('shared-demo', 'global'),
  makeSkill('invalid-demo', 'project', { name: 'broken', enabled: false, diagnostics: [{ message: 'Invalid document', severity: 'error' }] })];
const fixtures = {
  '/dashboard-api/catalog': { backends: [{ name: 'local', title: 'Local', enabled: true }] },
  '/dashboard-api/agents/cards': { cards: [{ name: 'default', valid: true, display_name: 'Default', provider: 'test', model: 'demo' }] },
  '/dashboard-api/agents/providers': { provider_names: ['test'], default_provider: 'test', default_model: 'demo', model_catalogs: [] },
  '/dashboard-api/github': { repositories: [] },
  '/dashboard-api/workspace/default': { workspace: projects[0].workspace },
  '/dashboard-api/chat-history': { threads: [] },
};
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname.endsWith('/sessions/events')) {
    res.setHeader('Content-Type', 'text/event-stream');
    res.write('event: snapshot\ndata: {"sessions":[]}\n\n');
    return;
  }
  if (url.pathname.startsWith('/dashboard-api')) {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-CSRF-Token', 'projects-test');
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const body = raw ? JSON.parse(raw) : {};
    const send = data => res.end(JSON.stringify(data));
    const instructions = url.pathname.match(/^\/dashboard-api\/projects\/(\d+)\/instructions$/);
    const runtime = url.pathname.match(/^\/dashboard-api\/projects\/(\d+)\/runtime\/ensure$/);
    if (runtime) {
      if (delayRuntimeFailure) {
        delayRuntimeFailure = false;
        failPendingRuntime = () => { res.writeHead(410); send({ detail: 'Delayed workspace failure.' }); };
        return;
      }
      if (unavailable) { res.writeHead(410); send({ detail: 'Workspace is unavailable.' }); return; }
      const project = projects.find(item => item.id === Number(runtime[1]));
      send({ workspace: project.workspace }); return;
    }
    const checkpoint = url.pathname.match(/^\/dashboard-api\/projects\/(\d+)\/checkpoint$/);
    if (checkpoint) {
      const project = projects.find(item => item.id === Number(checkpoint[1]));
      project.persistence = { ...project.persistence, status: 'clean', last_error: '', blocked_for_writes: false };
      send({ status: 'clean', saved: true, revision: project.persistence.revision }); return;
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
      send({ projects: ordered(projects.filter(project => project.status === 'active')).map(project => ({ ...project, threads: [] })), has_more: false, next_cursor: null }); return;
    }
    if (url.pathname === '/dashboard-api/projects' && req.method === 'GET') {
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
} finally {
  if (call && ws?.readyState === WebSocket.OPEN) await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws?.close(); browser.kill(); server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  const resolved = path.resolve(profile);
  assert.equal(path.dirname(resolved), path.resolve(os.tmpdir()));
  assert.ok(path.basename(resolved).startsWith('k41-projects-browser-'));
  fs.rmSync(resolved, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
}
