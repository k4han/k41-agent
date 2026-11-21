import { protocolFixture } from './protocol-fixture.mjs';
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
assert.ok(fs.existsSync(path.join(staticRoot, 'index.html')), 'Run pnpm build before checking navigation.');

const local = { backend: 'local', locator: '/projects/flask-test', label: 'flask test', metadata: {} };
const github = { backend: 'daytona', locator: 'cloudflare-sandbox', label: 'cloudflare', metadata: {
  source: 'github', repository_id: 42, repository_full_name: 'team/cloudflare', root: '/workspace', status: 'started',
} };
const temporary = { backend: 'local', locator: '/scratch/thread-old', label: 'No workspace', metadata: { temp: true, thread_id: 'old' } };
const binding = (key, label, execution) => ({ scope: { key, label, kind: key.split(':')[0], metadata: {} }, execution });
const threads = [
  ...Array.from({ length: 27 }, (_, index) => ({ thread_id: `local-${index}`,
    title: index === 0 ? 'Build a link shortener with Flask and a detailed project configuration' : `Flask conversation ${index}`,
    workspace: binding('local:/projects/flask-test', 'flask test', local) })),
  { thread_id: 'github-0', title: 'Cloudflare agent setup with Telegram integration', workspace: binding('github:42', 'cloudflare', github) },
  ...Array.from({ length: 6 }, (_, index) => ({ thread_id: `temp-${index}`, title: `Temporary chat ${index}`,
    workspace_key: 'no-workspace', workspace_label: 'No workspace', workspace: binding(`local:/scratch/thread-${index}`, 'No workspace', { ...temporary, locator: `/scratch/thread-${index}` }) })),
].map(thread => ({ latest_checkpoint_id: '', checkpoint_count: 1, platform: 'dashboard',
  user_id: 'dashboard', channel_id: '', agent_name: 'default', kind: 'interactive', ...thread }));
const byLatest = (left, right) => Date.parse(right.updated_at || '1970-01-01T00:00:00Z')
  - Date.parse(left.updated_at || '1970-01-01T00:00:00Z');
const sends = [];
const navRequests = [];
const projectThreadRequests = [];
let historyRequests = 0;
let failNav = false;
let failProjectThreads = false;
let showManagedProject = false;
let repositoryRequests = 0;
let schedulerRequests = 0;
const shortcutPath = '/dashboard-api/keyboard-shortcuts';
let rejectShortcutSave = false;
let shortcutSettings = {
  defaults: {
    'new-session': ['Mod+Alt+KeyO'],
    back: ['Mod+Shift+ArrowLeft'],
    forward: ['Mod+Shift+ArrowRight'],
    'copy-link': ['Alt+Shift+KeyL'],
    'command-palette': ['Mod+KeyK', 'Mod+Shift+Space'],
    'show-keyboard-shortcuts': ['Shift+Slash'],
    'search-sessions': ['Mod+Alt+KeyA'],
    'toggle-sidebar': ['Mod+Alt+KeyS'],
    'focus-message-input': ['Slash', 'Mod+Shift+Period'],
  },
  overrides: {},
  revision: 0,
};
const repositories = Array.from({ length: 8 }, (_, index) => ({
  repository_id: 8 - index,
  full_name: `team/repo-${8 - index}`,
}));
const scheduledJobs = [
  ...Array.from({ length: 7 }, (_, index) => ({
    id: String(7 - index), task: `Job ${7 - index}`, next_run_time: `2060-01-0${7 - index}T10:00:00Z`, paused: false,
  })),
  { id: 'paused', task: 'Paused job', next_run_time: '2060-01-01T00:00:00Z', paused: true },
  { id: 'invalid', task: 'Invalid date', next_run_time: 'invalid', paused: false },
];
const fixtures = {
  '/dashboard-api/agents/cards': { cards: [{ name: 'default', display_name: 'Default', provider: 'test', model: 'demo-model', valid: true }] },
  '/dashboard-api/agents/providers': { provider_names: ['test'], default_provider: 'test', default_model: 'demo-model',
    model_catalogs: [{ provider: 'test', provider_type: 'openai', default_model: 'demo-model', models: [{ id: 'demo-model', label: 'Demo model', source: 'config' }] }] },
  '/dashboard-api/chat-history': { threads, has_more: false },
  '/dashboard-api/scheduler': { jobs: scheduledJobs, identities: [], scheduler_timezone: 'UTC' },
  '/dashboard-api/system/version': { current_version: '0.1.2', latest_version: '0.1.3', has_update: true },
  '/dashboard-api/workspace/default': { workspace: null },
  '/scheduler/runs': { runs: [], total: 0 },
};
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/v2/api/chat/events' && req.method === 'POST') {
    let body = '';
    for await (const chunk of req) body += chunk;
    sends.push(JSON.parse(body));
    res.setHeader('Content-Type', 'application/x-ndjson');
    protocolFixture(res, sends.at(-1).thread_id);
    res.end(JSON.stringify({ type: 'final', content: 'Done.' }) + '\n');
    return;
  }
  if (url.pathname.startsWith('/v2/api/chat/') && url.pathname.endsWith('/async-tasks')) {
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ tasks: [], total: 0, active_count: 0 }));
    return;
  }
  if (url.pathname.endsWith('/sessions/events')) {
    res.setHeader('Content-Type', 'text/event-stream');
    res.write('event: snapshot\ndata: {"sessions":[]}\n\n');
    return;
  }
  if (url.pathname.startsWith('/dashboard-api') || url.pathname === '/scheduler/runs') {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('X-CSRF-Token', 'navbar-test-csrf');
    if (url.pathname === shortcutPath) {
      if (req.method === 'PUT') {
        let body = '';
        for await (const chunk of req) body += chunk;
        const { revision, overrides } = JSON.parse(body);
        assert.equal(req.headers['x-csrf-token'], 'navbar-test-csrf');
        if (rejectShortcutSave) {
          res.writeHead(503);
          res.end(JSON.stringify({ detail: 'Keyboard shortcut storage is unavailable.' }));
          return;
        }
        if (revision !== shortcutSettings.revision) {
          res.writeHead(409);
          res.end(JSON.stringify({ detail: 'Keyboard shortcuts changed in another tab or device. Try your edit again.' }));
          return;
        }
        shortcutSettings = { ...shortcutSettings, overrides, revision: revision + 1 };
      }
      res.end(JSON.stringify(shortcutSettings));
      return;
    }
    if (url.pathname === '/dashboard-api/nav-projects/threads') {
      projectThreadRequests.push(url);
      if (failProjectThreads) {
        res.writeHead(503);
        res.end(JSON.stringify({ detail: 'Project conversations are temporarily unavailable.' }));
        return;
      }
      const key = url.searchParams.get('project_key');
      const offset = Number(url.searchParams.get('offset'));
      const limit = Number(url.searchParams.get('limit'));
      const matching = threads.filter(thread => (thread.workspace_key || thread.workspace?.scope.key || 'no-workspace') === key).sort(byLatest);
      const rows = matching.slice(offset, offset + limit);
      res.end(JSON.stringify({ threads: rows, has_more: offset + rows.length < matching.length, next_offset: offset + rows.length }));
      return;
    }
    if (url.pathname === '/dashboard-api/nav-projects') {
      navRequests.push(url);
      if (failNav) {
        res.writeHead(503);
        res.end(JSON.stringify({ detail: 'Project navigation is temporarily unavailable.' }));
        return;
      }
      const groups = new Map();
      for (const thread of threads) {
        const key = thread.workspace_key || thread.workspace?.scope.key || 'no-workspace';
        if (!groups.has(key)) groups.set(key, { key, label: thread.workspace_label || thread.workspace?.scope.label || 'No workspace', threads: [] });
        groups.get(key).threads.push(thread);
      }
      const active = url.searchParams.get('active_thread_id');
      const projects = [...groups.values()].map(group => {
        group.threads.sort(byLatest);
        const selected = new Set([...group.threads].sort((left, right) => Number(right.thread_id === active) - Number(left.thread_id === active)).slice(0, 4));
        const project = showManagedProject && group.key === 'github:42' ? { id: 42 } : {};
        return { ...group, ...project, threads: group.threads.filter(thread => selected.has(thread)) };
      });
      res.end(JSON.stringify({ projects, has_more: false, next_cursor: null }));
      return;
    }
    if (url.pathname === '/dashboard-api/chat-history') historyRequests++;
    if (url.pathname === '/dashboard-api/scheduler') schedulerRequests++;
    if (url.pathname === '/dashboard-api/github') {
      repositoryRequests++;
      res.end(JSON.stringify({ repositories }));
      return;
    }
    const thread = threads.find(item => url.pathname === `/dashboard-api/chat-history/${item.thread_id}`);
    res.end(JSON.stringify(thread ? { ...thread, messages: [{ id: 'old-message', role: 'user', content: 'Existing conversation' }] }
      : fixtures[url.pathname] || { tools: [], agents: [], sessions: [], threads: [], packages: [],
        settings: {}, by_category: {}, backends: [], providers: [], entries: [], changes: [], tabs: [], images: [], open: false, current_version: 'test' }));
    return;
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
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-navbar-browser-'));
const browser = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-networking',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(callback, label, timeout = 15000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (await callback()) return;
    await delay(100);
  }
  throw new Error(`Timeout: ${label}`);
}

