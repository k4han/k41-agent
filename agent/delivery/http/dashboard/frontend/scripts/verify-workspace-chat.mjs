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
assert.ok(fs.existsSync(path.join(staticRoot, 'index.html')), 'Run pnpm build before checking the production chat.');

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-workspace-chat-'));
const filePath = path.join(root, 'source.txt');
fs.writeFileSync(filePath, 'initial content');
const workspace = { backend: 'local', locator: root, metadata: {} };
const requests = [];
let chatResponse;
const fixtures = {
  '/dashboard-api/agents/cards': { cards: [{ name: 'default', display_name: 'Default', provider: 'test', model: 'demo-model', valid: true }] },
  '/dashboard-api/agents/providers': { provider_names: ['test'], default_provider: 'test', default_model: 'demo-model', model_catalogs: [{ provider: 'test', provider_type: 'openai', default_model: 'demo-model', models: [{ id: 'demo-model', label: 'Demo model', source: 'config' }] }] },
  '/dashboard-api/chat-history/workspace-chat': { thread_id: 'workspace-chat', messages: [], platform: 'api', user_id: 'dashboard', channel_id: '', agent_name: 'default', workspace: { execution: workspace } },
  '/dashboard-api/workspace/default': { workspace },
};
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/api/chat/events') {
    for await (const _chunk of req) { /* Consume the request before streaming. */ }
    chatResponse = res;
    res.setHeader('Content-Type', 'application/x-ndjson');
    res.setHeader('Cache-Control', 'no-cache');
    res.write(JSON.stringify({ type: 'tool_call', id: 'write-source', name: 'shell', args: { command: 'write source.txt' } }) + '\n');
    return;
  }
  if (url.pathname.startsWith('/dashboard-api/') || url.pathname === '/health') {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('X-CSRF-Token', 'workspace-test-csrf');
    const reply = value => res.end(JSON.stringify(value));
    if (fixtures[url.pathname]) return reply(fixtures[url.pathname]);
    if (url.pathname.startsWith('/dashboard-api/workspace/')) {
      requests.push({ kind: url.pathname.split('/').pop(), path: url.searchParams.get('path') });
      assert.equal(url.searchParams.get('locator'), root);
      if (url.pathname.endsWith('/tree')) return reply({ root, path: root, entries: fs.readdirSync(root).map(name => ({ name, path: path.join(root, name), kind: 'file', size: fs.statSync(path.join(root, name)).size, modified_at: 1 })), truncated: false });
      if (url.pathname.endsWith('/changes')) return reply({ root, is_git_repo: false, changes: [], message: 'Workspace is not a Git repository.' });
      if (url.pathname.endsWith('/file')) return reply({ root, path: filePath, content: fs.readFileSync(filePath, 'utf8'), mime_type: 'text/plain', size: fs.statSync(filePath).size, binary: false, truncated: false, message: '' });
    }
    return reply({ tools: [], agents: [], sessions: [], threads: [], packages: [], settings: {}, by_category: {}, backends: [], providers: [], current_version: 'test' });
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
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-workspace-chat-browser-'));
const browser = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-networking', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' });
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
  await call('Page.enable');
  await call('Runtime.enable');
  await call('Page.navigate', { url: base + '/c/workspace-chat' });
  await until(() => js('!!document.querySelector(".workspace-tree-row") && !!document.querySelector(".chat-prompt-input") && !document.querySelector(".chat-prompt-input").disabled'), 'production chat and local workspace');
  await js('document.querySelector(".workspace-tree-row").click();');
  await until(() => js('document.querySelector(".workspace-file-panel")?.textContent.includes("initial content")'), 'initial file preview');
  await until(() => js('!!document.querySelector(".workspace-file-view-shiki")'), 'initial code rendering');
  await js(`document.querySelector('button[aria-label="New tab"]').click();
    Array.from(document.querySelectorAll('.workspace-plus-menu-item')).find(item => item.textContent.includes('Git Changes')).click();
    Array.from(document.querySelectorAll('.workspace-tab')).find(tab => tab.title.endsWith('source.txt')).click();`);
  await until(() => js('!!document.querySelector(".workspace-file-view-shiki")'), 'cached code rendering');
  await js(`(() => {
    const input = document.querySelector('.chat-prompt-input');
    input.value = 'Update source.txt';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector('button[aria-label="Send message"]').click();
  })()`);
  await until(() => Boolean(chatResponse), 'chat stream starts');
  await js(`window.previewNode = document.querySelector('.workspace-file-view-shiki').firstChild;
    window.backgroundMutations = [];
    window.backgroundObserver = new MutationObserver(records => backgroundMutations.push(...records));
    backgroundObserver.observe(document.querySelector('.workspace-explorer'), {
      childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'disabled', 'title'],
    });`);
  const quietReads = requests.filter(request => request.kind === 'tree').length;
  await until(() => requests.filter(request => request.kind === 'tree').length >= quietReads + 2, 'two unchanged running polls', 25000);
  await delay(150);
  assert.equal(await js('backgroundMutations.length'), 0, 'unchanged running polls must not flash loading states or rebuild content');
  assert.equal(await js('document.querySelector(".workspace-file-view-shiki").firstChild === previewNode'), true, 'unchanged polling must preserve code DOM');
  await js('backgroundObserver.disconnect();');
  const readsBeforeWrite = requests.filter(request => request.kind === 'tree').length;
  fs.writeFileSync(filePath, 'written while tool is running');
  fs.writeFileSync(path.join(root, 'created.txt'), 'new file');
  await until(() => js('document.querySelector(".workspace-file-panel")?.textContent.includes("written while tool is running")'), 'live preview before tool_result', 18000);
  assert.equal(chatResponse.writableEnded, false, 'chat must still be streaming');
  assert.ok(requests.filter(request => request.kind === 'tree').length > readsBeforeWrite, 'running chat refreshes the tree');

  fs.writeFileSync(filePath, 'written at tool completion');
  const event = JSON.stringify({ type: 'tool_result', tool_call_id: 'write-source', name: 'shell', content: 'Command exited with code 0.' }) + '\n';
  chatResponse.write(event.slice(0, 20));
  chatResponse.write(event.slice(20));
  await until(() => js('document.querySelector(".workspace-file-panel")?.textContent.includes("written at tool completion")'), 'tool_result updates preview during the stream');
  await js('Array.from(document.querySelectorAll(".workspace-tab")).find(tab => tab.title === "Files Explorer").click();');
  assert.equal(await js('Array.from(document.querySelectorAll(".workspace-tree-row")).some(row => row.textContent.includes("created.txt"))'), true, 'newly written files appear during chat');
  chatResponse.end(JSON.stringify({ type: 'final', content: 'Done.' }) + '\n');
  await until(() => js(`!!document.querySelector('button[aria-label="Send message"]')`), 'chat completes');
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: production Chat refreshes real local files and previews while tools run and on streamed tool results.');
} finally {
  chatResponse?.end();
  if (call && ws?.readyState === WebSocket.OPEN) await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws?.close();
  browser.kill();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  for (const directory of [profile, root]) {
    const resolved = path.resolve(directory);
    assert.equal(path.dirname(resolved), path.resolve(os.tmpdir()));
    assert.ok(path.basename(resolved).startsWith('k41-workspace-chat-'));
    fs.rmSync(resolved, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
  }
}
