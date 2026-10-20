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

const history = Object.fromEntries(['first', 'second'].map((id, index) => [id, {
  thread_id: id, title: `${id} conversation`, active_checkpoint_id: `${id}-checkpoint`,
  platform: 'api', user_id: 'dashboard', agent_name: 'default', provider: 'test',
  model: `model-${index ? 'b' : 'a'}`, workspace: null,
  messages: [{ id: `${id}-user`, role: 'user', content: `${id} original prompt` }],
}]));
history.legacy = {
  ...structuredClone(history.first), thread_id: 'legacy', title: 'Legacy conversation', model: '',
};
const fixtures = {
  '/dashboard-api/catalog': { backends: [] },
  '/dashboard-api/agents/cards': { cards: [{ name: 'default', display_name: 'Default', provider: 'test', model: 'model-a', valid: true }] },
  '/dashboard-api/agents/providers': {
    provider_names: ['test'], default_provider: 'test', default_model: 'model-b',
    model_catalogs: [{ provider: 'test', default_model: 'model-b', models: ['a', 'b', 'c'].map(id => ({
      id: `model-${id}`, label: `Model ${id.toUpperCase()}`,
      context_window: id === 'a' ? 64000 : 128000,
      reasoning_effort_levels: ['low', 'high'], reasoning_effort_default: id === 'b' ? 'high' : 'low',
    })) }],
  },
  '/dashboard-api/workspace/default': { workspace: null },
};
const responses = new Map();
const sentMessages = [];
const reconnects = [];
let delayedHistory;
let holdHistory = false;
let workspaceResolution;
let holdSessions = false;
const delayedSessions = [];
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/dashboard-api/sessions/events') {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write('event: snapshot\ndata: {"sessions":[]}\n\n');
    return;
  }
  if (url.pathname.startsWith('/api/chat/events')) {
    let body = '';
    for await (const chunk of req) body += chunk;
    const payload = JSON.parse(body);
    if (url.pathname.endsWith('/reconnect')) reconnects.push(payload);
    else sentMessages.push(payload);
    res.writeHead(200, { 'Content-Type': 'application/x-ndjson' });
    res.flushHeaders();
    responses.set(payload.thread_id || 'pending', res);
    if (payload.thread_id) res.write(JSON.stringify({ type: 'message', content: `${payload.thread_id} live response` }) + '\n');
    return;
  }
  if (url.pathname === '/dashboard-api/workspace/resolve') {
    workspaceResolution = res;
    res.setHeader('Content-Type', 'application/json');
    return;
  }
  if (url.pathname.startsWith('/dashboard-api/')) {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('X-CSRF-Token', 'switching-test-csrf');
    if (holdSessions && url.pathname === '/dashboard-api/sessions') {
      delayedSessions.push(res);
      return;
    }
    const thread = history[url.pathname.slice('/dashboard-api/chat-history/'.length)];
    if (url.pathname.startsWith('/dashboard-api/chat-history/') && thread) {
      if (holdHistory && thread.thread_id === 'first') {
        delayedHistory = { res, payload: structuredClone(thread) };
        return;
      }
      res.end(JSON.stringify(thread));
    } else if (url.pathname === '/dashboard-api/chat-history') {
      res.end(JSON.stringify({ threads: Object.values(history) }));
    } else {
      res.end(JSON.stringify(fixtures[url.pathname] || { sessions: [], threads: [], tools: [] }));
    }
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
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-chat-switching-'));
const browser = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-networking', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(callback, label, timeout = 15000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (await callback()) return;
    await delay(50);
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
  const navigate = async href => {
    await js(`(() => { const link = document.createElement('a'); link.href = ${JSON.stringify(href)}; document.body.append(link); link.click(); link.remove(); })()`);
    await until(() => js(`location.pathname === ${JSON.stringify(href)}`), `navigate to ${href}`);
  };
  const model = () => js('document.querySelector(".agent-model-badge-model")?.textContent');
  const effort = () => js('document.querySelector(".agent-model-badge-effort")?.textContent');
  const loaded = () => until(() => js('document.querySelector(".chat-prompt-input")?.disabled === false'), 'conversation loaded');
  const completed = () => until(() => js('!!document.querySelector(".chat-composer-send") && document.querySelector(".chat-prompt-input")?.disabled === false'), 'response completed');
  const send = async message => {
    const count = sentMessages.length;
    await js(`(() => {
      const input = document.querySelector('.chat-prompt-input'); input.value = ${JSON.stringify(message)};
      input.dispatchEvent(new Event('input', { bubbles: true }));
      document.querySelector('button[aria-label="Send message"]').click();
    })()`);
    await until(() => sentMessages.length === count + 1, 'message sent');
  };
  const selectModel = async id => {
    await js('document.querySelector(".model-picker-trigger").click()');
    await until(() => js('!!document.querySelector(".model-item-select")'), 'model menu');
    await js(`Array.from(document.querySelectorAll('.model-item-select')).find(el => el.textContent.includes(${JSON.stringify(`Model ${id.toUpperCase()}`)})).click()`);
  };
  const selectEffort = async label => {
    await js('document.querySelector(".model-picker-trigger").click()');
    await until(() => js('!!document.querySelector(".model-item-row.selected .model-item-effort-trigger")'), 'effort menu trigger');
    await js('document.querySelector(".model-item-row.selected .model-item-effort-trigger").click()');
    await until(() => js('!!document.querySelector(".model-effort-option")'), 'effort menu');
    await js(`Array.from(document.querySelectorAll('.model-effort-option')).find(el => el.textContent.trim() === ${JSON.stringify(label)}).click()`);
    await until(async () => await effort() === label, 'effort selected');
  };
  const finish = (id, content) => {
    history[id].messages.push({ id: `${id}-answer`, role: 'assistant', content });
    responses.get(id).end(JSON.stringify({ type: 'message', content: ` ${content}` }) + '\n');
    responses.delete(id);
  };

  await call('Page.enable');
  await call('Runtime.enable');
  await call('Page.navigate', { url: base + '/c/legacy' });
  await loaded();
  assert.equal(await model(), 'model-a', 'an omitted model inherits the agent model before provider defaults');
  assert.equal(await effort(), 'Low', 'effort defaults belong to the effective agent model');
  assert.equal(await js('document.querySelector(".cw-tokens-value").textContent.includes("/ 64K tokens")'), true, 'context capacity belongs to the effective agent model');
  await send('Continue without a model override');
  assert.equal(sentMessages.at(-1).model, undefined);
  assert.equal(sentMessages.at(-1).reasoning_effort, 'low');
  finish('legacy', 'legacy completed');
  await completed();

  await navigate('/c/first');
  await loaded();
  assert.equal(await model(), 'model-a');
  await selectModel('c');
  await navigate('/c/second');
  await loaded();
  assert.equal(await model(), 'model-b');
  await navigate('/c/first');
  await loaded();
  assert.equal(await model(), 'model-c', 'unsent model selection belongs to its conversation');
  await send('first new prompt');
  assert.equal(sentMessages.at(-1).model, 'model-c');
  await navigate('/settings/appearance');
  await navigate('/c/first');
  await loaded();
  assert.equal(await model(), 'model-c', 'running selection survives a page remount and stale history');
  await navigate('/c/second');
  await loaded();
  await send('second new prompt');
  assert.equal(sentMessages.at(-1).model, 'model-b');
  responses.get('first').write(JSON.stringify({ type: 'error', code: 'recursion_limit_reached', message: 'First conversation limit' }) + '\n');
  await delay(150);
  assert.equal(await model(), 'model-b');
  assert.equal(await js('document.querySelector(".chat-panel").textContent.includes("First conversation limit")'), false);
  assert.equal(await js(`!!document.querySelector('button[aria-label="Continue response"]')`), false);
  finish('first', 'first completed');
  await delay(150);
  assert.equal(await js(`!!document.querySelector('button[aria-label="Stop generating"]')`), true, 'finishing first cannot stop second');

  for (let index = 0; index < 8; index++) {
    await navigate('/c/first');
    await loaded();
    assert.equal(await model(), 'model-c');
    await navigate('/c/second');
    await loaded();
    assert.equal(await model(), 'model-b');
    assert.equal(await js('document.querySelector(".chat-panel").textContent.includes("second live response")'), true);
  }
  assert.equal(reconnects.length, 0, 'switching must reuse running streams');

  await navigate('/settings/appearance');
  await navigate('/c/second');
  await loaded();
  assert.equal(await model(), 'model-b');
  finish('second', 'second completed after remount');
  await until(() => js(`document.querySelector('.chat-panel')?.textContent.includes('second completed after remount') && !document.querySelector('button[aria-label="Stop generating"]')`), 'remounted stream completed');
  assert.equal(await js('document.querySelector(".chat-panel").textContent.includes("first live response")'), false);

  holdHistory = true;
  await navigate('/c/first');
  await until(() => Boolean(delayedHistory), 'delayed first history');
  await navigate('/c/second');
  await loaded();
  delayedHistory.res.end(JSON.stringify(delayedHistory.payload));
  holdHistory = false;
  await delay(150);
  assert.equal(await model(), 'model-b');
  assert.equal(await js('document.querySelector(".chat-header-title").textContent'), 'second conversation');

  holdSessions = true;
  await navigate('/c/first');
  await loaded();
  await until(() => delayedSessions.length > 0, 'delayed session snapshot');
  const session = { session_id: 'external-first', thread_id: 'first', agent_name: 'default', elapsed_display: '1s' };
  await js(`window.dispatchEvent(new CustomEvent('k41:session-started', { detail: ${JSON.stringify(session)} }))`);
  await until(() => reconnects.length === 1, 'external session reconnect');
  await js(`window.dispatchEvent(new CustomEvent('k41:session-stopped', { detail: ${JSON.stringify(session)} }))`);
  for (const response of delayedSessions.splice(0)) response.end(JSON.stringify({ sessions: [session] }));
  holdSessions = false;
  finish('first', 'external session completed');
  await until(() => js(`!document.querySelector('button[aria-label="Stop generating"]')`), 'session stays stopped after stale snapshot');
  await delay(150);
  assert.equal(reconnects.length, 1, 'a delayed session snapshot cannot restart a completed session');

  await navigate('/chat');
  await loaded();
  await js('document.querySelector(".composer-workspace-trigger").click()');
  await until(() => js('!!document.querySelector(".workspace-tab-btn")'), 'workspace picker');
  await js('Array.from(document.querySelectorAll(".workspace-tab-btn")).find(el => el.textContent.trim() === "Local Folder").click()');
  await js(`(() => {
    const input = document.querySelector('input[placeholder="Path to project directory..."]');
    input.value = '/original-project'; input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
  })()`);
  await js(`(() => {
    const input = document.querySelector('.chat-prompt-input'); input.value = 'Resolve original workspace';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector('button[aria-label="Send message"]').click();
  })()`);
  await until(() => Boolean(workspaceResolution), 'workspace resolution in progress');
  await navigate('/c/second');
  await loaded();
  workspaceResolution.end(JSON.stringify({ kind: 'local', workspace: {
    scope: { key: 'original', label: 'original', kind: 'local' },
    execution: { backend: 'local', locator: '/original-project', metadata: {} },
  } }));
  await until(() => responses.has('pending'), 'original message sent after navigation');
  assert.equal(sentMessages.at(-1).new_thread, true);
  assert.equal(sentMessages.at(-1).thread_id, undefined);
  assert.equal(sentMessages.at(-1).model, 'model-a');
  assert.equal(sentMessages.at(-1).workspace.locator, '/original-project');
  assert.equal(await model(), 'model-b');
  assert.equal(await js('document.querySelector(".composer-workspace-label").textContent'), 'Temporary');
  await navigate('/chat');
  await loaded();
  responses.get('pending').write(JSON.stringify({ type: 'thread_created', thread_id: 'created-in-background' }) + '\n');
  responses.get('pending').end(JSON.stringify({ type: 'final', content: 'background creation completed' }) + '\n');
  responses.delete('pending');
  await delay(200);
  assert.equal(await js('location.pathname'), '/chat', 'a background creation cannot navigate the new blank chat');
  assert.equal(await js('document.querySelector(".chat-panel").textContent.includes("background creation completed")'), false);

  for (const label of ['High', 'Auto']) {
    await navigate('/chat');
    await loaded();
    assert.equal(await effort(), 'Low', 'a different conversation resets the previous effort override');
    await selectEffort(label);
    const expectedEffort = label.toLowerCase();
    await send(`New conversation with ${expectedEffort} effort`);
    assert.equal(sentMessages.at(-1).reasoning_effort, expectedEffort);
    const threadId = `effort-${expectedEffort}`;
    history[threadId] = {
      ...structuredClone(history.first), thread_id: threadId, model: 'model-a',
    };
    const response = responses.get('pending');
    responses.delete('pending');
    responses.set(threadId, response);
    response.write(JSON.stringify({ type: 'thread_created', thread_id: threadId }) + '\n');
    await until(() => js(`location.pathname === ${JSON.stringify(`/c/${threadId}`)}`), 'new conversation received its ID');
    assert.equal(await effort(), label, 'assigning a thread ID preserves the explicit effort');
    finish(threadId, 'first response completed');
    await completed();
    assert.equal(await effort(), label, 'history refresh preserves the explicit effort');
    await send('Follow-up in the same conversation');
    assert.equal(sentMessages.at(-1).reasoning_effort, expectedEffort, 'follow-up requests retain the explicit effort');
    finish(threadId, 'follow-up completed');
    await completed();
  }
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: independent models, agent model defaults, concurrent streams, repeated switches, remount completion, delayed history and sessions, workspace request snapshots, background thread creation, and effort preservation across thread creation.');
} finally {
  for (const response of responses.values()) response.end();
  workspaceResolution?.end();
  delayedHistory?.res.end();
  for (const response of delayedSessions) response.end();
  if (call && ws?.readyState === WebSocket.OPEN) await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws?.close();
  browser.kill();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  const resolved = path.resolve(profile);
  assert.equal(path.dirname(resolved), path.resolve(os.tmpdir()));
  assert.ok(path.basename(resolved).startsWith('k41-chat-switching-'));
  fs.rmSync(resolved, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
}
