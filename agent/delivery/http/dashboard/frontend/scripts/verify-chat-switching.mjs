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
  '/dashboard-api/agents/cards': { cards: [
    { name: 'default', display_name: 'Default', provider: 'test', model: 'model-a', valid: true },
    { name: 'alternate', display_name: 'Alternate', provider: 'test', model: 'model-c', valid: true },
    { name: 'custom', display_name: 'Custom', provider: 'test', model: 'configured-custom-model', valid: true },
    { name: 'unavailable', display_name: 'Unavailable', provider: 'removed', model: 'removed-model', valid: true },
  ] },
  '/dashboard-api/agents/providers': {
    provider_names: ['test'], default_provider: 'test', default_model: 'model-b',
    model_catalogs: [{ provider: 'test', default_model: 'model-b', models: ['a', 'b', 'c'].map(id => ({
      id: `model-${id}`, label: `Model ${id.toUpperCase()}`,
      context_window: id === 'a' ? 64000 : 128000,
      reasoning_effort_levels: ['low', 'high'], reasoning_effort_default: id === 'b' ? 'high' : 'low',
    })) },
    { provider: 'backup', default_model: 'model-a', models: [{ id: 'model-a', label: 'Backup model A' }] },
    { provider: '@pool', default_model: '', models: [{ id: 'fallback-pool', label: 'Fallback pool' }] }],
  },
  '/dashboard-api/workspace/default': { workspace: null },
};
const responses = new Map();
const sentMessages = [];
const reconnects = [];
const writes = [];
const backgroundSnapshots = [];
const pendingForks = [];
let delayedHistory;
let holdHistory = false;
let workspaceResolution;
let holdSessions = false;
const delayedSessions = [];
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (req.method === 'POST' && url.pathname.endsWith('/fork')) {
    let body = '';
    for await (const chunk of req) body += chunk;
    pendingForks.push({ res, payload: JSON.parse(body), path: url.pathname });
    return;
  }
  if (url.pathname === '/dashboard-api/sessions/events') {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write('event: snapshot\ndata: {"sessions":[]}\n\n');
    return;
  }
  if (url.pathname === '/dashboard-api/background-task-events') {
    const threadId = url.searchParams.get('thread_id');
    backgroundSnapshots.push(threadId);
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    const snapshot = { ...history[threadId], messages: [{ role: 'assistant', content: `${threadId} background snapshot` }] };
    res.end(`event: snapshot\ndata: ${JSON.stringify(snapshot)}\n\nevent: done\ndata: {}\n\n`);
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
    if (req.method !== 'GET') writes.push({ method: req.method, path: url.pathname });
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
    } else if (url.pathname === '/dashboard-api/nav-projects') {
      res.end(JSON.stringify({ projects: [{ key: 'no-workspace', label: 'No workspace', threads: Object.values(history).slice(0, 4) }], has_more: false, next_cursor: null }));
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
    await until(() => js(`location.pathname + location.search === ${JSON.stringify(href)}`), `navigate to ${href}`);
  };
  const model = () => js('document.querySelector(".agent-model-badge-model")?.textContent');
  const threadRunning = id => js(`document.querySelector(${JSON.stringify(`a[href="/c/${id}"]`)})?.closest('.nav-history-item').classList.contains('running')`);
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
  const preference = () => js('JSON.parse(localStorage.getItem("k41.dashboard.lastChatModel"))');
  const reload = async () => {
    const previousTimeOrigin = await js('performance.timeOrigin');
    await call('Page.reload');
    await until(() => js(`performance.timeOrigin !== ${previousTimeOrigin} && document.readyState === 'complete'`), 'page reloaded');
    await loaded();
  };
  const selectModel = async (id, provider = 'test') => {
    await js('document.querySelector(".model-picker-trigger").click()');
    await until(() => js('!!document.querySelector(".model-item-select")'), 'model menu');
    const modelId = provider === 'test' ? `model-${id}` : id;
    await js(`Array.from(document.querySelectorAll('.model-item-row')).find(el => el.dataset.provider === ${JSON.stringify(provider)} && el.dataset.modelId === ${JSON.stringify(modelId)}).querySelector('.model-item-select').click()`);
  };
  const selectAgent = async name => {
    await js('document.querySelector(".agent-picker-trigger").click()');
    await until(() => js('!!document.querySelector(".agent-card-item")'), 'agent menu');
    await js(`Array.from(document.querySelectorAll('.agent-card-item')).find(el => el.textContent.trim() === ${JSON.stringify(name)}).click()`);
  };
  const selectCustomModel = async identifier => {
    await js('document.querySelector(".model-picker-trigger").click()');
    await until(() => js('!!document.querySelector(".agent-model-search-input")'), 'model search');
    await js(`(() => {
      const input = document.querySelector('.agent-model-search-input');
      input.value = ${JSON.stringify(identifier)};
      input.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
    await until(() => js('!!document.querySelector(".model-item-custom")'), 'custom model option');
    await js('document.querySelector(".model-item-custom").click()');
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
  assert.deepEqual(await preference(), { agentName: 'default', provider: 'test', model: 'model-a' }, 'remember the agent and effective model when the request omits an override');
  await navigate('/chat');
  await loaded();
  assert.equal(await model(), 'model-a');
  await reload();
  assert.equal(await model(), 'model-a', 'new chats keep the last sent model across reloads');
  await selectModel('c');
  assert.deepEqual(await preference(), { agentName: 'default', provider: 'test', model: 'model-a' }, 'an unsent selection does not change the preference');
  await navigate('/c/second');
  await loaded();
  assert.equal(await model(), 'model-b', 'history keeps its own model');
  await navigate('/chat');
  await loaded();
  assert.equal(await model(), 'model-a', 'opening history does not change the next new chat');

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
  assert.deepEqual(await preference(), { agentName: 'default', provider: 'test', model: 'model-c' });
  await navigate('/settings/appearance');
  await navigate('/c/first');
  await loaded();
  assert.equal(await model(), 'model-c', 'running selection survives a page remount and stale history');
  await navigate('/c/second');
  await loaded();
  await send('second new prompt');
  assert.equal(sentMessages.at(-1).model, 'model-b');
  await navigate('/chat');
  await loaded();
  assert.equal(await model(), 'model-b', 'new chats inherit the latest send while older chats are streaming');
  await navigate('/c/second');
  await loaded();
  responses.get('first').write(JSON.stringify({ type: 'error', code: 'recursion_limit_reached', message: 'First conversation limit' }) + '\n');
  await delay(150);
  assert.equal(await model(), 'model-b');
  assert.equal(await js('document.querySelector(".chat-panel").textContent.includes("First conversation limit")'), false);
  assert.equal(await js(`!!document.querySelector('button[aria-label="Continue response"]')`), false);
  finish('first', 'first completed');
  await delay(150);
  assert.equal(await js(`!!document.querySelector('button[aria-label="Stop generating"]')`), true, 'finishing first cannot stop second');
  assert.deepEqual(await preference(), { agentName: 'default', provider: 'test', model: 'model-b' }, 'late stream completion cannot overwrite the latest send');

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
  assert.deepEqual(await preference(), { agentName: 'default', provider: 'test', model: 'model-b' }, 'external reconnection does not remember the reopened model');

  await navigate('/chat');
  await loaded();
  await selectModel('a');
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
  assert.deepEqual(await preference(), { agentName: 'default', provider: 'test', model: 'model-a' }, 'remember the send before workspace resolution completes');
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

  await navigate('/chat');
  await loaded();
  await selectAgent('Alternate');
  assert.equal(await model(), 'model-c', 'switching agents applies their configured model');
  assert.deepEqual(await preference(), { agentName: 'default', provider: 'test', model: 'model-a' }, 'switching agents alone does not change the preference');
  await navigate('/c/legacy');
  await loaded();
  assert.equal(await threadRunning('legacy'), false, 'an idle conversation must not be marked running');
  const requestsBeforeAgentChange = sentMessages.length;
  await selectAgent('Alternate');
  assert.equal(await threadRunning('legacy'), false, 'changing the agent must not mark an idle conversation running');
  assert.equal(sentMessages.length, requestsBeforeAgentChange, 'changing agents does not send a message');
  assert.equal(await js('document.querySelector(\'a[href="/c/legacy"]\').title.includes("alternate")'), true, 'the sidebar agent metadata updates without starting execution');
  await send('Remember the alternate agent model');
  assert.equal(await threadRunning('legacy'), true, 'sending a message marks the conversation running');
  assert.deepEqual(await preference(), { agentName: 'alternate', provider: 'test', model: 'model-c' });
  await navigate('/c/second');
  await loaded();
  assert.equal(await js('document.querySelector(".agent-model-badge-agent").textContent'), 'Default', 'existing conversations keep their own agent');
  assert.equal(await model(), 'model-b');
  await navigate('/chat');
  await loaded();
  assert.equal(await js('document.querySelector(".agent-model-badge-agent").textContent'), 'Alternate', 'new chats inherit the latest agent while its conversation is streaming');
  await navigate('/c/legacy');
  await loaded();
  assert.equal(await js('document.querySelector(".agent-model-badge-agent").textContent'), 'Alternate', 'streaming conversations retain their agent');
  finish('legacy', 'alternate agent completed');
  await completed();
  assert.equal(await threadRunning('legacy'), false, 'completed conversations clear their running indicator');
  await navigate('/chat');
  await loaded();
  assert.equal(await model(), 'model-c');
  assert.equal(await js('document.querySelector(".agent-model-badge-agent").textContent'), 'Alternate', 'new chats remember the last sent agent');
  await reload();
  assert.equal(await js('document.querySelector(".agent-model-badge-agent").textContent'), 'Alternate', 'the remembered agent survives reloads');
  assert.equal(await model(), 'model-c');
  await selectAgent('Default');
  await navigate('/c/second');
  await loaded();
  await navigate('/chat');
  await loaded();
  assert.equal(await js('document.querySelector(".agent-model-badge-agent").textContent'), 'Alternate', 'unsent agent changes do not replace the last sent agent');

  for (const platform of ['github', 'schedule', 'telegram']) {
    history[platform] = { ...structuredClone(history.second), thread_id: platform, platform, kind: 'background', model: 'model-b' };
    await navigate(`/c/${platform}`);
    await until(() => backgroundSnapshots.includes(platform), `${platform} background snapshot`);
    await until(() => js(`document.querySelector('.chat-panel')?.textContent.includes(${JSON.stringify(`${platform} background snapshot`)})`), `${platform} snapshot applied`);
    await loaded();
    assert.equal(await model(), 'model-b', 'automated conversations keep their configured model');
    assert.equal(await js('document.querySelector(".agent-model-badge-agent").textContent'), 'Default', 'automated conversations keep their configured agent');
    assert.deepEqual(await preference(), { agentName: 'alternate', provider: 'test', model: 'model-c' }, 'automated snapshots do not change the preference');
  }

  for (const [provider, modelId] of [['backup', 'model-a'], ['@pool', 'fallback-pool']]) {
    await navigate('/c/legacy');
    await loaded();
    await selectModel(modelId, provider);
    await send(`Remember ${provider}/${modelId}`);
    assert.deepEqual(await preference(), { agentName: sentMessages.at(-1).agent_name, provider, model: modelId });
    finish('legacy', `${provider} completed`);
    await completed();
    await navigate('/chat');
    await loaded();
    await reload();
    assert.equal(await model(), modelId, 'provider and pool identity survive reloads');
    assert.equal(await js('document.querySelector(".model-picker-trigger").title.startsWith("Model: " + ' + JSON.stringify(`${provider}/${modelId}`) + ')'), true);
  }

  for (const agentName of [undefined, 'removed-agent']) {
    await js(`localStorage.setItem('k41.dashboard.lastChatModel', ${JSON.stringify(JSON.stringify({ agentName, provider: 'test', model: 'model-c' }))})`);
    await reload();
    assert.equal(await js('document.querySelector(".agent-model-badge-agent").textContent'), 'Default', 'legacy preferences and removed agents fall back to the default agent');
    assert.equal(await model(), 'model-c', 'agent fallback preserves an available remembered model');
  }
  await js(`localStorage.setItem('k41.dashboard.lastChatModel', ${JSON.stringify(JSON.stringify({ agentName: 'alternate', provider: 'test', model: 'model-c' }))})`);
  await navigate('/settings/appearance');
  await navigate('/chat?agent=default');
  await loaded();
  assert.equal(await js('document.querySelector(".agent-model-badge-agent").textContent'), 'Default', 'an explicitly requested agent takes precedence over the remembered agent');
  await navigate('/c/second');
  await navigate('/chat');
  await loaded();

  for (const value of [
    '{', '{"provider":"removed","model":"model-a"}', '{"provider":"test","model":"removed-model"}',
    '{"provider":"@pool","model":"removed-pool"}', '{"provider":"removed","model":"manual-model","customModel":true}',
    '{"provider":"@pool","model":"removed-pool","customModel":true}',
  ]) {
    await js(`localStorage.setItem('k41.dashboard.lastChatModel', ${JSON.stringify(value)})`);
    await reload();
    assert.equal(await model(), 'model-a', 'invalid preferences fall back to the agent model');
  }
  await navigate('/settings/appearance');
  await navigate('/chat?agent=unavailable');
  await loaded();
  assert.equal(await model(), 'model-b', 'an unavailable agent model falls back to the system default');

  const catalog = fixtures['/dashboard-api/agents/providers'].model_catalogs[0];
  const originalModels = catalog.models;
  history.retired = {
    ...structuredClone(history.second), thread_id: 'retired', model: 'model-c',
  };
  await navigate('/c/retired');
  await loaded();
  await send('Remember a catalog model before it is removed');
  assert.deepEqual(await preference(), { agentName: 'default', provider: 'test', model: 'model-c' });
  catalog.models = originalModels.filter(option => option.id !== 'model-c');
  await navigate('/settings/appearance');
  await navigate('/c/retired');
  await loaded();
  finish('retired', 'catalog model completed');
  await completed();
  await send('Keep the original source of the selected model');
  assert.deepEqual(await preference(), { agentName: 'default', provider: 'test', model: 'model-c' }, 'a catalog selection cannot become custom after its model disappears');
  finish('retired', 'removed model follow-up completed');
  await completed();
  await navigate('/chat');
  await loaded();
  await reload();
  assert.equal(await model(), 'model-a', 'removed catalog models fall back instead of becoming custom models');
  await navigate('/c/retired');
  await loaded();
  assert.equal(await model(), 'model-a', 'history also falls back when its model was removed');
  catalog.models = originalModels;

  await js('localStorage.removeItem("k41.dashboard.lastChatModel")');
  await navigate('/settings/appearance');
  await navigate('/chat?agent=custom');
  await loaded();
  assert.equal(await model(), 'model-b', 'agent models without custom provenance still require catalog membership');
  await reload();
  assert.equal(await model(), 'model-b', 'reloads preserve the fallback for unavailable configured models');

  history.custom = {
    ...structuredClone(history.second), thread_id: 'custom', agent_name: 'custom', model: 'configured-custom-model',
  };
  await navigate('/c/custom');
  await loaded();
  assert.equal(await model(), 'model-b', 'history falls back for unavailable configured models');
  await selectAgent('Custom');
  assert.equal(await model(), 'configured-custom-model', 'explicit agent selection can use its configured custom model');
  await send('Remember the configured custom model');
  assert.equal(sentMessages.at(-1).provider, 'test');
  assert.equal(sentMessages.at(-1).model, 'configured-custom-model');
  finish('custom', 'configured custom model completed');
  await completed();
  await navigate('/chat');
  await loaded();
  await reload();
  assert.equal(await model(), 'configured-custom-model', 'new chats remember configured custom models');

  await navigate('/c/custom');
  await loaded();
  await selectCustomModel('test/manual-model');
  await send('Remember a manually entered custom model');
  assert.equal(sentMessages.at(-1).provider, 'test');
  assert.equal(sentMessages.at(-1).model, 'manual-model');
  assert.deepEqual(await preference(), { agentName: 'custom', provider: 'test', model: 'manual-model', customModel: true });
  history.custom.model = 'manual-model';
  await navigate('/settings/appearance');
  await navigate('/c/custom');
  await loaded();
  finish('custom', 'manual custom model completed');
  await completed();
  await send('Continue with the explicitly selected custom model');
  assert.deepEqual(await preference(), { agentName: 'custom', provider: 'test', model: 'manual-model', customModel: true }, 'stream restoration preserves custom model provenance');
  finish('custom', 'custom model follow-up completed');
  await completed();
  await navigate('/chat');
  await loaded();
  assert.equal(await model(), 'manual-model', 'new chats inherit the manually entered model');
  await reload();
  assert.equal(await model(), 'manual-model', 'remembered manual models survive reloads');
  assert.equal(await js('document.querySelector(".agent-model-badge-agent").textContent'), 'Custom');

  await navigate('/c/custom');
  await loaded();
  await selectCustomModel('test/manual-model');
  await selectModel('a');
  await send('Switch back to a catalog model');
  assert.deepEqual(await preference(), { agentName: 'custom', provider: 'test', model: 'model-a' }, 'selecting a catalog model clears custom model provenance');
  finish('custom', 'catalog selection completed');
  await completed();

  await js(`(() => {
    const getItem = Storage.prototype.getItem;
    const setItem = Storage.prototype.setItem;
    Storage.prototype.getItem = function(key) {
      if (key === 'k41.dashboard.lastChatModel') throw new Error('Storage blocked');
      return getItem.call(this, key);
    };
    Storage.prototype.setItem = function(key, value) {
      if (key === 'k41.dashboard.lastChatModel') throw new Error('Quota exceeded');
      return setItem.call(this, key, value);
    };
  })()`);
  await navigate('/c/legacy');
  await loaded();
  await navigate('/chat');
  await loaded();
  assert.equal(await model(), 'model-a', 'blocked preference reads fall back safely');
  await navigate('/c/legacy');
  await loaded();
  await selectModel('b');
  await send('Chat still works when preference storage fails');
  assert.equal(sentMessages.at(-1).model, 'model-b');
  finish('legacy', 'storage failure completed');
  await completed();
  assert.equal(writes.length, 0, 'remembering dashboard models must not write settings or conversation metadata');

  history.first.messages = [
    { id: 'u1', role: 'user', content: 'First prompt', message_index: 0, source_checkpoint_id: 'input-1' },
    { id: 'a1', role: 'assistant', content: 'First answer', message_index: 1, source_checkpoint_id: 'answer-1' },
    { id: 'u2', role: 'user', content: 'Second prompt', message_index: 2, source_checkpoint_id: 'input-2' },
    { id: 'a2', role: 'assistant', content: 'Second answer', message_index: 3, source_checkpoint_id: 'answer-2' },
  ];
  await navigate('/c/first');
  await loaded();
  await until(() => js(`document.querySelectorAll('button[aria-label="Branch conversation"]').length === 2`), 'branch actions on old and latest answers');
  assert.equal(await js(`document.querySelectorAll('button[aria-label="Copy response"]').length`), 2);
  assert.equal(await js(`document.querySelectorAll('button[aria-label="Regenerate response"]').length`), 1);
  await js(`(() => {
    window.forkEvents = 0;
    window.addEventListener('k41:threads-changed', () => window.forkEvents++);
    const button = document.querySelector('button[aria-label="Branch conversation"]');
    button.click(); button.click();
  })()`);
  await until(() => pendingForks.length === 1, 'one fork request despite repeated clicks');
  assert.deepEqual(pendingForks[0].payload, { source_checkpoint_id: 'answer-1', message_index: 1 });
  assert.equal(await js(`document.querySelector('button[aria-label="Branch conversation"]').disabled`), true);
  assert.equal(await js(`document.querySelector('button[aria-label="Branch conversation"]').getAttribute('aria-busy')`), 'true');
  history.fork = { ...history.first, thread_id: 'fork', title: 'first conversation (2)', messages: history.first.messages.slice(0, 2) };
  pendingForks.shift().res.writeHead(201, { 'Content-Type': 'application/json' }).end(JSON.stringify(history.fork));
  await until(() => js('location.pathname === "/c/fork"'), 'open created branch');
  await loaded();
  assert.equal(await js('document.querySelectorAll(".assistant-actions").length'), 1);
  assert.equal(await js('window.forkEvents'), 1, 'refresh navigation when the fork is created');

  await navigate('/c/first');
  await loaded();
  await js(`document.querySelector('button[aria-label="Branch conversation"]').click()`);
  await until(() => pendingForks.length === 1, 'start delayed fork');
  await navigate('/c/second');
  await loaded();
  const eventsBeforeLateFork = await js('window.forkEvents');
  pendingForks.shift().res.writeHead(201, { 'Content-Type': 'application/json' }).end(JSON.stringify(history.fork));
  await until(async () => (await js('window.forkEvents')) > eventsBeforeLateFork, 'publish late fork result');
  assert.equal(await js('location.pathname'), '/c/second', 'a delayed fork must not replace the current conversation');

  await navigate('/c/first');
  await loaded();
  await js(`document.querySelector('button[aria-label="Branch conversation"]').click()`);
  await until(() => pendingForks.length === 1, 'start rejected fork');
  pendingForks.shift().res.writeHead(409, { 'Content-Type': 'application/json' }).end(JSON.stringify({ detail: 'Conversation is busy.' }));
  await until(() => js(`document.querySelector('button[aria-label="Branch conversation"]')?.disabled === false`), 'restore actions after fork failure');
  assert.equal(await js('location.pathname'), '/c/first');
  assert.equal(await js('document.querySelectorAll(".assistant-actions").length'), 2, 'failed fork keeps the transcript');
  const sentBeforeRegenerate = sentMessages.length;
  await js(`document.querySelector('button[aria-label="Regenerate response"]').click()`);
  await until(() => sentMessages.length === sentBeforeRegenerate + 1, 'regenerate the latest response');
  assert.equal(sentMessages.at(-1).source_checkpoint_id, 'input-2', 'regeneration still uses the preceding user checkpoint');
  assert.equal(sentMessages.at(-1).message_index, 2);
  assert.equal(sentMessages.at(-1).message, 'Second prompt');
  finish('first', 'Regenerated after branching');
  await completed();
  await send('Continue after branching');
  await until(() => js(`document.querySelectorAll('button[aria-label="Branch conversation"]').length === 0`), 'hide branch actions during streaming');
  finish('first', 'Continued after branching');
  await completed();
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: model preferences, concurrent streams, delayed navigation, workspace snapshots, and conversation branching with old answers, repeated clicks, failures, and streaming.');
} finally {
  for (const response of responses.values()) response.end();
  workspaceResolution?.end();
  delayedHistory?.res.end();
  for (const response of delayedSessions) response.end();
  for (const fork of pendingForks) fork.res.end();
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
