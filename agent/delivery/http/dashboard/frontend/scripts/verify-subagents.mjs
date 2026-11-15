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

const descriptor = (id, name, status = 'completed', parent = 'subagents') => ({
  delegation_id: id, agent_name: name, parent_thread_id: parent,
  thread_id: `${parent}:sub:${id}`, tool_call_id: `delegate-${id}`, status,
});
const asyncTask = { ...descriptor('async', 'Background worker', 'waiting'), task_id: 'async', run_id: 'async-run',
  root_thread_id: 'subagents', execution_mode: 'async', version: 1, queued_updates: 0, result: '',
  interrupts: [{ id: 'permission-interrupt', value: { type: 'permission_request', request_id: 'permission-request',
    tool_call_id: 'write-report', action: 'write', resource: '/workspace/report.txt' } }] };
const delegatedMessages = (id, name) => [
  { id: `call-${id}`, role: 'assistant', content: '', tool_calls: [
    { id: `delegate-${id}`, name: 'delegate_task', args: { sub_agent: name } },
  ] },
  { id: `result-${id}`, role: 'tool', name: 'delegate_task', tool_call_id: `delegate-${id}`,
    content: 'Delegated result', subagent: descriptor(id, name) },
];
const history = {
  thread_id: 'subagents', active_checkpoint_id: 'saved', platform: 'api', user_id: 'dashboard',
  agent_name: 'default', messages: [
    { id: 'user', role: 'user', content: 'Inspect saved subagents.' },
    ...delegatedMessages('saved', 'Saved agent'),
    ...delegatedMessages('retry', 'Retry agent'),
    { id: 'failed-call', role: 'assistant', content: '', tool_calls: [
      { id: 'failed-delegate', name: 'delegate_task', args: { sub_agent: 'Missing' } },
    ] },
    { id: 'failed-result', role: 'tool', name: 'delegate_task', tool_call_id: 'failed-delegate',
      content: '[error] not_found: agent config not found' },
  ],
};
const threads = [{ thread_id: 'subagents', title: 'Subagent display', platform: 'api', user_id: 'dashboard', agent_name: 'default' }];
const fixtures = {
  '/health': { status: 'ok' },
  '/dashboard-api/catalog': { backends: [] },
  '/dashboard-api/agents/cards': { cards: [{ name: 'default', display_name: 'Default', provider: 'test', model: 'demo', valid: true }] },
  '/dashboard-api/agents/providers': { provider_names: ['test'], default_provider: 'test', default_model: 'demo', model_catalogs: [{ provider: 'test', default_model: 'demo', models: [{ id: 'demo', label: 'Demo' }] }] },
  '/dashboard-api/workspace/default': { workspace: null },
  '/dashboard-api/chat-history': { threads },
  '/dashboard-api/chat-history/subagents': history,
  '/dashboard-api/browser/threads/subagents': { open: false, generation: null, profile_id: null,
    mode: 'agent', tabs: [], handoff: null, handoff_expired: false, images: [], sessions: [] },
  '/dashboard-api/nav-projects': { projects: [{ key: 'no-workspace', label: 'No workspace', threads }], has_more: false, next_cursor: null },
};
const historyRequests = new Map();
let savedResponse;
let stream;
let parentReconnect;
let sessionsStream;
let backgroundVisible = false;
const liveTasks = new Map();
const readyChildren = new Set();
const childStreams = new Map();
const childSequences = new Map();
const reconnectRequests = new Map();
const childHistory = new Map();
const delayedHistories = new Map();
let sequence = 0;
const send = (method, data, namespace = []) => {
  stream.write(JSON.stringify({ type: 'event', method, params: { namespace, data, timestamp: Date.now() },
    seq: ++sequence, event_id: `subagents:${sequence}` }) + '\n');
};
const publishTask = task => sessionsStream?.write(`event: subagent_task_updated\ndata: ${JSON.stringify(task)}\n\n`);
const sendChild = (id, method, data) => {
  const seq = (childSequences.get(id) || 0) + 1;
  childSequences.set(id, seq);
  childStreams.get(id).write(JSON.stringify({ type: 'event', method,
    params: { namespace: [], data, timestamp: Date.now() }, seq, event_id: `${id}-stream:${seq}` }) + '\n');
};
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/dashboard-api/sessions/events') {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write('event: snapshot\ndata: {"sessions":[]}\n\n');
    sessionsStream = res;
    req.on('close', () => { if (sessionsStream === res) sessionsStream = undefined; });
    return;
  }
  if (url.pathname.startsWith('/v2/api/chat/subagents/async-tasks')) {
    const action = url.pathname.split('/')[7];
    if (req.method === 'POST') {
      let body = '';
      for await (const chunk of req) body += chunk;
      const payload = body ? JSON.parse(body) : {};
      if (action === 'updates') {
        assert.ok(payload.request_id);
        assert.equal(payload.instructions, 'Include references');
        asyncTask.queued_updates += 1;
      } else if (action === 'resume') {
        assert.equal(payload.interrupt_id, 'permission-interrupt');
        assert.equal(payload.run_id, 'async-run');
        assert.equal(payload.payload.action, 'permission');
        assert.equal(payload.payload.decision, 'allow_once');
        asyncTask.status = 'pending';
        asyncTask.interrupts = [];
      } else if (action === 'cancel') {
        asyncTask.status = 'cancelled';
      }
      asyncTask.version += 1;
      sessionsStream?.write(`event: subagent_task_updated\ndata: ${JSON.stringify(asyncTask)}\n\n`);
    }
    res.setHeader('Content-Type', 'application/json');
    const active = backgroundVisible && ['pending', 'running', 'waiting'].includes(asyncTask.status);
    const tasks = [...(active ? [asyncTask] : []), ...Array.from(liveTasks.values()).filter(task => ['pending', 'running', 'waiting'].includes(task.status))];
    res.end(JSON.stringify(url.pathname.endsWith('/async-tasks')
      ? { tasks, total: tasks.length, active_count: tasks.length }
      : liveTasks.get(url.pathname.split('/')[6]) || asyncTask));
    return;
  }
  if (url.pathname === '/v2/api/chat/events/reconnect') {
    let body = '';
    for await (const chunk of req) body += chunk;
    const thread = JSON.parse(body).thread_id;
    if (thread === 'subagents') {
      res.writeHead(200, { 'Content-Type': 'application/x-ndjson' });
      res.flushHeaders();
      parentReconnect = res;
      return;
    }
    const task = Array.from(liveTasks.values()).find(task => task.thread_id === thread);
    assert.ok(task);
    reconnectRequests.set(task.task_id, (reconnectRequests.get(task.task_id) || 0) + 1);
    res.writeHead(200, { 'Content-Type': 'application/x-ndjson' });
    res.flushHeaders();
    if (!readyChildren.has(task.task_id)) res.end();
    else childStreams.set(task.task_id, res);
    return;
  }
  if (url.pathname === '/v2/api/chat/events') {
    for await (const chunk of req) { /* Consume the chat request body. */ }
    res.writeHead(200, { 'Content-Type': 'application/x-ndjson' });
    res.flushHeaders();
    stream = res;
    send('lifecycle', { event: 'started', thread_id: 'subagents' });
    return;
  }
  if (/\/subagents\/[^/]+$/.test(url.pathname)) {
    const id = url.pathname.split('/').at(-1);
    const count = (historyRequests.get(id) || 0) + 1;
    historyRequests.set(id, count);
    res.setHeader('Content-Type', 'application/json');
    if (id === 'saved') {
      savedResponse = res;
    } else if (liveTasks.has(id)) {
      if (liveTasks.get(id).status === 'completed') delayedHistories.set(id, res);
      else res.end(JSON.stringify({ messages: childHistory.get(id) || [] }));
    } else if (id === 'retry' && count === 1) {
      res.writeHead(503);
      res.end(JSON.stringify({ detail: 'History temporarily unavailable' }));
    } else {
      res.end(JSON.stringify({ messages: id === 'retry' ? [
        { id: 'read', role: 'assistant', content: '', tool_calls: [{ id: 'read', name: 'read_file', args: { path: 'retry.txt' } }] },
        { id: 'read-result', role: 'tool', tool_call_id: 'read', name: 'read_file', content: '' },
      ] : [] }));
    }
    return;
  }
  if (url.pathname.startsWith('/dashboard-api/') || url.pathname === '/health') {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('X-CSRF-Token', 'subagent-test-csrf');
    res.end(JSON.stringify(fixtures[url.pathname] || { sessions: [], threads: [], tools: [] }));
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
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-subagents-'));
const browser = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-networking',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' });
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
  let nextId = 0;
  const pending = new Map();
  const exceptions = [];
  call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId;
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
  const card = name => `Array.from(document.querySelectorAll('.subagent-details')).find(el => el.querySelector(':scope > summary .subagent-name')?.textContent === ${JSON.stringify(name)})`;
  const summary = name => `(${card(name)}).querySelector(':scope > summary .subagent-tool-summary').textContent`;
  const detail = name => `(${card(name)}).querySelector(':scope > summary .subagent-current-tool').textContent`;
  const toggle = name => js(`(${card(name)}).querySelector(':scope > summary').click()`);
  await call('Page.enable');
  await call('Runtime.enable');
  await call('Page.navigate', { url: base + '/c/subagents' });
  await until(() => js(`!!(${card('Saved agent')})`), 'saved subagent card');
  await until(() => savedResponse, 'background history request');
  assert.equal(await js(`(${card('Saved agent')}).open`), false);
  assert.equal(await js(summary('Saved agent')), '· Loading tools...');
  assert.equal(await js('document.querySelector(".subagent-transcript")'), null);
  assert.equal(historyRequests.get('nested') || 0, 0);
  const nested = descriptor('nested', 'Nested agent', 'completed', 'subagents:sub:saved');
  savedResponse.end(JSON.stringify({ messages: [
    { id: 'read', role: 'assistant', content: '', tool_calls: [{ id: 'read', name: 'read_file', args: { path: 'saved.txt' } }] },
    { id: 'read-result', role: 'tool', tool_call_id: 'read', name: 'read_file', content: '' },
    { id: 'nested-call', role: 'assistant', content: '', tool_calls: [{ id: nested.tool_call_id, name: 'delegate_task', args: {} }] },
    { id: 'nested-result', role: 'tool', tool_call_id: nested.tool_call_id, name: 'delegate_task', content: 'Nested answer', subagent: nested },
    { id: 'answer', role: 'assistant', content: 'Saved child answer' },
  ] }));
  savedResponse = null;
  await until(() => js(`${summary('Saved agent')} === '· 2 tools: 1 read, 1 delegate task'`), 'collapsed historical count');
  assert.equal(await js(detail('Saved agent')), 'Completed');
  assert.equal(await js('document.querySelector(".subagent-transcript")'), null);
  assert.equal(historyRequests.get('saved'), 1);
  assert.equal(historyRequests.get('nested') || 0, 0, 'collapsed parents do not mount nested agents');
  await js(`window.subagentToggles = 0; document.addEventListener('k41:transcript-tool-toggle', () => subagentToggles++)`);
  await toggle('Saved agent');
  await until(() => js(`!!(${card('Nested agent')})`), 'nested card on expansion');
  await until(() => historyRequests.get('nested') === 1, 'nested history on expansion');
  assert.equal(await js(`(${card('Nested agent')}).open`), false);
  assert.equal(await js('subagentToggles > 0'), true, 'expansion notifies the transcript scroll controller');
  assert.equal(await js('document.querySelectorAll(".tool-call-summary").length'), 2, 'only child read and delegation without subagent metadata render raw calls');
  await toggle('Saved agent');
  await toggle('Saved agent');
  await until(() => js(`(${card('Saved agent')}).open`), 'reopened saved card');
  assert.equal(historyRequests.get('saved'), 1, 'reopening reuses the parent history');
  await toggle('Saved agent');

  await until(() => js(`${summary('Retry agent')} === '· Tools unavailable'`), 'history load error');
  await toggle('Retry agent');
  await until(() => js(`!!(${card('Retry agent')}).querySelector('[role="alert"] button')`), 'history retry action');
  await js(`(${card('Retry agent')}).querySelector('[role="alert"] button').click()`);
  await until(() => js(`${summary('Retry agent')} === '· 1 tool: 1 read'`), 'retried summary');
  assert.equal(historyRequests.get('retry'), 2);
  await toggle('Retry agent');
  for (const width of [360, 768, 1280, 1920]) {
    await call('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: false });
    for (const workspaceOpen of [false, true]) {
      await js(`(() => {
        const button = document.querySelector('button[aria-label="Open workspace"], button[aria-label="Close workspace"]');
        if (button.getAttribute('aria-expanded') !== '${workspaceOpen}') button.click();
      })()`);
      await until(() => js(`!!document.querySelector('.chat-shell.workspace-${workspaceOpen ? 'open' : 'closed'}')`), 'workspace layout');
      const bounds = await js(`(() => {
        const reference = document.querySelector('.transcript-virtual-row').getBoundingClientRect();
        return Array.from(document.querySelectorAll('.transcript .subagent-details')).map(el => {
          const card = el.getBoundingClientRect();
          return { left: card.left, right: card.right, columnLeft: reference.left, columnRight: reference.right };
        });
      })()`);
      for (const bound of bounds) {
        assert.ok(bound.left >= bound.columnLeft - 1 && bound.right <= bound.columnRight + 1,
          `Subagent exceeds the chat column at ${width}px with workspace ${workspaceOpen}: ${JSON.stringify(bound)}`);
      }
    }
  }
  await js(`document.querySelector('button[aria-label="Close workspace"]')?.click()`);
  await toggle('Saved agent');
  const nestedBounds = await js(`(() => {
    const parent = (${card('Saved agent')}).querySelector('.subagent-transcript').getBoundingClientRect();
    const child = (${card('Nested agent')}).getBoundingClientRect();
    return { parentLeft: parent.left, parentRight: parent.right, left: child.left, right: child.right };
  })()`);
  assert.ok(nestedBounds.left >= nestedBounds.parentLeft - 1 && nestedBounds.right <= nestedBounds.parentRight + 1,
    `Nested subagent exceeds its parent: ${JSON.stringify(nestedBounds)}`);
  await toggle('Saved agent');
  console.log('PASS: collapsed saved agents load summaries, preserve lazy nested views, retry errors and reuse history.');

  backgroundVisible = true;
  history.messages.push(
    { id: 'async-call', role: 'assistant', content: '', tool_calls: [
      { id: 'delegate-async', name: 'start_async_task', args: { task: 'Write a report', sub_agent: 'worker' } },
    ] },
    { id: 'async-result', role: 'tool', name: 'start_async_task', tool_call_id: 'delegate-async',
      content: 'Task accepted', subagent: { ...asyncTask } },
  );
  await call('Page.reload');

  await until(() => js(`!!(${card('Background worker')}) && ${detail('Background worker')} === 'Waiting for your response'`), 'waiting background task');
  await until(() => js(`document.querySelector('.transcript-async-wait')?.textContent.includes('1 background agent needs your response')`), 'background user response divider');
  assert.equal(await js('document.querySelectorAll(".subagent-name").length === new Set(Array.from(document.querySelectorAll(".subagent-name"), el => el.textContent)).size'), true,
    'durable task recovery does not duplicate transcript cards');
  history.messages = history.messages.filter(message => !['async-call', 'async-result'].includes(message.id));
  await call('Page.reload');
  await until(() => js(`!!document.querySelector('.async-task-list') && !!(${card('Background worker')})
    && ${detail('Background worker')} === 'Waiting for your response'`), 'task controls recovered without the compacted launch');
  assert.equal(await js(`!!document.querySelector('button[aria-label="Send message"]')`), true,
    'the parent stays available for new messages while a child waits');
  await toggle('Background worker');
  await until(() => js(`!!(${card('Background worker')}).querySelector('.user-input-request-card')`), 'child permission card');
  await js(`(() => {
    const root = (${card('Background worker')});
    const input = root.querySelector('.async-task-controls textarea');
    input.value = 'Include references';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    Array.from(root.querySelectorAll('button')).find(el => el.textContent === 'Queue instructions').click();
  })()`);
  await until(() => js(`(${card('Background worker')}).textContent.includes('1 follow-up(s) queued')`), 'durable queued update');
  assert.equal(asyncTask.status, 'waiting', 'updating a child preserves its permission request');
  await until(() => js(`Array.from((${card('Background worker')}).querySelectorAll('button')).some(el => el.textContent.includes('Allow once') && !el.disabled)`), 'permission controls settle after update');
  await js(`Array.from((${card('Background worker')}).querySelectorAll('button')).find(el => el.textContent.includes('Allow once')).click()`);
  await until(() => js(`Array.from((${card('Background worker')}).querySelectorAll('button')).some(el => el.textContent.includes('Apply decision') && !el.disabled)`), 'permission decision selected');
  await js(`Array.from((${card('Background worker')}).querySelectorAll('button')).find(el => el.textContent.includes('Apply decision')).click()`);
  await until(() => js(`${detail('Background worker')} === 'Queued'`), 'resume targets exact child interrupt');
  await until(() => js(`document.querySelector('.transcript-async-wait')?.textContent.includes('Waiting for 1 background agent...')`), 'queued background wait divider');
  await until(() => js(`Array.from((${card('Background worker')}).querySelectorAll('button')).some(el => el.textContent === 'Cancel task' && !el.disabled)`), 'task controls settle after resume');
  await js(`Array.from((${card('Background worker')}).querySelectorAll('button')).find(el => el.textContent === 'Cancel task').click()`);
  await until(() => js(`!(${card('Background worker')})`), 'cancelled recovered task leaves the active list');
  await until(() => js(`!document.querySelector('.transcript-async-wait')`), 'cancelled task clears its wait divider');
  console.log('PASS: compacted background task recovers controls, queues instructions, preserves approval, resumes its exact interrupt and cancels independently.');

  await until(() => js('document.querySelector(".chat-prompt-input")?.disabled === false'), 'chat composer');
  await js(`(() => {
    const input = document.querySelector('.chat-prompt-input');
    input.value = 'Run concurrent subagents';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector('button[aria-label="Send message"]').click();
  })()`);
  await until(() => stream, 'live stream');
  const startChild = (id, name) => {
    send('tools', { event: 'tool-started', tool_call_id: `delegate-${id}`, tool_name: 'delegate_task', input: { sub_agent: name } });
    send('lifecycle', { event: 'started', delegation_id: id, tool_call_id: `delegate-${id}`, agent_name: name,
      thread_id: `subagents:sub:${id}` }, [`child:${id}`]);
  };
  const startTool = (child, id, command) => send('tools', {
    event: 'tool-started', tool_call_id: id, tool_name: 'bash', input: { command },
  }, [`child:${child}`]);
  startChild('a', 'Kode');
  startChild('b', 'Researcher');
  await until(() => js(`!!(${card('Kode')}) && !!(${card('Researcher')})`), 'concurrent subagents');
  assert.equal(await js(`(${card('Kode')}).open`), false);
  assert.equal(await js(detail('Kode')), 'Starting...');
  assert.equal(await js(summary('Kode')), '· 0 tools');
  assert.equal(historyRequests.get('a') || 0, 0, 'live snapshots do not fetch history');
  startTool('a', 'a1', 'pnpm check');
  startTool('a', 'a2', 'pnpm test');
  startTool('b', 'b1', 'uv run search.py');
  send('tools', { event: 'tool-output-delta', tool_call_id: 'delegate-a', delta: 'RAW_DELEGATION_PROGRESS' });
  await until(() => js(`${detail('Kode')} === 'bash pnpm test · +1'`), 'latest parallel tool');
  assert.equal(await js(summary('Kode')), '· 2 tools: 2 commands');
  assert.equal(await js(detail('Researcher')), 'bash uv run search.py');
  assert.equal(await js('document.body.textContent.includes("RAW_DELEGATION_PROGRESS")'), false);
  assert.equal(await js('document.querySelectorAll(".tool-call-summary").length'), 1, 'delegation raw calls are replaced by cards');
  assert.equal(await js(`(${card('Kode')}).hasAttribute('data-transcript-item-id')`), true);
  await toggle('Kode');
  await until(() => js(`(${card('Kode')}).open`), 'manually expanded live agent');
  startTool('a', 'a3', 'pnpm build');
  await until(() => js(`${detail('Kode')} === 'bash pnpm build · +2'`), 'live update in open card');
  assert.equal(await js(`(${card('Kode')}).open`), true);
  await toggle('Kode');
  send('tools', { event: 'tool-finished', tool_call_id: 'a3', output: '' }, ['child:a']);
  await until(() => js(`${detail('Kode')} === 'bash pnpm test · +1'`), 'finished tools stop being active before result projection');
  assert.equal(await js(`(${card('Kode')}).open`), false, 'updates respect manual collapse');
  send('tools', { event: 'tool-error', tool_call_id: 'a2' }, ['child:a']);
  send('custom:k41', { type: 'tool_result', tool_call_id: 'a1', name: 'bash', content: '', projection: 'ui' }, ['child:a']);
  await until(() => js(`${detail('Kode')} === 'Last tool: bash pnpm build'`), 'latest tool retained between calls');
  send('custom:k41', { type: 'thinking', content: 'Inspecting the results.\n\nChecking the imports now.', projection: 'ui' }, ['child:a']);
  await until(() => js(`${detail('Kode')} === 'Thinking: Checking the imports now.'`), 'actual reasoning in the summary');
  startTool('a', 'a4', 'pnpm lint');
  send('tools', { event: 'tool-finished', tool_call_id: 'a4', output: '' }, ['child:a']);
  send('custom:k41', { type: 'tool_result', tool_call_id: 'a4', name: 'bash', content: '', projection: 'ui' }, ['child:a']);
  await until(() => js(`${detail('Kode')} === 'Last tool: bash pnpm lint'`), 'fast tools remain visible after completion');
  await delay(150);
  assert.equal(await js(detail('Kode')), 'Last tool: bash pnpm lint', 'idle gaps must not replace the last activity with generic thinking');
  send('custom:k41', { type: 'message', content: 'All checks are ready for review.', projection: 'ui' }, ['child:a']);
  await until(() => js(`${detail('Kode')} === 'Latest update: All checks are ready for review.'`), 'actual assistant update');
  send('lifecycle', { event: 'completed', delegation_id: 'a', tool_call_id: 'delegate-a', agent_name: 'Kode' }, ['child:a']);
  send('lifecycle', { event: 'stopped', delegation_id: 'b', tool_call_id: 'delegate-b', agent_name: 'Researcher' }, ['child:b']);
  await until(() => js(`${detail('Kode')} === 'Completed' && ${detail('Researcher')} === 'Stopped'`), 'terminal statuses override pending calls');
  assert.equal(await js(`(${card('Kode')}).open`), false);
  console.log('PASS: concurrent live agents remain collapsed, track parallel tools and retain manual expansion state.');

  const longName = 'LongAgent'.repeat(30);
  startChild('long', longName);
  startTool('long', 'long-command', 'pnpm test ' + 'long-command-argument/'.repeat(40));
  await until(() => js(`!!(${card(longName)}) && ${detail(longName)}.startsWith('bash pnpm test')`), 'long subagent summary');
  for (const width of [360, 768, 1280, 1920]) {
    await call('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: false });
    const dimensions = await js(`(() => {
      const root = (${card(longName)});
      const heading = root.querySelector('.subagent-heading');
      const current = root.querySelector('.subagent-current-tool');
      return { viewport: innerWidth, pageWidth: document.documentElement.scrollWidth,
        headingTop: heading.getBoundingClientRect().top, currentTop: current.getBoundingClientRect().top,
        headingHeight: heading.getBoundingClientRect().height,
        lineHeight: parseFloat(getComputedStyle(heading).lineHeight),
        detailWidth: current.clientWidth, detailContentWidth: current.scrollWidth, title: current.title };
    })()`);
    assert.ok(dimensions.pageWidth <= dimensions.viewport, `Horizontal overflow at ${width}px: ${JSON.stringify(dimensions)}`);
    assert.ok(dimensions.currentTop > dimensions.headingTop, 'current tool occupies the second line');
    assert.ok(dimensions.headingHeight <= dimensions.lineHeight + 1, 'heading stays on one line');
    assert.ok(dimensions.detailContentWidth > dimensions.detailWidth, 'long tool text truncates');
    assert.ok(dimensions.title.includes('long-command-argument/'), 'full detail remains available in the tooltip');
  }
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: two-line summaries fit the chat column and truncate long names and tools at 360px, 768px, 1280px and 1920px.');

  await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
  const transcriptTop = await js('document.querySelector(".transcript-container").getBoundingClientRect().top');
  for (const [id, name] of [['async-first', 'Async first'], ['async-second', 'Async second']]) {
    const task = { ...descriptor(id, name, 'running'), task_id: id, execution_mode: 'async',
      run_id: `${id}-job`, root_thread_id: 'subagents', version: 1 };
    liveTasks.set(id, task);
    childHistory.set(id, [{ id: `${id}-input`, role: 'user', content: 'Child instructions' }]);
    send('tools', { event: 'tool-started', tool_call_id: task.tool_call_id,
      tool_name: 'start_async_task', input: { task: 'Inspect source', sub_agent: name } });
  }
  for (const task of Array.from(liveTasks.values()).reverse()) publishTask(task);
  await until(() => js(`!!(${card('Async first')}) && !!(${card('Async second')})`), 'inline async cards before launch results');
  assert.equal(await js(`!!document.querySelector('.transcript-async-wait')`), false,
    'the wait divider stays hidden while the parent is still working');
  assert.deepEqual(await js(`Array.from(document.querySelectorAll('.transcript .subagent-name'), el => el.textContent).filter(name => name.startsWith('Async '))`), ['Async first', 'Async second']);
  assert.equal(await js('document.querySelector(".async-task-list")'), null, 'pending launch calls prevent temporary cards above the transcript');
  assert.equal(await js('document.querySelector(".transcript-container").getBoundingClientRect().top'), transcriptTop);
  await js(`window.asyncCard = (${card('Async first')})`);
  await toggle('Async first');
  await until(() => (reconnectRequests.get('async-first') || 0) > 0, 'first connection before child admission');
  readyChildren.add('async-first');
  await until(() => childStreams.has('async-first'), 'retry after child session admission');
  sendChild('async-first', 'custom:k41', { type: 'message', message_id: 'child-answer', projection: 'ui', content: 'Live answer' });
  await until(() => js(`asyncCard.textContent.includes('Live answer')`), 'live async child text');
  await js(`window.asyncMessage = Array.from(asyncCard.querySelectorAll('.message.assistant')).find(el => el.textContent.includes('Live answer'))`);
  sendChild('async-first', 'custom:k41', { type: 'message', message_id: 'child-answer', projection: 'ui', content: ' continues' });
  await until(() => js(`asyncMessage.textContent.includes('Live answer continues')`), 'live delta retains the message element');
  assert.equal(await js(`asyncCard === (${card('Async first')}) && asyncCard.open && asyncMessage.isConnected`), true);
  const firstTask = liveTasks.get('async-first');
  firstTask.status = 'completed';
  firstTask.version = 3;
  publishTask(firstTask);
  await until(() => delayedHistories.has('async-first'), 'completion history refresh');
  assert.equal(await js('asyncMessage.isConnected && asyncMessage.textContent.includes("Live answer continues")'), true,
    'completion retains live text while its saved checkpoint is loading');
  assert.equal(await js('asyncCard.textContent.includes("Loading subagent history...")'), false,
    'refreshing an existing transcript does not insert a loading row');
  for (const task of Array.from(liveTasks.values()).reverse()) send('custom:k41', { type: 'tool_result', projection: 'ui',
    tool_call_id: task.tool_call_id, name: 'start_async_task', content: 'Accepted',
    subagent: { ...task, version: 1, status: 'pending' } });
  delayedHistories.get('async-first').end(JSON.stringify({ messages: [
    ...childHistory.get('async-first'),
    { id: 'lc_run--child-answer', role: 'assistant', content: 'Live answer continues' },
  ] }));
  delayedHistories.delete('async-first');
  await until(() => js(`${detail('Async first')} === 'Completed'`), 'stale launch result preserves completed status');
  assert.equal(await js(`asyncCard === (${card('Async first')}) && asyncCard.open && asyncMessage.isConnected`), true);
  assert.equal(await js(`asyncCard.querySelectorAll('.message.assistant').length`), 1, 'checkpoint and stream replay share one message');
  assert.deepEqual(await js(`Array.from(document.querySelectorAll('.transcript .subagent-name'), el => el.textContent).filter(name => name.startsWith('Async '))`), ['Async first', 'Async second']);
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: asynchronous launches keep their original order and DOM, retry session admission, retain live text during completion and reject stale results.');

  const secondTask = liveTasks.get('async-second');
  secondTask.status = 'completed';
  secondTask.version = 3;
  publishTask(secondTask);
  await until(() => delayedHistories.has('async-second'), 'second child completion');
  delayedHistories.get('async-second').end(JSON.stringify({ messages: childHistory.get('async-second') }));
  delayedHistories.delete('async-second');
  history.messages = [
    { id: 'parent-request', role: 'user', content: 'Run concurrent subagents' },
    { id: 'parent-launches', role: 'assistant', content: '', tool_calls: Array.from(liveTasks.values(), task => ({
      id: task.tool_call_id, name: 'start_async_task', args: { sub_agent: task.agent_name },
    })) },
    ...Array.from(liveTasks.values(), task => ({ id: `result-${task.task_id}`, role: 'tool', name: 'start_async_task',
      tool_call_id: task.tool_call_id, content: 'Accepted', subagent: { ...task } })),
    { id: 'initial-parent-answer', role: 'assistant', content: 'Waiting for both workers.' },
  ];
  send('custom:k41', { type: 'message', message_id: 'initial-parent-answer', projection: 'ui', content: 'Waiting for both workers.' });
  send('lifecycle', { event: 'completed', thread_id: 'subagents' });
  stream.end();
  await until(() => js(`!!document.querySelector('button[aria-label="Send message"]') && !!(${card('Async first')}) && !!(${card('Async second')})`), 'parent launch run finishes');
  await js(`window.wakeupCards = [(${card('Async first')}), (${card('Async second')})]`);
  sessionsStream.write(`event: session_started\ndata: ${JSON.stringify({ session_id: 'parent-wakeup', thread_id: 'subagents',
    platform: 'api', user_id: 'dashboard', agent_name: 'default', current_step: 'responding', tools_called: [] })}\n\n`);
  await until(() => parentReconnect, 'parent continuation reconnect');
  assert.equal(await js(`wakeupCards.every(el => el.isConnected)`), true,
    'starting the parent continuation must retain both subagent cards');
  let parentSeq = 0;
  let parentRunId = 'parent-wakeup';
  const parentEvent = (method, data) => parentReconnect.write(JSON.stringify({ type: 'event', method,
    params: { namespace: [], data, timestamp: Date.now() }, seq: ++parentSeq, event_id: `${parentRunId}:${parentSeq}` }) + '\n');
  parentEvent('lifecycle', { event: 'started', thread_id: 'subagents' });
  const firstWakeup = { wakeup_id: 'first-parent-wakeup', agent_names: ['Async first', 'Async second'] };
  parentEvent('custom:k41', { type: 'async_wakeup', ...firstWakeup, projection: 'ui' });
  await until(() => js(`document.querySelector('.transcript-run-divider')?.textContent.includes('Async first and Async second')`), 'shared wakeup divider before the first token');
  await js(`window.wakeupDivider = document.querySelector('.transcript-run-divider')`);
  parentEvent('messages', [{ event: 'message-start', id: 'combined-parent-answer' }, { run_id: 'combined-parent-answer' }]);
  parentEvent('custom:k41', { type: 'message', message_id: 'combined-parent-answer', projection: 'ui', content: 'Combining both results' });
  await until(() => js('document.querySelector(".transcript").textContent.includes("Combining both results")'), 'parent streams the combined answer');
  assert.equal(await js('wakeupCards.every(el => el.isConnected)'), true,
    'both completed children remain mounted while the combined answer streams');
  assert.equal(await js(`document.querySelectorAll('.transcript-run-divider').length`), 1, 'parallel results share one wakeup divider');
  assert.equal(await js(`(() => {
    const rows = Array.from(document.querySelectorAll('.transcript-virtual-row'));
    const marker = rows.findIndex(el => el.contains(wakeupDivider));
    const answer = rows.findIndex(el => el.textContent.includes('Combining both results'));
    return marker >= 0 && answer > marker;
  })()`), true, 'the wakeup divider precedes its new answer');
  history.messages.push(
    { id: 'browser-completed', role: 'system', source: 'async_subagent', async_wakeup: firstWakeup, content: 'Background task completed: Browser' },
    { id: 'researcher-completed', role: 'system', source: 'async_subagent', async_wakeup: firstWakeup, content: 'Background task completed: Researcher' },
    { id: 'combined-parent-answer', role: 'assistant', content: 'Combining both results into one answer.' },
  );
  parentEvent('custom:k41', { type: 'message', message_id: 'combined-parent-answer', projection: 'ui', content: ' into one answer.' });
  parentEvent('lifecycle', { event: 'completed', thread_id: 'subagents' });
  parentReconnect.end();
  sessionsStream.write(`event: session_stopped\ndata: ${JSON.stringify({ session_id: 'parent-wakeup', thread_id: 'subagents' })}\n\n`);
  await until(() => js(`!!document.querySelector('button[aria-label="Send message"]')
    && document.querySelector('.transcript').textContent.includes('Combining both results into one answer.')`), 'parent continuation finishes');
  assert.equal(await js('document.querySelector(".transcript").textContent.includes("Background task completed:")'), false,
    'internal completion notifications do not appear after the final history refresh');
  assert.equal(await js('wakeupCards.every(el => el.isConnected)'), true,
    'checkpoint refreshes preserve the same subagent card elements');
  assert.equal(await js('wakeupDivider.isConnected && document.querySelectorAll(".transcript-run-divider").length === 1'), true,
    'the saved checkpoint retains the same wakeup divider without duplicating parallel notifications');
  assert.deepEqual(await js(`Array.from(document.querySelectorAll('.transcript .subagent-name'), el => el.textContent)`), ['Async first', 'Async second']);
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: parent wakeups retain both completed subagent cards throughout streaming and checkpoint refreshes without internal completion messages.');

  await call('Page.reload');
  await until(() => js(`document.querySelector('.transcript-run-divider')?.dataset.wakeupId === 'first-parent-wakeup'`), 'first wakeup divider restored after reload');
  await js(`window.firstSavedDivider = document.querySelector('.transcript-run-divider')`);
  parentReconnect = undefined;
  sessionsStream.write(`event: session_started\ndata: ${JSON.stringify({ session_id: 'parent-wakeup-2', thread_id: 'subagents',
    platform: 'api', user_id: 'dashboard', agent_name: 'default', current_step: 'responding', tools_called: [] })}\n\n`);
  await until(() => parentReconnect, 'second parent wakeup reconnect');
  parentSeq = 0;
  parentRunId = 'parent-wakeup-2';
  const secondWakeup = { wakeup_id: 'second-parent-wakeup', agent_names: ['Async first'] };
  parentEvent('lifecycle', { event: 'started', thread_id: 'subagents' });
  parentEvent('custom:k41', { type: 'async_wakeup', ...secondWakeup, projection: 'ui' });
  parentEvent('custom:k41', { type: 'async_wakeup', ...secondWakeup, projection: 'ui' });
  await until(() => js(`document.querySelectorAll('.transcript-run-divider').length === 2`), 'a later wakeup adds one distinct divider');
  assert.equal(await js('firstSavedDivider.isConnected'), true);
  parentEvent('messages', [{ event: 'message-start', id: 'second-parent-answer' }, { run_id: 'second-parent-answer' }]);
  parentEvent('custom:k41', { type: 'message', message_id: 'second-parent-answer', projection: 'ui', content: 'Additional background result' });
  history.messages.push(
    { id: 'browser-follow-up', role: 'system', source: 'async_subagent', async_wakeup: secondWakeup, content: 'Background task completed: Browser' },
    { id: 'second-parent-answer', role: 'assistant', content: 'Additional background result' },
  );
  parentEvent('lifecycle', { event: 'completed', thread_id: 'subagents' });
  parentReconnect.end();
  sessionsStream.write(`event: session_stopped\ndata: ${JSON.stringify({ session_id: 'parent-wakeup-2', thread_id: 'subagents' })}\n\n`);
  await until(() => js(`!!document.querySelector('button[aria-label="Send message"]')
    && document.querySelector('.transcript').textContent.includes('Additional background result')`), 'second wakeup finishes');
  await call('Page.reload');
  await until(() => js(`document.querySelectorAll('.transcript-run-divider').length === 2`), 'both wakeup dividers restored after reload');
  assert.deepEqual(await js(`Array.from(document.querySelectorAll('.transcript-run-divider'), el => el.dataset.wakeupId)`),
    ['first-parent-wakeup', 'second-parent-wakeup']);
  assert.equal(await js(`document.querySelector('.transcript').textContent.includes('Background task completed:')`), false);
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: fixed wakeup dividers group parallel results, separate later runs, precede streamed answers and survive checkpoint refreshes and page reloads.');

  for (const task of liveTasks.values()) {
    task.status = 'pending';
    task.version += 1;
    publishTask(task);
  }
  await until(() => js(`document.querySelector('.transcript-async-wait')?.textContent.includes('Waiting for 2 background agents...')`), 'two queued agents show the wait divider');
  for (const width of [360, 768, 1280]) {
    await call('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: false });
    const bounds = await js(`(() => {
      const divider = document.querySelector('.transcript-async-wait');
      const rect = divider.getBoundingClientRect();
      const column = document.querySelector('.transcript').getBoundingClientRect();
      return { left: rect.left, right: rect.right, columnLeft: column.left, columnRight: column.right,
        viewport: innerWidth, pageWidth: document.documentElement.scrollWidth,
        before: getComputedStyle(divider, '::before').borderTopWidth,
        after: getComputedStyle(divider, '::after').borderTopWidth, title: divider.title };
    })()`);
    assert.ok(bounds.left >= bounds.columnLeft && bounds.right <= bounds.columnRight, `Wait divider exceeds the transcript at ${width}px`);
    assert.ok(bounds.pageWidth <= bounds.viewport, `Wait divider causes horizontal overflow at ${width}px`);
    assert.equal(bounds.before, '1px');
    assert.equal(bounds.after, '1px');
    assert.ok(bounds.title.includes('Async first, Async second'));
  }
  firstTask.status = 'completed';
  firstTask.version += 1;
  publishTask(firstTask);
  await until(() => js(`document.querySelector('.transcript-async-wait')?.textContent.includes('Waiting for 1 background agent...')`), 'completed task updates the remaining count');
  secondTask.status = 'completed';
  secondTask.version += 1;
  publishTask(secondTask);
  await until(() => js(`!document.querySelector('.transcript-async-wait')`), 'all completed tasks remove the wait divider');
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: background wait dividers track queued agents, user responses and completion, and fit 360px, 768px and 1280px viewports.');
  asyncTask.status = 'waiting';
  asyncTask.version += 1;
  asyncTask.interrupts = [{ id: 'browser-interrupt', value: { type: 'browser_handoff_request', handoff_id: 'browser-handoff',
    thread_id: asyncTask.thread_id, reason: 'Sign in for the background worker.' } }];
  backgroundVisible = true;
  publishTask(asyncTask);
  await until(() => js(`!!(${card('Background worker')}) && ${detail('Background worker')} === 'Waiting for your response'`), 'background browser request');
  if (!await js(`(${card('Background worker')}).open`)) await toggle('Background worker');
  await until(() => js(`[...(${card('Background worker')}).querySelectorAll('.async-task-controls button')].some(button => button.textContent === 'Open browser')`), 'browser request has an open-browser action');
  assert.equal(await js(`!!(${card('Background worker')}).querySelector('.user-input-request-card')`), false, 'browser handoff must not render a question form');
  await js(`window.browserHandoffRequests = []; window.addEventListener('k41-browser-handoff', event => browserHandoffRequests.push(event.detail));`);
  await js(`[...(${card('Background worker')}).querySelectorAll('.async-task-controls button')].find(button => button.textContent === 'Open browser').click()`);
  assert.deepEqual(await js('browserHandoffRequests'), [{ threadId: asyncTask.parent_thread_id, browserThreadId: asyncTask.thread_id }]);
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: a waiting browser request opens its exact background session without a question form.');
} finally {
  savedResponse?.end();
  stream?.end();
  parentReconnect?.end();
  for (const response of childStreams.values()) response.end();
  for (const response of delayedHistories.values()) response.end(JSON.stringify({ messages: [] }));
  if (call && ws?.readyState === WebSocket.OPEN) await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws?.close();
  browser.kill();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  const resolved = path.resolve(profile);
  assert.equal(path.dirname(resolved), path.resolve(os.tmpdir()));
  assert.ok(path.basename(resolved).startsWith('k41-subagents-'));
  fs.rmSync(resolved, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
}