let ws, call;
try {
  const portFile = path.join(profile, 'DevToolsActivePort');
  let port;
  await until(() => {
    try {
      port = Number(fs.readFileSync(portFile, 'utf8').split('\n')[0]);
    } catch (error) {
      if (error.code !== 'ENOENT' && error.code !== 'EBUSY') throw error;
    }
    return port > 0;
  }, 'browser debugger');
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  ws = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
  await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }));
  let sequence = 0;
  const pending = new Map();
  const exceptions = [];
  call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const entry = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) entry?.reject(new Error(message.error.message)); else entry?.resolve(message.result);
    }
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails);
  });
  ws.addEventListener('close', () => {
    for (const entry of pending.values()) entry.reject(new Error('Browser closed'));
    pending.clear();
  });
  const js = async expression => {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const viewport = (width, height = 1000) => call('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width <= 980 });
  const width = () => js('Math.round(document.querySelector(".sidebar")?.getBoundingClientRect().width ?? 0)');
  const box = selector => js(`document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect().toJSON()`);
  const center = async selector => {
    const { x, y, width, height } = await box(selector);
    return { x: x + width / 2, y: y + height / 2 };
  };
  const move = point => call('Input.dispatchMouseEvent', { type: 'mouseMoved', ...point });
  const click = async selector => {
    const target = `document.querySelector(${JSON.stringify(selector)})`;
    await js(`${target}.scrollIntoView({ block: 'nearest' })`);
    const point = await center(selector);
    await move(point);
    assert.ok(await js(`${target}.contains(document.elementFromPoint(${point.x}, ${point.y}))`), `Clickable ${selector}`);
    await call('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', buttons: 1, clickCount: 1, ...point });
    await call('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...point });
  };
  const press = async (key, windowsVirtualKeyCode, options = {}) => {
    const details = { key, code: key, windowsVirtualKeyCode, ...options };
    const text = options.text ?? (key === 'Enter' ? '\r' : '');
    await call('Input.dispatchKeyEvent', { type: text ? 'keyDown' : 'rawKeyDown', text, ...details });
    await call('Input.dispatchKeyEvent', { type: 'keyUp', ...details });
  };
  const ready = () => until(() => js('document.querySelectorAll(".nav-history-workspace").length === 3 && !!document.querySelector(".chat-prompt-input") && !document.querySelector(".chat-prompt-input").disabled'), 'chat navigation');
  const newChat = '.app-shell-sidebar .nav-new-chat';
  const key = value => js(`document.querySelector('.sidebar-resize-handle').dispatchEvent(new KeyboardEvent('keydown', { key: ${JSON.stringify(value)}, bubbles: true }))`);
  const draft = text => js(`{ const input = document.querySelector('.chat-prompt-input'); input.value = ${JSON.stringify(text)}; input.dispatchEvent(new Event('input', { bubbles: true })); }`);
  const send = async workspace => {
    const count = sends.length;
    await draft('Start a new project conversation');
    await js(`document.querySelector('button[aria-label="Send message"]').click()`);
    await until(() => sends.length > count, 'new conversation request');
    assert.equal(sends.at(-1).new_thread, true);
    assert.equal(sends.at(-1).thread_id, undefined);
    assert.deepEqual(sends.at(-1).workspace, workspace);
    await until(() => js(`!!document.querySelector('button[aria-label="Send message"]')`), 'completed request');
  };
  await call('Page.enable');
  await call('Runtime.enable');
  await viewport(1600);
  await call('Page.navigate', { url: base + '/c/local-6' });
  await ready();
  assert.ok(navRequests.length > 0, 'navigation uses the project preview endpoint');
  assert.equal(historyRequests, 0, 'navigation must not load full conversation history');
  assert.deepEqual(await js(`Array.from(document.querySelectorAll('.nav-history-workspace')).map(group => group.querySelectorAll('.nav-history-item').length)`), [4, 1, 4]);
  assert.equal(await width(), 290);
  assert.deepEqual(await js(`['.nav-link', '.nav-history-workspace-label', '.nav-history-title', '.nav-project-view-all'].map(selector => getComputedStyle(document.querySelector(selector)).fontSize)`), ['14px', '14px', '14px', '14px']);
  assert.equal(await js(`document.querySelector('.nav-history-link[aria-current="page"]').closest('section').getAttribute('aria-label')`), 'flask test');
  assert.equal(await js(`document.querySelector('.nav-history-title').textContent`), threads[0].title);
  await js(`document.querySelector('.nav-history-workspace-toggle').click()`);
  assert.equal(await js(`document.querySelector('.nav-history-workspace-threads').getBoundingClientRect().height`), 0);
  await js(`document.querySelector('.nav-history-workspace-toggle').click()`);
  assert.equal(await js(`getComputedStyle(document.querySelector('.nav-project-add')).opacity`), '0');
  const heading = await js(`(() => { const r = document.querySelector('.nav-project-heading').getBoundingClientRect(); return { x: r.x + 40, y: r.y + r.height / 2 }; })()`);
  await call('Input.dispatchMouseEvent', { type: 'mouseMoved', ...heading });
  await until(() => js(`getComputedStyle(document.querySelector('.nav-project-add')).opacity === '1'`), 'project hover action');

  const handle = await js(`(() => { const r = document.querySelector('.sidebar-resize-handle').getBoundingClientRect(); return { x: r.x + r.width / 2, y: 100 }; })()`);
  assert.equal(await js(`document.elementFromPoint(${handle.x}, ${handle.y})?.className`), 'sidebar-resize-handle', 'resize handle is exposed above the chat');
  await call('Input.dispatchMouseEvent', { type: 'mouseMoved', ...handle });
  await call('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', buttons: 1, clickCount: 1, ...handle });
  assert.equal(await js('document.body.classList.contains("sidebar-resizing")'), true, 'pointer press starts resizing');
  await call('Input.dispatchMouseEvent', { type: 'mouseMoved', button: 'left', buttons: 1, x: 900, y: 100 });
  await until(async () => await width() === 400, 'pointer resize');
  assert.equal(await width(), 400, 'drag stops at one quarter of the viewport');
  await call('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, x: 900, y: 100 });
  assert.equal(await js('document.body.classList.contains("sidebar-resizing")'), false);
  await key('Home');
  await until(async () => await width() === 240, 'minimum width');
  await key('ArrowRight');
  await until(async () => await width() === 256, 'keyboard resize');
  await key('End');
  await until(async () => await width() === 400, 'maximum width');
  await call('Page.reload');
  await ready();
  assert.equal(await width(), 400, 'custom width survives reload');
  await viewport(1100);
  await until(async () => await width() === 275, 'viewport width clamp');
  await viewport(1600);
  await until(async () => await width() === 400, 'preferred width restored');
  await js(`document.querySelector('.sidebar-resize-handle').dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))`);
  await until(async () => await width() === 290, 'reset navigation width');
  await js(`window.firstThreadRow = document.querySelector('.nav-history-item-wrapper'); window.dispatchEvent(new CustomEvent('k41:thread-title-updated', { detail: { threadId: 'local-0', title: 'Updated conversation title' } }))`);
  assert.equal(await js(`document.querySelector('.nav-history-title').textContent`), 'Updated conversation title');
  assert.equal(await js(`firstThreadRow === document.querySelector('.nav-history-item-wrapper')`), true, 'title updates preserve thread rows');
  threads.find(thread => thread.thread_id === 'local-5').updated_at = new Date().toISOString();
  await js(`window.dispatchEvent(new CustomEvent('k41:thread-start-running', { detail: { threadId: 'local-5', workspace: ${JSON.stringify(local)} } }))`);
  assert.equal(await js(`document.querySelector('.nav-history-link').getAttribute('href')`), '/c/local-5', 'running conversations appear first');
  assert.equal(await js(`document.querySelector('.nav-history-link[href="/c/local-5"]').closest('section').getAttribute('aria-label')`), 'flask test', 'running conversations remain under their project');
  assert.equal(await js(`document.querySelectorAll('.nav-history-link[href="/c/local-5"]').length`), 1);
  assert.equal(await js(`document.querySelector('.nav-history-link[href="/c/local-5"]').closest('section').querySelectorAll('.nav-history-item').length`), 4, 'running and active conversations share the four-row limit');
  threads.find(thread => thread.thread_id === 'github-0').updated_at = new Date().toISOString();
  await js(`window.dispatchEvent(new CustomEvent('k41:thread-start-running', { detail: { threadId: 'github-0' } }))`);
  assert.equal(await js(`document.querySelector('.nav-history-workspace').getAttribute('aria-label')`), 'cloudflare', 'running projects use their latest activity to break ties');
  await js(`window.dispatchEvent(new CustomEvent('k41:thread-stop-running', { detail: { threadId: 'local-5' } }))`);
  assert.equal(await js(`document.querySelector('.nav-history-workspace').getAttribute('aria-label')`), 'cloudflare', 'projects with running conversations move above idle projects');
  assert.equal(await js(`document.querySelector('.nav-history-link').getAttribute('href')`), '/c/github-0');
  await js(`window.dispatchEvent(new CustomEvent('k41:thread-stop-running', { detail: { threadId: 'github-0' } }))`);
  assert.equal(await js(`document.querySelector('.nav-history-workspace').getAttribute('aria-label')`), 'cloudflare', 'completed projects keep their recent position');
  const completedNavRequests = navRequests.length;
  await js(`window.dispatchEvent(new CustomEvent('k41:threads-changed'))`);
  await until(() => navRequests.length > completedNavRequests, 'navigation refresh after completion');
  await until(() => js(`!document.querySelector('.nav-history-status')?.textContent.includes('Loading more')`), 'completed navigation refresh');
  assert.equal(await js(`document.querySelector('.nav-history-workspace').getAttribute('aria-label')`), 'cloudflare', 'completed projects stay first after the final history refresh');
  assert.equal(await js(`document.querySelector('.nav-history-workspace[aria-label="flask test"] .nav-history-link').getAttribute('href')`), '/c/local-5', 'completed conversations keep their recent position');
  await call('Page.reload');
  await ready();
  assert.equal(await js(`document.querySelector('.nav-history-workspace').getAttribute('aria-label')`), 'cloudflare', 'recent project order survives a page reload');
  assert.equal(await js(`document.querySelector('.nav-history-workspace[aria-label="flask test"] .nav-history-link').getAttribute('href')`), '/c/local-5');
  console.log('PASS: project tree, active conversation, 14px text, hover action, pointer/keyboard resize, persistence, and viewport limits.');

  failNav = true;
  await js(`window.dispatchEvent(new CustomEvent('k41:threads-changed'))`);
  await until(() => js(`document.querySelector('.nav-history-retry')?.textContent.trim() === 'Retry'`), 'navigation retry after API failure');
  assert.equal(await js(`document.querySelectorAll('.nav-history-workspace').length`), 3, 'failed refresh preserves visible projects');
  failNav = false;
  await js(`document.querySelector('.nav-history-retry').click()`);
  await until(() => js(`!document.querySelector('.nav-history-retry')`), 'navigation retry succeeds with existing rows');

  await js(`document.querySelector('.nav-project-add[aria-label="New chat in flask test"]').click()`);
  await until(() => js(`location.pathname === '/chat' && !document.querySelector('.chat-prompt-input').disabled`), 'new local chat');
  assert.equal(await js(`document.querySelector('.transcript').textContent.includes('Existing conversation')`), false);
  await send(local);
  await draft('Draft from the project workspace');
  await click(newChat);
  assert.equal(await js(`document.querySelector('.chat-prompt-input').value`), '', 'new chat opens a fresh draft on the same route');
  await send(undefined);
  await js(`document.querySelector('.nav-project-add[aria-label="New chat in flask test"]').click()`);
  await draft('Discard this draft');
  await js(`document.querySelector('.nav-project-add[aria-label="New chat in flask test"]').click()`);
  assert.equal(await js(`document.querySelector('.chat-prompt-input').value`), '', 'same-project action clears the previous draft');
  await js(`document.querySelector('.nav-project-add[aria-label="New chat in cloudflare"]').click()`);
  await send(github);
  await js(`document.querySelector('.nav-project-add[aria-label="New chat in No workspace"]').click()`);
  await send(undefined);
  console.log('PASS: project actions start fresh conversations and preserve local paths, sandbox locators, and GitHub metadata.');

  const localProject = '.nav-history-workspace[aria-label="flask test"]';
  const previousPath = await js('location.pathname');
  failProjectThreads = true;
  await js(`document.querySelector('${localProject} .nav-project-view-all').click()`);
  await until(() => js(`document.querySelector('${localProject} .nav-project-view-all')?.textContent.trim() === 'Retry'`), 'inline conversations retry');
  assert.equal(await js(`document.querySelectorAll('${localProject} .nav-history-item').length`), 4, 'failed loading retains previews');
  failProjectThreads = false;
  await js(`document.querySelector('${localProject} .nav-project-view-all').click()`);
  await until(() => js(`document.querySelectorAll('${localProject} .nav-history-item').length === 20`), 'first inline conversation page');
  assert.equal(await js(`document.querySelector('${localProject} .nav-project-view-all').textContent.trim()`), 'Load more');
  await js(`document.querySelector('${localProject} .nav-project-view-all').click()`);
  await until(() => js(`document.querySelectorAll('${localProject} .nav-history-item').length === 27`), 'remaining inline conversations');
  assert.equal(projectThreadRequests.at(-1).searchParams.get('offset'), '20');
  assert.equal(await js(`!!document.querySelector('${localProject} .nav-project-view-all')`), false, 'exhausted project hides load action');
  assert.equal(await js('location.pathname'), previousPath, 'inline loading preserves the current conversation');
  assert.equal(await js(`document.querySelectorAll('.nav-history-workspace[aria-label="No workspace"] .nav-history-item').length`), 4, 'other projects stay collapsed to previews');
  await js(`window.dispatchEvent(new CustomEvent('k41:thread-start-running', { detail: { threadId: 'local-26' } }))`);
  await js(`window.dispatchEvent(new CustomEvent('k41:thread-title-updated', { detail: { threadId: 'local-0', title: 'Updated idle conversation' } }))`);
  assert.equal(await js(`document.querySelector('${localProject} .nav-history-link').getAttribute('href')`), '/c/local-26', 'running conversations stay first in expanded projects after idle conversation updates');
  assert.equal(await js(`document.querySelectorAll('${localProject} .nav-history-item').length`), 27);
  await js(`window.dispatchEvent(new CustomEvent('k41:thread-stop-running', { detail: { threadId: 'local-26' } }))`);
  await js(`document.querySelector('.nav-history-all-btn').click()`);
  await until(() => js(`location.pathname === '/history' && !!document.querySelector('.select-control-trigger')`), 'global history remains accessible');
  console.log('PASS: View all loads conversations inline, paginates, retries failures, and preserves the current route.');

  const rail = '.app-shell-nav-rail';
  const projects = '#app-shell-projects';
  const footer = '.app-shell-sidebar .sidebar-footer-row';
  const toggle = '.app-shell-sidebar-pin';
  const navTitle = '.app-shell-navigation-title';
  const footerPin = `${footer} ${toggle}`;
  const searchButton = '.app-shell-sidebar .nav-search';
  const searchDialog = '.conversation-search-dialog';
  const searchInput = `${searchDialog} input[type="search"]`;
  const searchResults = `${searchDialog} .conversation-search-result`;
  const searchFor = value => js(`{ const input = document.querySelector('${searchInput}'); input.value = ${JSON.stringify(value)}; input.dispatchEvent(new Event('input', { bubbles: true })); }`);
  const railLink = href => `${rail} a[href="${href}"]`;
  const homeLink = railLink('/');
  const repoLink = railLink('/repositories');
  const schedulerLink = railLink('/scheduler');
  const manageProjects = '.nav-projects-manage';
  const outside = { x: 650, y: 280 };
  const projectsVisible = () => js(`document.querySelector('${projects}')?.hidden === false`);
  const railLabelsVisible = () => js(`getComputedStyle(document.querySelector('${homeLink} .nav-label')).display !== 'none'`);
  const savedPreference = () => js(`localStorage.getItem('k41-dashboard-sidebar')`);
  const footerDirection = () => js(`getComputedStyle(document.querySelector('${footer}')).flexDirection`);
  const currentNav = scope => js(`Array.from(document.querySelectorAll('${scope} a.nav-link:is(.active, [aria-current])'),
    item => [item.getAttribute('href'), item.classList.contains('active'), item.getAttribute('aria-current')])`);
  const navActions = scope => js(`Array.from(document.querySelectorAll('${scope} .nav-link'),
    item => item.getAttribute('aria-label'))`);
  const expectedActions = ['Home', 'New chat', 'Scheduler', 'Repositories', 'Search conversations'];
  assert.deepEqual(await navActions(rail), expectedActions);
  assert.deepEqual(await currentNav(rail), [['/', true, 'location']]);
  assert.equal(await railLabelsVisible(), true);
  assert.equal(await js(`document.querySelector('${footerPin}').getAttribute('aria-label')`), 'Unpin navigation panel');
  assert.equal((await box(projects)).width, 290, 'Projects spans the whole pinned sidebar');
  const projectsTop = (await box(projects)).top;
  assert.equal(projectsTop, (await box(rail)).bottom, 'Projects sits directly below the navigation rows');
  assert.equal(await footerDirection(), 'row');
  await click(newChat);
  await until(() => js(`location.pathname === '/chat' && !!document.querySelector('.chat-prompt-input')`), 'New chat opens a draft');
  await until(() => js(`document.querySelectorAll('${localProject} .nav-history-item').length === 27`), 'expanded conversations survive navigation');
  await click('.nav-history-all-btn');
  await until(() => js(`location.pathname === '/history' && !!document.querySelector('.select-control-trigger')`), 'Projects links back to history');
  const homeIconX = (await box(`${homeLink} svg`)).x;
  await click(toggle);
  await until(async () => await width() === 52, '52px collapsed navigation');
  assert.equal(await projectsVisible(), false, 'unpinning closes the temporary navigation');
  assert.equal(await railLabelsVisible(), false);
  assert.equal((await box(projects)).height, 0, 'Projects is hidden while the rail is closed');
  assert.equal(await footerDirection(), 'column');
  assert.equal(await js(`document.querySelector('${footerPin}').getAttribute('aria-label')`), 'Pin navigation panel');
  await until(() => js(`!!document.querySelector('${footerPin} .sidebar-pin-update-dot')`), 'update indicator on the footer pin');
  assert.equal((await box(`${homeLink} svg`)).x, homeIconX, 'primary icons keep their position');
  assert.equal(await savedPreference(), 'collapsed');
  const mainBox = await box('.main');
  await move(outside);
  await move({ x: 25, y: 400 });
  await until(projectsVisible, 'hovering the rail background opens the whole sidebar');
  assert.equal(await width(), 290, 'hover expands the whole sidebar');
  assert.equal((await box(projects)).width, 290);
  assert.equal((await box(projects)).top, projectsTop, 'hovered and pinned navigation use the same divider position');
  assert.equal(await footerDirection(), 'row');
  assert.equal(await railLabelsVisible(), true);
  assert.equal((await box(`${homeLink} svg`)).x, homeIconX);
  assert.deepEqual(await box('.main'), mainBox, 'opening the full sidebar leaves the page in place');
  await move({ x: 160, y: 400 });
  await delay(200);
  assert.equal(await projectsVisible(), true, 'the sidebar stays open when the pointer enters Projects');
  await move(outside);
  await until(async () => !(await projectsVisible()), 'leaving Projects closes the temporary sidebar');
  await move(await center(homeLink));
  await until(projectsVisible, 'hovering an icon opens the whole sidebar');
  assert.deepEqual(await box('.main'), mainBox, 'temporary navigation overlays the current page');
  assert.equal(await savedPreference(), 'collapsed');
  const requestsBeforeHover = [repositoryRequests, schedulerRequests];
  await move(await center(repoLink));
  await delay(200);
  await move(await center(schedulerLink));
  await delay(200);
  assert.deepEqual([repositoryRequests, schedulerRequests], requestsBeforeHover, 'hovering destination links does not fetch previews');
  assert.deepEqual(await currentNav(rail), [['/', true, 'location']], 'hovering links keeps the active destination');
  assert.equal(await js('location.pathname'), '/history');
  assert.equal((await box(projects)).top, projectsTop, 'Projects stays in place when moving between destinations');
  assert.deepEqual(await navActions(rail), expectedActions);

  await viewport(1600, 240);
  await until(() => js(`document.querySelector('${rail}').scrollHeight > document.querySelector('${rail}').clientHeight`), 'navigation scrolls on a short viewport');
  assert.ok((await box(projects)).height > 0, 'Projects remains visible below the scrollable navigation');
  await viewport(1600);

  const pinBounds = await js(`(() => {
    const sidebar = document.querySelector('.app-shell-sidebar');
    const before = sidebar.getBoundingClientRect().toJSON();
    document.querySelector('${footerPin}').click();
    return { before, after: sidebar.getBoundingClientRect().toJSON() };
  })()`);
  assert.deepEqual(pinBounds.after, pinBounds.before, 'pinning keeps the already open sidebar in place');
  await until(async () => (await box('.main')).left === pinBounds.before.right, 'the page reflows beside the pinned sidebar');
  assert.equal((await box('.main')).right, mainBox.right, 'the page fits in the remaining width');
  await click(footerPin);
  await move(outside);
  await until(async () => await width() === 52, 'unpinning restores the rail');
  await move(await center(homeLink));
  await until(projectsVisible, 'reopen temporary navigation before searching');

  const requestsBeforeSearch = historyRequests;
  await click(searchButton);
  await until(() => js(`document.activeElement.matches('${searchInput}')`), 'search focuses the query field');
  await until(() => js(`document.querySelectorAll('${searchResults}').length === 10`), 'recent conversations in search');
  assert.equal(historyRequests, requestsBeforeSearch + 1, 'opening search loads conversation summaries on demand');
  assert.equal(await projectsVisible(), false, 'search closes the temporary navigation');
  await searchFor('  FLASK CONVERSATION 18  ');
  assert.deepEqual(await js(`Array.from(document.querySelectorAll('${searchResults}'), result => result.getAttribute('href'))`), ['/c/local-18']);
  await searchFor('flask test');
  assert.equal(await js(`document.querySelectorAll('${searchResults}').length`), 27, 'search can match the project name');
  await searchFor('no conversation matches this');
  assert.equal(await js(`document.querySelector('${searchDialog} [role="status"]').textContent`), 'No matching conversations.');
  assert.equal(await js('location.pathname'), '/history', 'search keeps the current page while typing');
  await press('Escape', 27);
  await until(() => js(`!document.querySelector('${searchDialog}') && document.activeElement.matches('${searchButton}')`), 'Escape closes search and returns focus');

  showManagedProject = true;
  await move(outside);
  await move(await center(homeLink));
  await until(projectsVisible, 'temporary navigation with Projects');
  await js(`document.querySelector('.main').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))`);
  assert.equal(await projectsVisible(), false, 'an outside press closes temporary navigation');
  await move(outside);
  await move(await center(homeLink));
  await until(projectsVisible, 'open navigation for project actions');
  await js(`window.dispatchEvent(new CustomEvent('k41:threads-changed'))`);
  await until(() => js(`!!document.querySelector('.project-actions-trigger')`), 'managed project');
  assert.equal(await js(`document.querySelector('.nav-history-group').getBoundingClientRect().height > 0`), true);

  await click('.project-actions-trigger');
  await until(() => js(`!!document.querySelector('.project-menu')`), 'project actions in temporary navigation');
  await move(await center('.project-menu button'));
  await delay(200);
  assert.equal(await projectsVisible(), true, 'portaled project actions keep navigation open');
  await press('Escape', 27);
  assert.equal(await js(`!!document.querySelector('.project-menu')`), false);
  assert.equal(await projectsVisible(), true, 'the first Escape closes the project menu');

  await click('.nav-history-action');
  await until(() => js(`!!document.querySelector('.nav-history-menu--portal')`), 'thread actions in temporary navigation');
  await move(await center('.nav-history-menu--portal button'));
  await delay(200);
  assert.equal(await projectsVisible(), true, 'portaled thread actions keep navigation open');
  await click('.nav-history-menu--portal button');
  await until(() => js(`!!document.querySelector('.nav-history-rename-input')`), 'inline rename opens in temporary navigation');
  await move(outside);
  await delay(200);
  assert.equal(await projectsVisible(), true, 'moving away keeps an inline rename active');
  await press('Escape', 27);
  await until(async () => !(await projectsVisible()), 'navigation closes after canceling the rename outside it');

  await move(await center(homeLink));
  await until(projectsVisible, 'reopen navigation');
  await js(`document.querySelector('.nav-scroll').scrollTop = 70`);
  const scrollTop = await js(`document.querySelector('.nav-scroll').scrollTop`);
  assert.ok(scrollTop > 0, 'project history is scrollable');
  await move(outside);
  await until(async () => !(await projectsVisible()), 'temporary navigation closes after leaving it');
  await move(await center(homeLink));
  await until(projectsVisible, 'reopen scrolled navigation');
  assert.equal(await js(`document.querySelector('.nav-scroll').scrollTop`), scrollTop);

  await move(outside);
  await call('Page.reload');
  await until(async () => await width() === 52, 'collapsed preference survives reload');
  assert.equal(await projectsVisible(), false);
  const hasFocus = selector => js(`document.activeElement.matches(${JSON.stringify(selector)})`);
  for (let steps = 0; steps < 10 && !(await hasFocus(homeLink)); steps++) {
    await press('Tab', 9);
  }
  assert.equal(await hasFocus(homeLink), true, 'Tab reaches the Home icon');
  assert.equal(await projectsVisible(), true, 'keyboard focus opens the whole sidebar');
  assert.equal(await railLabelsVisible(), true);
  await press('ArrowRight', 39);
  assert.equal(await hasFocus(manageProjects), true, 'ArrowRight focuses Manage in Projects');
  await press('Escape', 27);
  assert.equal(await projectsVisible(), false);
  assert.equal(await hasFocus(homeLink), true, 'Escape restores focus to the initiating icon');
  await press('Tab', 9);
  assert.equal(await hasFocus(newChat), true, 'Tab moves to New chat after Escape');
  assert.equal(await projectsVisible(), true, 'the next focused item opens the same Projects list');
  await js(`document.querySelector('${footerPin}').focus()`);
  await press('Enter', 13);
  assert.equal(await savedPreference(), 'expanded', 'Enter activates the focused toggle');
  await until(async () => await width() === 290, 'keyboard pins the panel');
  assert.equal(await projectsVisible(), true, 'pinning keeps Projects open');
  assert.equal(await hasFocus(footerPin), true, 'pinning keeps focus on the footer button');
  await press('Enter', 13);
  await until(() => hasFocus(footerPin), 'unpinning keeps focus on the footer button');
  await press('Enter', 13);
  await until(() => hasFocus(footerPin), 'pinning keeps focus on the same button');
  await call('Page.reload');
  await until(async () => await width() === 290 && await projectsVisible(), 'pinned preference survives reload');
  await click(repoLink);
  await until(() => js(`location.pathname === '/repositories' && document.querySelector('${repoLink}').getAttribute('aria-current') === 'location'`), 'click navigates to Repositories');
  await until(() => repositoryRequests > 0, 'the Repositories route fetches its data');
  assert.equal(await projectsVisible(), true, 'the pinned Projects list stays visible on Repositories');
  await click(footerPin);
  await viewport(981, 800);
  await until(async () => await width() === 52, '52px desktop navigation at 981px');
  await call('Emulation.setTouchEmulationEnabled', { enabled: true });
  const tapToggle = async selector => {
    const touchToggle = await center(selector);
    await call('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [touchToggle] });
    await call('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  };
  await tapToggle(footerPin);
  await until(async () => await width() === 245 && await projectsVisible(), 'touch opens the pinned desktop navigation');
  await tapToggle(footerPin);
  await until(async () => await width() === 52, 'touch collapses the desktop panel');
  await viewport(980, 800);
  await until(() => js(`!document.querySelector('${rail}') && !!document.querySelector('.topbar-menu-toggle')`), 'drawer layout at 980px');
  console.log('PASS: 52px rail, overlay geometry, mouse and keyboard navigation, submenu actions, scroll position, desktop touch, and saved preferences.');

  await viewport(390, 844);
  const mobileNav = '.app-shell-sidebar';
  await js(`document.querySelector('.topbar-menu-toggle').click()`);
  await until(() => js(`document.querySelector('.app-layout--drawer-open ${navTitle}')?.textContent === 'Repositories'`), 'mobile title follows the active page');
  assert.equal(await js(`!!document.querySelector('.app-shell-navigation-heading .drawer-close-btn')`), true);
  assert.equal(await js(`!!document.querySelector('${toggle}')`), false);
  assert.deepEqual(await navActions(mobileNav), expectedActions, 'each primary action appears once in the mobile drawer');
  assert.deepEqual(await currentNav(mobileNav), [['/repositories', true, 'location']]);
  await js(`document.querySelector('${mobileNav} a.nav-link[href="/scheduler"]').click()`);
  await until(() => js(`location.pathname === '/scheduler' && document.querySelector('${navTitle}')?.textContent === 'Scheduler'
    && !document.querySelector('.app-layout--drawer-open')`), 'the Scheduler link opens the route and closes the drawer');
  await js(`document.querySelector('.topbar-menu-toggle').click()`);
  assert.deepEqual(await currentNav(mobileNav), [['/scheduler', true, 'location']]);
  await js(`document.querySelector('.nav-history-all-btn').click()`);
  await until(() => js(`location.pathname === '/history' && !!document.querySelector('.select-control-trigger')`), 'return to the history page');
  await js(`document.querySelector('.topbar-menu-toggle').click()`);
  await until(() => js(`document.querySelector('.app-layout').classList.contains('app-layout--drawer-open') && document.querySelectorAll('.nav-history-workspace').length === 3`), 'mobile project drawer with saved desktop collapse');
  assert.deepEqual(await currentNav(mobileNav), [['/', true, 'location']], 'history belongs to the Home section');
  assert.equal(await js(`document.querySelectorAll('.app-shell-sidebar a.nav-link:is([href="/chat"], [href="/history"])').length`), 0);
  assert.equal(await js(`!!document.querySelector('.sidebar-resize-handle')`), false);
  await until(() => js(`getComputedStyle(document.querySelector('.nav-project-add')).opacity === '1'`), 'visible mobile project action');
  assert.equal(await js(`document.documentElement.scrollWidth <= innerWidth`), true);
  await js(`document.querySelector('.nav-history-workspace[aria-label="No workspace"] .nav-project-view-all').click()`);
  await until(() => js(`document.querySelectorAll('.nav-history-workspace[aria-label="No workspace"] .nav-history-item').length === 6`), 'mobile inline conversations');
  assert.equal(await js(`document.querySelector('.app-layout').classList.contains('app-layout--drawer-open')`), true, 'inline loading keeps the mobile drawer open');
  assert.equal(await js('location.pathname'), '/history');
  await js(`document.querySelector('.nav-project-add[aria-label="New chat in flask test"]').click()`);
  await until(() => js(`location.pathname === '/chat' && !document.querySelector('.app-layout').classList.contains('app-layout--drawer-open')`), 'mobile project action closes drawer');
  await call('Page.reload');
  await until(() => js(`!!document.querySelector('.chat-header-menu-toggle')`), 'cold mobile chat');
  await js(`document.querySelector('.chat-header-menu-toggle').click()`);
  await until(() => js(`document.querySelectorAll('.nav-history-workspace').length === 3`), 'cold drawer loads project history with saved collapse');
  await js(`document.querySelector('.drawer-close-btn').click()`);
  await js(`document.querySelector('.chat-header-menu-toggle').click()`);
  await draft('Mobile draft');
  await js(`document.querySelector('${newChat}').click()`);
  await until(() => js(`!document.querySelector('.app-layout--drawer-open') && document.querySelector('.chat-prompt-input').value === ''`), 'new chat resets the draft and closes the mobile drawer');
  console.log('PASS: mobile projects remain accessible with collapsed desktop preferences and close after starting a chat.');

  await js(`document.querySelector('.chat-header-menu-toggle').click()`);
  await until(() => js(`!!document.querySelector('.app-layout--drawer-open ${searchButton}')`), 'search is available in the mobile drawer');
  await click(searchButton);
  await until(() => js(`document.querySelectorAll('${searchResults}').length === 10`), 'mobile search loads conversations');
  await searchFor('Flask conversation 18');
  await click(`${searchResults}[href="/c/local-18"]`);
  await until(() => js(`location.pathname === '/c/local-18' && !document.querySelector('${searchDialog}, .app-layout--drawer-open')
    && document.querySelector('.transcript')?.textContent.includes('Existing conversation')`), 'search loads the selected conversation and closes the mobile drawer');
  console.log('PASS: search opens from both navigation layouts, filters by title or project, and opens the selected conversation.');

  if (process.env.NAVBAR_SCREENSHOT) {
    await viewport(1600);
    await until(() => js(`!!document.querySelector('${footerPin}')`), 'desktop sidebar pin');
    await js(`document.querySelector('${footerPin}').click()`);
    await delay(250);
    const screenshot = await call('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(process.env.NAVBAR_SCREENSHOT, Buffer.from(screenshot.data, 'base64'));
  }

  await viewport(1440, 900);
  await call('Emulation.setTouchEmulationEnabled', { enabled: false });
  const alt = 1;
  const shift = 8;
  const mod = await js('/^Mac/i.test(navigator.platform)') ? 4 : 2;
  const primaryKey = mod === 4 ? 'Cmd' : 'Ctrl';
  const altKey = mod === 4 ? 'Option' : 'Alt';
  const chord = (key, code, modifiers, virtualKey) => press(key, virtualKey, { code, modifiers });
  const blur = () => js('document.activeElement.blur()');
  const paletteInput = '[aria-label="Search commands or pages"]';
  const modal = '[aria-modal="true"]';
  const launcher = '.settings-nav .shortcut-launcher';
  const shortcutsRoute = '/settings/keyboard-shortcuts';
  const settingsReady = () => until(() => js(`location.pathname === '${shortcutsRoute}'
    && document.querySelectorAll('.settings-page-body .shortcut-row').length === 9`), 'shortcut settings');
  const runPalette = async (query, alternate = false) => {
    await blur();
    if (alternate) await chord(' ', 'Space', mod | shift, 32);
    else await chord('k', 'KeyK', mod, 75);
    await until(() => js(`document.activeElement.matches('${paletteInput}')`), 'palette input focus');
    await js(`{ const input = document.activeElement; input.value = ${JSON.stringify(query)};
      input.dispatchEvent(new Event('input', { bubbles: true })); }`);
    await press('Enter', 13);
  };
  const captureFor = async (action) => {
    await click(`button[aria-label="Add shortcut for ${action}"]`);
    await until(() => js("document.activeElement.matches('.shortcut-recorder')"), 'shortcut recorder focus');
  };
  const saveShortcut = async () => {
    await click('.shortcut-editor-actions .btn-primary');
    await until(() => js(`!document.querySelector('${modal}')`), 'finish shortcut edit');
  };
  const copyBindings = () => js(`Array.from(document.querySelectorAll('[aria-label^="Edit Copy link:"]'), el => el.getAttribute('aria-label'))`);
  const expectTooltip = (label, binding) => until(() => js(`(() => {
    const tooltip = document.querySelector('.shortcut-tooltip');
    return tooltip && getComputedStyle(tooltip).visibility === 'visible'
      && tooltip.querySelector('span').textContent === ${JSON.stringify(label)}
      && (tooltip.querySelector('kbd')?.textContent ?? '') === ${JSON.stringify(binding)};
  })()`), `tooltip for ${label} with ${binding || 'no binding'}`);
  const hoverTooltip = async (selector, label, binding) => {
    await move(await center(selector));
    await expectTooltip(label, binding);
    assert.equal(await js(`(() => {
      const rect = document.querySelector('.shortcut-tooltip').getBoundingClientRect();
      return rect.left >= 0 && rect.right <= innerWidth && rect.top >= 0 && rect.bottom <= innerHeight;
    })()`), true, 'tooltip fits the viewport');
  };

  await until(() => js(`!!document.querySelector('${rail} .nav-search')`), 'desktop nav search');
  await hoverTooltip(searchButton, 'Search conversations', `${primaryKey}+${altKey}+A`);
  await move(await center(homeLink));
  await until(() => js(`document.querySelector('${newChat}')?.getBoundingClientRect().width > 0`), 'new chat control');
  await hoverTooltip(newChat, 'New chat', `${primaryKey}+${altKey}+O`);
  assert.equal(await js('location.pathname'), '/c/local-18', 'New chat stays available while viewing a conversation');
  await click(newChat);
  await until(() => js(`location.pathname === '/chat' && history.state.newChatId
    && document.querySelector('.chat-prompt-input')?.value === ''`), 'New chat opens a fresh draft from a conversation');

  await call('Page.navigate', { url: base + shortcutsRoute });
  await settingsReady();
  await hoverTooltip(launcher, 'Command palette', `${primaryKey}+K`);
  await move(outside);
  await until(() => js('!document.querySelector(".shortcut-tooltip")'), 'leaving a button hides its tooltip');
  await press('Tab', 9);
  await js(`document.querySelector('${launcher}').focus()`);
  await expectTooltip('Command palette', `${primaryKey}+K`);
  assert.equal(await js(`document.querySelector('${launcher}').getAttribute('aria-describedby')
    === document.querySelector('.shortcut-tooltip').id`), true);
  await press('Escape', 27);
  assert.equal(await js('!document.querySelector(".shortcut-tooltip")'), true);
  await blur();
  await press('/', 191, { code: 'Slash' });
  assert.equal(await js('document.activeElement.getAttribute("aria-label")'), 'Search settings');
  await click(launcher);
  await until(() => js(`document.activeElement.matches('${paletteInput}')`), 'mouse opens command palette');
  await press('ArrowDown', 40);
  assert.equal(await js('document.querySelector("[role=option][aria-selected=true]")?.textContent.trim().startsWith("Back")'), true);
  await press('Escape', 27);
  assert.equal(await js(`document.activeElement.matches('${launcher}')`), true);
  await runPalette('show keyboard shortcuts');
  await until(() => js("document.querySelectorAll('.dialog .shortcut-row').length === 9"), 'palette opens shortcut help');
  assert.equal(await js(`document.querySelectorAll('${modal}').length`), 1);
  await press('Escape', 27);
  await blur();
  await chord('?', 'Slash', shift, 191);
  await until(() => js("document.querySelectorAll('.dialog .shortcut-row').length === 9"), 'shortcut help default');
  await press('Escape', 27);

  const collapsed = () => js('document.querySelector(".settings-app-layout").classList.contains("sidebar-collapsed")');
  const wasCollapsed = await collapsed();
  await blur();
  await chord('s', 'KeyS', mod | alt, 83);
  assert.equal(await collapsed(), !wasCollapsed);
  await chord('s', 'KeyS', mod | alt, 83);
  assert.equal(await collapsed(), wasCollapsed);

  await captureFor('Copy link');
  await chord('k', 'KeyK', mod, 75);
  assert.match(await js('document.querySelector(".shortcut-conflict").textContent'), /Command palette/);
  assert.equal(await js('document.querySelector(".shortcut-editor-actions .btn-primary").disabled'), true);
  await chord('7', 'Digit7', mod | alt, 55);
  rejectShortcutSave = true;
  await click('.shortcut-editor-actions .btn-primary');
  await until(() => js('Array.from(document.querySelectorAll(".toast.error")).some(el => el.textContent.includes("storage is unavailable"))'), 'shortcut save error');
  assert.deepEqual(shortcutSettings.overrides, {}, 'a failed request keeps the saved shortcuts');
  assert.equal(await js('document.querySelector(".shortcut-recorder kbd:last-child").textContent'), '7');
  rejectShortcutSave = false;
  await saveShortcut();
  assert.deepEqual(shortcutSettings.overrides['copy-link'], ['Alt+Shift+KeyL', 'Mod+Alt+Digit7']);
  await call('Page.reload');
  await settingsReady();
  assert.equal((await copyBindings()).length, 2, 'both bindings survive a reload');
  await click('[aria-label^="Edit Copy link:"][aria-label$="+7"]');
  await until(() => js("document.activeElement.matches('.shortcut-recorder')"), 'edit shortcut focus');
  await chord('8', 'Digit8', mod | alt, 56);
  await saveShortcut();
  assert.deepEqual(shortcutSettings.overrides['copy-link'], ['Alt+Shift+KeyL', 'Mod+Alt+Digit8']);

  await js(`history.replaceState(history.state, '', '${shortcutsRoute}?filter=copy#link');
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText(value) {
      window.copiedLink = value; return Promise.resolve();
    } } });`);
  await blur();
  await chord('L', 'KeyL', alt | shift, 76);
  await until(() => js('window.copiedLink === location.href'), 'copy link default');
  await js('window.copiedLink = null');
  await chord('8', 'Digit8', mod | alt, 56);
  await until(() => js(`window.copiedLink === location.href && location.hash === '#link'`), 'copy current URL with query and hash');
  await click('[aria-label^="Remove Copy link:"][aria-label$="+8"]');
  await until(() => shortcutSettings.overrides['copy-link']?.length === 1, 'remove custom binding');
  await until(async () => (await copyBindings()).length === 1, 'update list after removal');
  await click('[aria-label^="Remove Copy link:"]');
  await until(async () => (await copyBindings()).length === 0, 'remove last binding');
  assert.deepEqual(shortcutSettings.overrides['copy-link'], []);
  await click('[aria-label="Reset Copy link to default"]');
  await until(async () => (await copyBindings()).length === 1, 'reset individual action');
  assert.equal(shortcutSettings.overrides['copy-link'], undefined);

  await captureFor('Copy link');
  shortcutSettings = { ...shortcutSettings, revision: shortcutSettings.revision + 1, overrides: { 'copy-link': [] } };
  await chord('8', 'Digit8', mod | alt, 56);
  await saveShortcut();
  assert.equal((await copyBindings()).length, 0, 'refresh after a concurrent save');
  await click('.settings-topbar-actions .btn');
  await until(async () => (await copyBindings()).length === 1, 'reset all shortcuts');
  assert.deepEqual(shortcutSettings.overrides, {});
  shortcutSettings = { ...shortcutSettings, revision: shortcutSettings.revision + 1, overrides: { 'copy-link': [] } };
  await js('window.dispatchEvent(new Event("focus"))');
  await until(async () => (await copyBindings()).length === 0, 'refresh preference on returning to the tab');

  const sidebarButton = (await collapsed()) ? '.brand-expand-btn' : '.brand-collapse-btn';
  const sidebarLabel = await js(`document.querySelector('${sidebarButton}').getAttribute('aria-label')`);
  await hoverTooltip(sidebarButton, sidebarLabel, `${primaryKey}+${altKey}+S`);
  for (const [bindings, text] of [
    [['Mod+Shift+KeyS'], `${primaryKey}+Shift+S`],
    [[], ''],
    [undefined, `${primaryKey}+${altKey}+S`],
  ]) {
    const overrides = { ...shortcutSettings.overrides };
    if (bindings) overrides['toggle-sidebar'] = bindings;
    else delete overrides['toggle-sidebar'];
    shortcutSettings = { ...shortcutSettings, overrides, revision: shortcutSettings.revision + 1 };
    await js('window.dispatchEvent(new Event("focus"))');
    await expectTooltip(sidebarLabel, text);
  }
  await move(outside);
  console.log('PASS: action tooltips show saved bindings, respond to keyboard focus, and update when bindings change.');

  await blur();
  await chord('a', 'KeyA', mod | alt, 65);
  await until(() => js(`document.activeElement.matches('${searchInput}')`), 'search sessions on settings route');
  await press('Escape', 27);
  await runPalette('new session', true);
  await until(() => js('location.pathname === "/chat" && document.querySelector(".chat-prompt-input")?.disabled === false'), 'palette opens a blank chat');
  await blur();
  const pressSlash = () => press('/', 191, { code: 'Slash', text: '/' });
  await pressSlash();
  await until(() => js('document.activeElement.matches(".chat-prompt-input")'), 'slash focuses the chat composer');
  assert.equal(await js('document.activeElement.value'), '', 'the focus shortcut does not type a slash');
  await pressSlash();
  assert.equal(await js('document.activeElement.value'), '/', 'slash can still be typed in the composer');
  const draftId = await js('history.state.newChatId');
  await draft('Keep this draft while typing');
  await js('document.querySelector(".chat-prompt-input").focus()');
  await chord('o', 'KeyO', mod | alt, 79);
  assert.equal(await js('history.state.newChatId'), draftId, 'shortcuts skip the focused composer');
  await blur();
  await js(`document.body.dispatchEvent(new KeyboardEvent('keydown', {
    bubbles: true, code: 'KeyO', key: 'o', ctrlKey: ${mod === 2}, metaKey: ${mod === 4}, altKey: true, isComposing: true,
  }));`);
  assert.equal(await js('history.state.newChatId'), draftId, 'shortcuts skip IME composition');
  await chord('o', 'KeyO', mod | alt, 79);
  await until(() => js(`location.pathname === '/chat' && history.state.newChatId !== ${JSON.stringify(draftId)}
    && document.querySelector('.chat-prompt-input')?.value === ''`), 'new session from /chat creates a new draft');
  await chord('>', 'Period', mod | shift, 190);
  await until(() => js('document.activeElement.matches(".chat-prompt-input")'), 'focus message input');
  const countBeforeEnter = sends.length;
  await draft('Send with Enter');
  await press('Enter', 13);
  await until(() => sends.length > countBeforeEnter, 'Enter still sends from the focused composer');

  await runPalette('chat history');
  await until(() => js('location.pathname === "/history"'), 'palette navigates to chat history');
  await blur();
  assert.equal(await js(`document.body.dispatchEvent(new KeyboardEvent('keydown', {
    bubbles: true, cancelable: true, code: 'Slash', key: '/',
  }))`), true, 'slash is not consumed outside chat');
  await chord('ArrowLeft', 'ArrowLeft', mod | shift, 37);
  await until(() => js('location.pathname.startsWith("/c/") || location.pathname === "/chat"'), 'history back shortcut');
  await chord('ArrowRight', 'ArrowRight', mod | shift, 39);
  await until(() => js('location.pathname === "/history"'), 'history forward shortcut');
  await viewport(375, 812);
  await until(() => js('!!document.querySelector(".topbar-menu-toggle")'), 'mobile dashboard shell');
  await chord('s', 'KeyS', mod | alt, 83);
  await until(() => js('!!document.querySelector(".app-layout--drawer-open")'), 'toggle mobile drawer from keyboard');
  await chord('s', 'KeyS', mod | alt, 83);
  await until(() => js('!document.querySelector(".app-layout--drawer-open")'), 'close mobile drawer from keyboard');
  await js('localStorage.setItem("k41-dashboard-theme", "dark")');
  await viewport(320, 568);
  await call('Page.addScriptToEvaluateOnNewDocument', {
    source: 'Object.defineProperty(navigator, "platform", { value: "MacIntel" });',
  });
  await call('Page.navigate', { url: base + shortcutsRoute });
  await settingsReady();
  assert.equal(await js('document.documentElement.classList.contains("dark")'), true);
  assert.equal(await js('document.querySelector(\'[aria-label^="Edit Command palette:"] kbd\').textContent'), 'Cmd');
  assert.equal(await js('document.documentElement.scrollWidth <= innerWidth'), true, 'shortcut settings fit a small viewport');
  await captureFor('Copy link');
  assert.equal(await js('document.querySelector(".dialog").getBoundingClientRect().right <= innerWidth'), true);
  await press('Escape', 27);
  await blur();
  await chord('k', 'KeyK', 4, 75);
  await until(() => js(`document.activeElement.matches('${paletteInput}')`), 'Command key opens the palette on Mac');
  await press('Escape', 27);
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: account-synced shortcut settings, conflicts, command palette, all keyboard actions, and mobile dialogs.');
} finally {
  if (call && ws?.readyState === WebSocket.OPEN) await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws?.close();
  browser.kill();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  const resolved = path.resolve(profile);
  assert.equal(path.dirname(resolved), path.resolve(os.tmpdir()));
  assert.ok(path.basename(resolved).startsWith('k41-navbar-browser-'));
  fs.rmSync(resolved, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
}
