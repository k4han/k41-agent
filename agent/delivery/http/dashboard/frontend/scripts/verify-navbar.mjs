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
let failRepositories = true;
let releaseRepositoryPreview;
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
  '/dashboard-api/scheduler': { jobs: scheduledJobs },
  '/dashboard-api/workspace/default': { workspace: null },
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
  if (url.pathname.startsWith('/dashboard-api')) {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('X-CSRF-Token', 'navbar-test-csrf');
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
    if (url.pathname === '/dashboard-api/github') {
      if (!releaseRepositoryPreview) await new Promise(resolve => { releaseRepositoryPreview = resolve; });
      if (failRepositories) {
        res.writeHead(503);
        res.end(JSON.stringify({ detail: 'Repositories temporarily unavailable' }));
      } else {
        res.end(JSON.stringify({ repositories }));
      }
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
  const press = async (key, windowsVirtualKeyCode) => {
    const details = { key, code: key, windowsVirtualKeyCode };
    const text = key === 'Enter' ? '\r' : '';
    await call('Input.dispatchKeyEvent', { type: text ? 'keyDown' : 'rawKeyDown', text, ...details });
    await call('Input.dispatchKeyEvent', { type: 'keyUp', ...details });
  };
  const ready = () => until(() => js('document.querySelectorAll(".nav-history-workspace").length === 3 && !!document.querySelector(".chat-prompt-input") && !document.querySelector(".chat-prompt-input").disabled'), 'chat navigation');
  const panel = '.app-shell-navigation-panel';
  const openHome = `${panel} a[href="/"]`;
  const newChat = `${panel} .nav-new-chat`;
  const assertHomeActions = async () => {
    assert.deepEqual(await js(`Array.from(document.querySelectorAll('${panel} nav .nav-link'), item => item.textContent.trim())`),
      ['Open Home', 'New chat'], 'New chat follows Open Home in the Home panel');
  };
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
  await assertHomeActions();
  assert.ok((await box(newChat)).top >= (await box(openHome)).bottom, 'New chat appears directly below Open Home');
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
  const toggle = `${rail} button:first-child`;
  const searchButton = '.app-shell-sidebar .nav-search';
  const searchDialog = '.conversation-search-dialog';
  const searchInput = `${searchDialog} input[type="search"]`;
  const searchResults = `${searchDialog} .conversation-search-result`;
  const searchFor = value => js(`{ const input = document.querySelector('${searchInput}'); input.value = ${JSON.stringify(value)}; input.dispatchEvent(new Event('input', { bubbles: true })); }`);
  const railLink = href => `${rail} a[href="${href}"]`;
  const homeLink = railLink('/');
  const repoLink = railLink('/repositories');
  const previewRows = '.navigation-section-list > li';
  const outside = { x: 650, y: 280 };
  const panelVisible = () => js(`document.querySelector('${panel}')?.hidden === false`);
  const savedPreference = () => js(`localStorage.getItem('k41-dashboard-sidebar')`);
  const footerDirection = () => js(`getComputedStyle(document.querySelector('${footer}')).flexDirection`);
  const focusedInPanel = () => js(`document.querySelector('${panel}')?.contains(document.activeElement) ?? false`);
  const sectionIs = section => js(`document.querySelector('${panel}')?.dataset.navigationSection === '${section}'`);
  await until(() => sectionIs('home'), 'the pinned panel uses Home shortcuts on the history page');
  assert.deepEqual(await js(`Array.from(document.querySelectorAll('${rail} a'), item => item.getAttribute('href'))`),
    ['/', '/repositories', '/scheduler']);
  await assertHomeActions();
  assert.equal((await box(projects)).width, 290, 'Projects spans the whole pinned sidebar');
  assert.equal((await box(projects)).top, (await box(panel)).bottom, 'Projects sits below the shortcut area');
  const projectsGap = (await box(projects)).top - (await box(searchButton)).bottom;
  assert.ok(projectsGap >= 0 && projectsGap <= 12, 'Projects starts just below the search icon');
  assert.equal(await footerDirection(), 'row');
  await move(await center(repoLink));
  assert.equal(await sectionIs('home'), true, 'hovering another item leaves the pinned section on the current route');
  await click(newChat);
  await until(() => js(`location.pathname === '/chat' && !!document.querySelector('.chat-prompt-input')`), 'the Home panel starts a new chat');
  await until(() => js(`document.querySelectorAll('${localProject} .nav-history-item').length === 27`), 'expanded conversations survive navigation');
  assert.equal((await box(rail)).width, 52, 'pinned navigation has a 52px rail');
  await click('.nav-history-all-btn');
  await until(() => js(`location.pathname === '/history' && !!document.querySelector('.select-control-trigger')`), 'Projects links back to history');
  const homeLinkX = (await box(homeLink)).x;
  await click(toggle);
  await until(async () => await width() === 52, '52px collapsed navigation');
  assert.equal(await panelVisible(), false, 'unpinning closes the panel while the pointer is still on the rail');
  assert.equal((await box(projects)).height, 0, 'Projects is hidden while the rail is closed');
  assert.equal(await footerDirection(), 'column');
  assert.equal((await box(homeLink)).x, homeLinkX, 'primary icons keep their position');
  assert.equal(await savedPreference(), 'collapsed');
  const mainBox = await box('.main');
  await move(outside);
  await move({ x: 25, y: 400 });
  await until(() => sectionIs('home'), 'hovering the rail background opens Home shortcuts on the history page');
  assert.equal(await width(), 290, 'hover expands the whole sidebar');
  assert.equal((await box(projects)).width, 290);
  assert.equal((await box(projects)).top - (await box(searchButton)).bottom, projectsGap, 'hover uses the same compact header as pinned navigation');
  assert.equal(await footerDirection(), 'row');
  assert.deepEqual(await box('.main'), mainBox, 'opening the full sidebar leaves the page in place');
  await move({ x: 160, y: 400 });
  await delay(200);
  assert.equal(await panelVisible(), true, 'the sidebar stays open when the pointer enters Projects');
  await move(outside);
  await until(async () => !(await panelVisible()), 'leaving Projects closes the temporary sidebar');
  await move(await center(homeLink));
  await until(() => sectionIs('home'), 'home preview from the rail');
  await assertHomeActions();
  assert.equal((await box(panel)).x, 52);
  assert.equal((await box(panel)).width, 238);
  assert.deepEqual(await box('.main'), mainBox, 'hover preview overlays the current page');
  assert.equal(await savedPreference(), 'collapsed');
  await move({ x: 54, y: 75 });
  await move({ x: 160, y: 100 });
  await delay(200);
  assert.equal(await panelVisible(), true, 'the panel stays open when the pointer crosses into it');

  const requestsBeforeSearch = historyRequests;
  await click(searchButton);
  await until(() => js(`document.activeElement.matches('${searchInput}')`), 'search focuses the query field');
  await until(() => js(`document.querySelectorAll('${searchResults}').length === 10`), 'recent conversations in search');
  assert.equal(historyRequests, requestsBeforeSearch + 1, 'opening search loads conversation summaries on demand');
  assert.equal(await panelVisible(), false, 'search closes the hover panel');
  await searchFor('  FLASK CONVERSATION 18  ');
  assert.deepEqual(await js(`Array.from(document.querySelectorAll('${searchResults}'), result => result.getAttribute('href'))`), ['/c/local-18']);
  await searchFor('flask test');
  assert.equal(await js(`document.querySelectorAll('${searchResults}').length`), 27, 'search can match the project name');
  await searchFor('no conversation matches this');
  assert.equal(await js(`document.querySelector('${searchDialog} [role="status"]').textContent`), 'No matching conversations.');
  assert.equal(await js('location.pathname'), '/history', 'search keeps the current page while typing');
  await press('Escape', 27);
  await until(() => js(`!document.querySelector('${searchDialog}') && document.activeElement.matches('${searchButton}')`), 'Escape closes search and returns focus');

  await move(await center(repoLink));
  await until(() => sectionIs('repositories'), 'repository preview from its own icon');
  await until(() => Boolean(releaseRepositoryPreview), 'repository request starts on hover');
  assert.ok(await js(`document.querySelector('.navigation-section-panel [role="status"]').textContent.includes('Loading')`));
  await move(await center(railLink('/scheduler')));
  await until(() => sectionIs('scheduler'), 'scheduler preview while the repository request is still pending');
  releaseRepositoryPreview();
  await until(() => js(`document.querySelectorAll('${previewRows} .navigation-section-detail').length === 5`), 'five upcoming jobs');
  assert.deepEqual(await js(`Array.from(document.querySelectorAll('${previewRows} .nav-label'), item => item.textContent)`),
    ['Job 1', 'Job 2', 'Job 3', 'Job 4', 'Job 5']);
  assert.equal(await sectionIs('scheduler'), true, 'the previous request cannot replace the current panel');

  await move(await center(repoLink));
  await until(() => js(`!!document.querySelector('${panel} .nav-history-retry')`), 'repository error and retry');
  failRepositories = false;
  await click(`${panel} .nav-history-retry`);
  await until(() => js(`document.querySelectorAll('${previewRows} a').length === 6`), 'retry loads six repository links');
  assert.deepEqual(await js(`Array.from(document.querySelectorAll('${previewRows} a'), item => item.getAttribute('href'))`),
    [1, 2, 3, 4, 5, 6].map(id => `/repositories/${id}`));
  assert.equal(await js('location.pathname'), '/history', 'hovering sections leaves the current route intact');

  showManagedProject = true;
  await move(await center(homeLink));
  await until(() => sectionIs('home'), 'Home preview with Projects');
  await js(`window.dispatchEvent(new CustomEvent('k41:threads-changed'))`);
  await until(() => js(`!!document.querySelector('.project-actions-trigger')`), 'managed project');
  assert.equal(await js(`document.querySelector('.nav-history-group').getBoundingClientRect().height > 0`), true);

  await click('.project-actions-trigger');
  await until(() => js(`!!document.querySelector('.project-menu')`), 'project actions in the preview');
  await move(await center('.project-menu button'));
  await delay(200);
  assert.equal(await panelVisible(), true, 'portaled project actions keep their preview open');
  await press('Escape', 27);
  assert.equal(await js(`!!document.querySelector('.project-menu')`), false);
  assert.equal(await panelVisible(), true, 'the first Escape closes the project menu');

  await click('.nav-history-action');
  await until(() => js(`!!document.querySelector('.nav-history-menu--portal')`), 'thread actions in the preview');
  await move(await center('.nav-history-menu--portal button'));
  await delay(200);
  assert.equal(await panelVisible(), true, 'portaled thread actions keep their preview open');
  await click('.nav-history-menu--portal button');
  await until(() => js(`!!document.querySelector('.nav-history-rename-input')`), 'inline rename opens from the preview');
  await move(outside);
  await delay(200);
  assert.equal(await panelVisible(), true, 'moving away keeps an inline rename active');
  await press('Escape', 27);
  await until(async () => !(await panelVisible()), 'preview closes after canceling the rename outside the panel');

  await move(await center(homeLink));
  await until(panelVisible, 'reopen preview');
  await js(`document.querySelector('.nav-scroll').scrollTop = 70`);
  const scrollTop = await js(`document.querySelector('.nav-scroll').scrollTop`);
  assert.ok(scrollTop > 0, 'project history is scrollable');
  await move(outside);
  await until(async () => !(await panelVisible()), 'preview closes after leaving navigation');
  await move(await center(homeLink));
  await until(panelVisible, 'reopen scrolled preview');
  assert.equal(await js(`document.querySelector('.nav-scroll').scrollTop`), scrollTop);

  await move(outside);
  await call('Page.reload');
  await until(async () => await width() === 52, 'collapsed preference survives reload');
  assert.equal(await panelVisible(), false);
  const hasFocus = selector => js(`document.activeElement.matches(${JSON.stringify(selector)})`);
  for (let steps = 0; steps < 10 && !(await hasFocus(homeLink)); steps++) {
    await press('Tab', 9);
  }
  assert.equal(await hasFocus(homeLink), true, 'Tab reaches the Home icon');
  assert.equal(await sectionIs('home'), true, 'keyboard focus opens the matching preview');
  await press('ArrowRight', 39);
  assert.equal(await focusedInPanel(), true, 'ArrowRight places focus in the matching panel');
  await press('Tab', 9);
  assert.equal(await hasFocus(newChat), true, 'New chat follows Open Home in keyboard order');
  await press('Escape', 27);
  assert.equal(await panelVisible(), false);
  assert.equal(await hasFocus(homeLink), true, 'Escape restores focus to the initiating icon');
  await press('Tab', 9);
  assert.equal(await hasFocus(repoLink), true, 'Tab moves to the next icon after Escape');
  assert.equal(await sectionIs('repositories'), true, 'the next focused icon opens its own preview');
  await js(`document.querySelector('${toggle}').focus()`);
  await press('Enter', 13);
  assert.equal(await savedPreference(), 'expanded', 'Enter activates the focused toggle');
  await until(async () => await width() === 290, 'keyboard pins the panel');
  assert.equal(await sectionIs('home'), true, 'pinning opens Home shortcuts on the history page');
  await call('Page.reload');
  await until(async () => await width() === 290 && await panelVisible(), 'pinned preference survives reload');
  await click(toggle);
  await viewport(981, 800);
  await until(async () => await width() === 52, '52px desktop navigation at 981px');
  await call('Emulation.setTouchEmulationEnabled', { enabled: true });
  const touchToggle = await center(toggle);
  const tapToggle = async () => {
    await call('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [touchToggle] });
    await call('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  };
  await tapToggle();
  await until(async () => await width() > 52 && await panelVisible(), 'touch opens the pinned desktop panel');
  await tapToggle();
  await until(async () => await width() === 52, 'touch collapses the desktop panel');
  await viewport(980, 800);
  await until(() => js(`!document.querySelector('${rail}') && !!document.querySelector('.topbar-menu-toggle')`), 'drawer layout at 980px');
  console.log('PASS: 52px rail, overlay geometry, mouse and keyboard preview, submenu actions, scroll position, desktop touch, and saved preferences.');

  await viewport(390, 844);
  await js(`document.querySelector('.topbar-menu-toggle').click()`);
  await until(() => js(`document.querySelector('.app-layout').classList.contains('app-layout--drawer-open') && document.querySelectorAll('.nav-history-workspace').length === 3`), 'mobile project drawer with saved desktop collapse');
  await assertHomeActions();
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
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: search opens from both navigation layouts, filters by title or project, and opens the selected conversation.');

  if (process.env.NAVBAR_SCREENSHOT) {
    await viewport(1600);
    await until(() => js(`!!document.querySelector('.brand-expand-btn')`), 'desktop sidebar toggle');
    await js(`document.querySelector('.brand-expand-btn').click()`);
    await delay(250);
    const screenshot = await call('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(process.env.NAVBAR_SCREENSHOT, Buffer.from(screenshot.data, 'base64'));
  }
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
