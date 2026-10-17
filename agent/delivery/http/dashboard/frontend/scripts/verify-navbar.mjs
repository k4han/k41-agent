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
const binding = (key, label, execution) => ({ scope: { key, label, kind: key.split(':')[0], metadata: {} }, execution });
const threads = [
  ...Array.from({ length: 7 }, (_, index) => ({ thread_id: `local-${index}`,
    title: index === 0 ? 'Build a link shortener with Flask and a detailed project configuration' : `Flask conversation ${index}`,
    workspace: binding('local:/projects/flask-test', 'flask test', local) })),
  { thread_id: 'github-0', title: 'Cloudflare agent setup with Telegram integration', workspace: binding('github:42', 'cloudflare', github) },
  { thread_id: 'unassigned', title: 'General conversation', workspace: null },
].map(thread => ({ latest_checkpoint_id: '', checkpoint_count: 1, platform: 'dashboard',
  user_id: 'dashboard', channel_id: '', agent_name: 'default', kind: 'interactive', ...thread }));
const sends = [];
const fixtures = {
  '/dashboard-api/agents/cards': { cards: [{ name: 'default', display_name: 'Default', provider: 'test', model: 'demo-model', valid: true }] },
  '/dashboard-api/agents/providers': { provider_names: ['test'], default_provider: 'test', default_model: 'demo-model',
    model_catalogs: [{ provider: 'test', provider_type: 'openai', default_model: 'demo-model', models: [{ id: 'demo-model', label: 'Demo model', source: 'config' }] }] },
  '/dashboard-api/chat-history': { threads, has_more: false },
  '/dashboard-api/workspace/default': { workspace: null },
};
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/api/chat/events' && req.method === 'POST') {
    let body = '';
    for await (const chunk of req) body += chunk;
    sends.push(JSON.parse(body));
    res.setHeader('Content-Type', 'application/x-ndjson');
    res.end(JSON.stringify({ type: 'final', content: 'Done.' }) + '\n');
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
    const thread = threads.find(item => url.pathname === `/dashboard-api/chat-history/${item.thread_id}`);
    res.end(JSON.stringify(thread ? { ...thread, messages: [{ id: 'old-message', role: 'user', content: 'Existing conversation' }] }
      : fixtures[url.pathname] || { tools: [], agents: [], sessions: [], threads: [], packages: [],
        settings: {}, by_category: {}, backends: [], providers: [], entries: [], changes: [], current_version: 'test' }));
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
  await until(() => fs.existsSync(portFile), 'browser debugger');
  const port = Number(fs.readFileSync(portFile, 'utf8').split('\n')[0]);
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
  const width = () => js('Math.round(document.querySelector(".sidebar").getBoundingClientRect().width)');
  const ready = () => until(() => js('document.querySelectorAll(".nav-history-workspace").length === 3 && !!document.querySelector(".chat-prompt-input") && !document.querySelector(".chat-prompt-input").disabled'), 'chat navigation');
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
  await js(`window.dispatchEvent(new CustomEvent('k41:thread-start-running', { detail: { threadId: 'local-5' } }))`);
  assert.equal(await js(`document.querySelector('a[href="/c/local-5"]').closest('section').getAttribute('aria-label')`), 'flask test', 'running conversations remain under their project');
  assert.equal(await js(`document.querySelectorAll('a[href="/c/local-5"]').length`), 1);
  await js(`window.dispatchEvent(new CustomEvent('k41:thread-stop-running', { detail: { threadId: 'local-5' } }))`);
  console.log('PASS: project tree, active conversation, 14px text, hover action, pointer/keyboard resize, persistence, and viewport limits.');

  await js(`document.querySelector('.nav-project-add').click()`);
  await until(() => js(`location.pathname === '/chat' && !document.querySelector('.chat-prompt-input').disabled`), 'new local chat');
  assert.equal(await js(`document.querySelector('.transcript').textContent.includes('Existing conversation')`), false);
  await send(local);
  await draft('Discard this draft');
  await js(`document.querySelector('.nav-project-add').click()`);
  assert.equal(await js(`document.querySelector('.chat-prompt-input').value`), '', 'same-project action clears the previous draft');
  await js(`document.querySelectorAll('.nav-project-add')[1].click()`);
  await send(github);
  console.log('PASS: project actions start fresh conversations and preserve local paths, sandbox locators, and GitHub metadata.');

  await js(`document.querySelectorAll('.nav-project-view-all')[1].click()`);
  await until(() => js(`location.pathname === '/history' && document.querySelector('.select-control-trigger')?.textContent.includes('cloudflare')`), 'project-filtered history');
  assert.equal(await js(`document.querySelector('.content').textContent.includes('Flask conversation')`), false);
  await js(`document.querySelector('.nav-project-view-all').click()`);
  await until(() => js(`document.querySelector('.select-control-trigger')?.textContent.includes('flask test')`), 'switch history project');
  console.log('PASS: View all filters history and switches projects without a page reload.');

  await js(`document.querySelector('.brand-collapse-btn').click()`);
  await viewport(390, 844);
  await call('Emulation.setTouchEmulationEnabled', { enabled: true });
  await js(`document.querySelector('.topbar-menu-toggle').click()`);
  await until(() => js(`document.querySelector('.app-layout').classList.contains('app-layout--drawer-open') && document.querySelectorAll('.nav-history-workspace').length === 3`), 'mobile project drawer with saved desktop collapse');
  assert.equal(await js(`!!document.querySelector('.sidebar-resize-handle')`), false);
  await until(() => js(`getComputedStyle(document.querySelector('.nav-project-add')).opacity === '1'`), 'visible mobile project action');
  assert.equal(await js(`document.documentElement.scrollWidth <= innerWidth`), true);
  await js(`document.querySelector('.nav-project-add').click()`);
  await until(() => js(`location.pathname === '/chat' && !document.querySelector('.app-layout').classList.contains('app-layout--drawer-open')`), 'mobile project action closes drawer');
  await call('Page.reload');
  await until(() => js(`!!document.querySelector('.chat-header-menu-toggle')`), 'cold mobile chat');
  await js(`document.querySelector('.chat-header-menu-toggle').click()`);
  await until(() => js(`document.querySelectorAll('.nav-history-workspace').length === 3`), 'cold drawer loads project history with saved collapse');
  await js(`document.querySelector('.drawer-close-btn').click()`);
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: mobile projects remain accessible with collapsed desktop preferences and close after starting a chat.');
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
