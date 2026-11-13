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
  '/dashboard-api/nav-projects': { projects: [{ key: 'no-workspace', label: 'No workspace', threads }], has_more: false, next_cursor: null },
};
const historyRequests = new Map();
let savedResponse;
let stream;
let sequence = 0;
const send = (method, data, namespace = []) => {
  stream.write(JSON.stringify({ type: 'event', method, params: { namespace, data, timestamp: Date.now() },
    seq: ++sequence, event_id: `subagents:${sequence}` }) + '\n');
};
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
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
        const reference = document.querySelector('.transcript > .message').getBoundingClientRect();
        return Array.from(document.querySelectorAll('.transcript > .subagent-details')).map(el => {
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
} finally {
  savedResponse?.end();
  stream?.end();
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
