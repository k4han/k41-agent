import assert from 'node:assert/strict';
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
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' });
let projects = [makeProject(1, 'Empty project'), makeProject(2, 'Other project')];
const mutations = [];
let unavailable = false;
let failCatalog = false;
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
    res.setHeader('X-CSRF-Token', 'projects-test');
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const body = raw ? JSON.parse(raw) : {};
    const send = data => res.end(JSON.stringify(data));
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
  assert.equal(await js(`document.querySelector('.project-card a').getAttribute('href')`), '/history?workspace=local%3A%2Fprojects%2F1');
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
} finally {
  if (call && ws?.readyState === WebSocket.OPEN) await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws?.close(); browser.kill(); server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  const resolved = path.resolve(profile);
  assert.equal(path.dirname(resolved), path.resolve(os.tmpdir()));
  assert.ok(path.basename(resolved).startsWith('k41-projects-browser-'));
  fs.rmSync(resolved, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
}
