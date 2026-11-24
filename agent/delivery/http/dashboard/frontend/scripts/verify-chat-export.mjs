import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { protocolFixture } from './protocol-fixture.mjs';

const staticRoot = fileURLToPath(new URL('../../static/', import.meta.url));
const executable = process.env.BROWSER_EXECUTABLE || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].find(candidate => fs.existsSync(candidate));
assert.ok(executable, 'Set BROWSER_EXECUTABLE to run browser checks.');

const threadId = 'api/user/channel/default';
const branchOptions = [{ checkpoint_id: 'older', message: 'Original prompt' }, { checkpoint_id: 'latest', message: 'Revised prompt' }];
const history = {
  thread_id: threadId, title: 'Project notes', active_checkpoint_id: 'latest', platform: 'api', user_id: 'dashboard',
  channel_id: 'channel', agent_name: 'default', provider: 'test', model: 'model-a', workspace: null,
  messages: [
    { id: 'user', role: 'user', content: 'Revised prompt', message_index: 0, source_checkpoint_id: 'latest',
      branch: { current: 2, total: 2, options: branchOptions },
      attachments: [{ name: 'notes.txt', mime_type: 'text/plain', size: 5, kind: 'text', content: 'Notes' }] },
    { id: 'call', role: 'assistant', content: '', tool_calls: [{ id: 'read-1', name: 'read_file', args: { path: 'notes.txt' } }] },
    { id: 'result', role: 'tool', name: 'read_file', tool_call_id: 'read-1', content: 'Tool output' },
    { id: 'answer', role: 'assistant', content: 'Latest answer' },
  ],
};
const older = { ...history, active_checkpoint_id: 'older', messages: [
  { ...history.messages[0], content: 'Original prompt', source_checkpoint_id: 'older', branch: { current: 1, total: 2, options: branchOptions } },
  { id: 'answer-old', role: 'assistant', content: 'Original answer' },
] };
const fixtures = {
  '/dashboard-api/catalog': { backends: [] },
  '/dashboard-api/agents/cards': { cards: [
    { name: 'default', display_name: 'Default', provider: 'test', model: 'model-a', valid: true },
  ] },
  '/dashboard-api/agents/providers': {
    provider_names: ['test'], default_provider: 'test', default_model: 'model-a',
    model_catalogs: [{ provider: 'test', default_model: 'model-a', models: [{ id: 'model-a', label: 'Model A' }] }],
  },
  '/dashboard-api/workspace/default': { workspace: null },
  [`/dashboard-api/workspace/runtime/${encodeURIComponent(threadId)}`]: { workspace: null, runtime: null },
  '/dashboard-api/workspace/tree': { root: '', path: '', entries: [], truncated: false },
  '/dashboard-api/workspace/changes': { changes: [], is_git_repo: false },
  [`/dashboard-api/browser/threads/${encodeURIComponent(threadId)}`]: {
    open: false, profile_id: null, generation: null, mode: 'agent', tabs: [], handoff: null, handoff_expired: false, images: [],
  },
  '/dashboard-api/browser/profiles': { profiles: [] },
  [`/dashboard-api/usage/thread/${encodeURIComponent(threadId)}`]: {
    thread_id: threadId, total_tokens: 120000, input_tokens: 100000, output_tokens: 20000,
    latest_input_tokens: 1500, latest_output_tokens: 100, current_context_tokens: 1600,
    has_context_usage: true, context_window: 128000, models: [],
    estimated_cost_usd: 0.36, cost_complete: true, host_platform: 'Linux',
  },
  '/dashboard-api/chat-history': { threads: [history] },
  '/dashboard-api/nav-projects': { projects: [{ key: 'no-workspace', label: 'No workspace', threads: [history] }], has_more: false, next_cursor: null },
};
const historyRequests = [];
const writes = [];
const edits = [];
const historyUnavailable = 'History is temporarily unavailable.';
let historyError;
let holdHistory = false;
let heldHistory;
let failRename = false;
let paginateHistory = false;
let historyPageSize = 2;
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/dashboard-api/chat-presence') {
    res.writeHead(204).end();
    return;
  }
  if (req.method !== 'GET') writes.push(req.url);
  if (url.pathname === '/v2/api/chat/events/edit') {
    let body = '';
    for await (const chunk of req) body += chunk;
    const payload = JSON.parse(body);
    edits.push(payload);
    history.active_checkpoint_id = 'regenerated';
    history.messages = [
      ...history.messages.slice(0, payload.message_index + 1),
      { id: 'regenerated-answer', role: 'assistant', content: 'Regenerated answer',
        message_index: payload.message_index + 1, source_checkpoint_id: 'regenerated' },
    ];
    res.writeHead(200, { 'Content-Type': 'application/x-ndjson' });
    protocolFixture(res, threadId);
    res.end(JSON.stringify({ type: 'message', content: 'Regenerated answer' }) + '\n');
    return;
  }
  if (url.pathname === '/dashboard-api/sessions/events') {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write('event: snapshot\ndata: {"sessions":[]}\n\n');
    return;
  }
  if (url.pathname.startsWith('/dashboard-api/')) {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('X-CSRF-Token', 'export-test-csrf');
    if (decodeURIComponent(url.pathname) === `/dashboard-api/chat-history/${threadId}`) {
      if (req.method === 'PATCH') {
        let body = '';
        for await (const chunk of req) body += chunk;
        if (failRename) { res.writeHead(503).end(JSON.stringify({ detail: 'Rename is temporarily unavailable.' })); return; }
        history.title = older.title = JSON.parse(body).title;
        res.end(JSON.stringify(history));
        return;
      }
      const checkpointId = url.searchParams.get('checkpoint_id');
      const before = url.searchParams.get('before');
      historyRequests.push({ checkpointId, before });
      if (historyError) {
        res.writeHead(503).end(JSON.stringify({ detail: historyError }));
        return;
      }
      let payload = checkpointId === 'older' ? older : history;
      payload = { ...payload, total_user_messages: payload.messages.filter(message => message.role === 'user').length };
      if (paginateHistory) {
        const end = before === null ? payload.messages.length : Number(before);
        const start = Math.max(0, end - historyPageSize);
        payload = { ...payload, messages: payload.messages.slice(start, end),
          total_messages: payload.messages.length, has_more: start > 0, next_before: start || null };
      }
      if (holdHistory) { heldHistory = { res, payload }; return; }
      res.end(JSON.stringify(payload));
    } else {
      res.end(JSON.stringify(fixtures[url.pathname] || { sessions: [], threads: [], tools: [] }));
    }
    return;
  }
  const relative = url.pathname.startsWith('/dashboard-assets/') ? url.pathname.slice('/dashboard-assets/'.length) : 'index.html';
  const target = path.resolve(staticRoot, relative);
  if (!target.startsWith(path.resolve(staticRoot) + path.sep)) { res.writeHead(403).end(); return; }
  try {
    res.setHeader('Content-Type', { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' }[path.extname(target)] || 'application/octet-stream');
    res.end(fs.readFileSync(target));
  } catch { res.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-chat-export-'));
const downloads = path.join(profile, 'exports');
fs.mkdirSync(downloads);
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
  const historyLoading = () => js('!!document.querySelector(".transcript-history-loading[role=status]")');
  const transcriptPosition = () => js(`(() => {
    const transcript = document.querySelector(".transcript");
    return { top: transcript.scrollTop, max: transcript.scrollHeight - transcript.clientHeight };
  })()`);
  const transcriptPoint = () => js(`(() => {
    const { left, top, width, height } = document.querySelector(".transcript").getBoundingClientRect();
    return { x: Math.round(left + width / 2), y: Math.round(top + height / 3) };
  })()`);
  const keyboardUp = async () => {
    await js('document.querySelector(".transcript").focus({ preventScroll: true })');
    const key = { key: 'Home', code: 'Home', windowsVirtualKeyCode: 36 };
    await call('Input.dispatchKeyEvent', { type: 'keyDown', ...key });
    await call('Input.dispatchKeyEvent', { type: 'keyUp', ...key });
  };
  const releaseHistory = () => {
    holdHistory = false;
    const { res, payload } = heldHistory;
    heldHistory = undefined;
    res.end(JSON.stringify(payload));
  };
  const openMenu = async () => {
    await until(() => js(`!!document.querySelector('.chat-header button[aria-label="Conversation actions"]')`), 'conversation actions available');
    await js(`document.querySelector('.chat-header button[aria-label="Conversation actions"]').click()`);
    await until(() => js('!!document.querySelector(".chat-actions-menu")'), 'conversation menu');
  };
  const userMessageCount = async () => {
    await openMenu();
    const count = await js('Number(document.querySelectorAll(".chat-session-insights dd")[1].textContent)');
    await js('document.querySelector(".chat-actions-menu").dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))');
    return count;
  };
  const openExport = async () => {
    await openMenu();
    await until(() => js(`Array.from(document.querySelectorAll('.chat-actions-menu button')).find(button => button.textContent.includes('Export conversation'))?.disabled === false`), 'export available');
    await js(`Array.from(document.querySelectorAll('.chat-actions-menu button')).find(button => button.textContent.includes('Export conversation')).click()`);
    await until(async () => {
      const state = await js('({ title: document.querySelector(".dialog-title")?.textContent, error: document.querySelector(".content[role=alert]")?.textContent })');
      if (state.title === 'Export conversation') return true;
      if (state.error) throw new Error(state.error);
      return false;
    }, 'export dialog');
  };
  const download = () => js('document.querySelector(".dialog-footer .btn-primary").click()');
  const exportedFiles = () => fs.readdirSync(downloads).filter(name => /\.(md|txt|json)$/.test(name));
  const exportFile = async (format) => {
    const before = new Set(exportedFiles());
    await openExport();
    await js(`(() => { const select = document.querySelector('.dialog select'); select.value = '${format}'; select.dispatchEvent(new Event('change', { bubbles: true })); })()`);
    await download();
    await until(() => exportedFiles().some(name => !before.has(name)), `download ${format}`);
    await until(() => js('!document.querySelector(".dialog")'), 'close successful export');
    return fs.readFileSync(path.join(downloads, exportedFiles().find(name => !before.has(name))), 'utf8');
  };

  await call('Page.enable');
  await call('Runtime.enable');
  await call('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: downloads });
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await call('Page.navigate', { url: `${base}/c/${encodeURIComponent(threadId)}` });
  await openMenu();
  await until(() => js('document.querySelector(".chat-session-insights")?.textContent.includes("$0.36")'), 'session cost loaded');
  assert.deepEqual(await js(`Array.from(document.querySelector('.chat-header-right').children).map(element => element.className)`), [
    'composer-workspace-picker', 'chat-header-workspace-btn chat-actions-trigger is-open', 'chat-header-workspace-btn',
  ], 'project picker, horizontal actions menu, workspace toggle');
  assert.ok((await js('document.querySelector(".chat-session-insights").textContent')).includes('User messages:1'));
  assert.ok((await js('document.querySelector(".chat-session-insights").textContent')).includes('Session size:XS'));
  assert.ok((await js('document.querySelector(".chat-session-insights").textContent')).includes('Platform:Linux'));
  assert.equal(await js('document.querySelector(".chat-actions-menu").lastElementChild.className'), 'chat-session-insights');
  if (process.env.CHAT_MENU_SCREENSHOT) {
    await js('document.documentElement.classList.add("dark")');
    const shot = await call('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.resolve(process.env.CHAT_MENU_SCREENSHOT), Buffer.from(shot.data, 'base64'));
  }
  await js('document.querySelector(".chat-actions-menu").dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))');
  assert.equal(await js('document.activeElement?.classList.contains("chat-actions-trigger")'), true, 'Escape restores trigger focus');
  for (const format of ['markdown', 'text', 'json']) {
    const content = await exportFile(format);
    assert.equal(historyRequests.at(-1).checkpointId, 'latest');
    if (format === 'json') {
      const data = JSON.parse(content);
      assert.equal(data.thread_id, threadId);
      assert.equal(data.title, history.title);
      assert.equal(data.messages.length, history.messages.length);
      assert.equal(data.messages[0].attachments[0].content, 'Notes');
      assert.equal(data.messages[0].branch, undefined);
    } else {
      assert.ok(content.includes('Revised prompt'));
      assert.ok(content.includes('Latest answer'));
      assert.ok(content.includes('Notes'));
      assert.ok(!content.includes('Tool output'));
    }
  }
  await openExport();
  await js(`(() => { const select = document.querySelector('.dialog select'); select.value = 'text'; select.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await js('document.querySelector(".dialog input[type=checkbox]").click()');
  const beforeTools = new Set(exportedFiles());
  await download();
  await until(() => exportedFiles().some(name => !beforeTools.has(name)), 'download tool activity');
  await until(() => js('!document.querySelector(".dialog")'), 'close tool activity export');
  assert.ok(fs.readFileSync(path.join(downloads, exportedFiles().find(name => !beforeTools.has(name))), 'utf8').includes('Tool output'));

  paginateHistory = true;
  const requestsBeforeReload = historyRequests.length;
  await call('Page.reload');
  await until(() => js('document.querySelector(".transcript")?.textContent.includes("Latest answer")'), 'paginated history loaded');
  assert.deepEqual(historyRequests.slice(requestsBeforeReload), [{ checkpointId: null, before: null }]);
  assert.equal(await js(`Array.from(document.querySelectorAll('button')).some(button => button.textContent === 'Load earlier messages')`), false);
  assert.equal(await js(`document.querySelector('.transcript').textContent.includes('Revised prompt')`), false);
  assert.equal((await transcriptPosition()).max, 0, 'the latest page fits in the viewport');
  assert.equal(await userMessageCount(), 1, 'session count includes prompts outside the loaded page');
  const requestsBeforeExport = historyRequests.length;
  const paginatedExport = JSON.parse(await exportFile('json'));
  assert.deepEqual(paginatedExport.messages.map(message => message.id), history.messages.map(message => message.id));
  assert.equal(historyRequests.length - requestsBeforeExport, 2, 'export reads every history page');
  assert.deepEqual(historyRequests.at(-1), { checkpointId: 'latest', before: '2' });
  const requestsBeforeScroll = historyRequests.length;
  historyError = 'Earlier messages are temporarily unavailable.';
  await keyboardUp();
  await until(() => js('document.body.textContent.includes("Earlier messages are temporarily unavailable.")'), 'show earlier page error');
  assert.equal(await historyLoading(), false);
  historyError = undefined;
  holdHistory = true;
  await keyboardUp();
  await until(() => !!heldHistory, 'retry the earlier page');
  await until(historyLoading, 'show the earlier history spinner');
  await keyboardUp();
  await delay(150);
  assert.deepEqual(historyRequests.slice(requestsBeforeScroll), [
    { checkpointId: 'latest', before: '2' },
    { checkpointId: 'latest', before: '2' },
  ], 'only one request stays in flight');
  releaseHistory();
  await until(() => js(`document.querySelector('.transcript').textContent.includes('Revised prompt')`), 'earlier messages rendered');
  assert.equal(await historyLoading(), false);
  await keyboardUp();
  await delay(150);
  assert.equal(historyRequests.length - requestsBeforeScroll, 2, 'reaching the beginning stops pagination');
  assert.equal(await userMessageCount(), 1, 'loading older pages does not change the session count');

  await js(`document.querySelector('button[aria-label="Previous branch"]').click()`);
  await until(() => js('document.querySelector(".message-branch-count")?.textContent.trim() === "1/2"'), 'older branch loaded');
  const data = JSON.parse(await exportFile('json'));
  assert.equal(historyRequests.at(-1).checkpointId, 'older');
  assert.equal(data.active_checkpoint_id, 'older');
  assert.equal(data.messages.at(-1).content, 'Original answer');

  await openExport();
  historyError = historyUnavailable;
  await download();
  await until(() => js('document.querySelector(".dialog [role=alert]")?.textContent.includes("temporarily unavailable")'), 'show export error');
  assert.equal(await js('document.querySelector(".dialog-footer .btn-primary").disabled'), false);
  historyError = undefined;
  const beforeRetry = exportedFiles().length;
  await download();
  await until(() => exportedFiles().length > beforeRetry, 'retry successful export');
  await until(() => js('!document.querySelector(".dialog")'), 'close after retry');

  await openExport();
  holdHistory = true;
  await download();
  await until(() => !!heldHistory, 'delayed export request');
  assert.equal(await js('document.querySelector(".dialog-footer .btn-primary").disabled'), true);
  const beforeCancel = exportedFiles().length;
  await js('document.querySelector(".dialog-close-btn").click()');
  holdHistory = false;
  heldHistory.res.end(JSON.stringify(heldHistory.payload));
  heldHistory = undefined;
  await delay(200);
  assert.equal(exportedFiles().length, beforeCancel, 'closing cancels the pending download');
  assert.deepEqual(writes, [], 'export never mutates the conversation');

  const openRename = async () => {
    await openMenu();
    await js(`Array.from(document.querySelectorAll('.chat-actions-menu button')).find(button => button.textContent.includes('Rename conversation')).click()`);
    await until(() => js('document.querySelector(".dialog-title")?.textContent === "Rename conversation"'), 'rename dialog');
  };
  await openRename();
  await js(`(() => { const input = document.querySelector('.dialog input'); input.value = 'Renamed conversation'; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  failRename = true;
  await js('document.querySelector(".dialog form").requestSubmit()');
  await until(() => js('document.querySelector(".dialog [role=alert]")?.textContent.includes("temporarily unavailable")'), 'rename failure stays in dialog');
  assert.equal(history.title, 'Project notes');
  failRename = false;
  await js('document.querySelector(".dialog form").requestSubmit()');
  await until(() => js('!document.querySelector(".dialog")'), 'rename retry succeeds');
  await until(() => js('document.querySelector(".chat-header-title")?.textContent.includes("Renamed conversation")'), 'header reflects rename');
  assert.equal(history.title, 'Renamed conversation');

  await navigate('/history');
  await until(() => js('!!document.querySelector(".history-thread-link")'), 'history loaded');
  await js(`document.querySelector('.thread-row-actions button[aria-label="More actions"]').click()`);
  await until(() => js('!!document.querySelector(".thread-action-menu")'), 'history row menu');
  await js('Array.from(document.querySelectorAll(".thread-action-item")).find(button => button.textContent.trim() === "Export").click()');
  await until(() => js('document.querySelector(".dialog-title")?.textContent === "Export conversation"'), 'export from history');
  await js('document.querySelector(".dialog-close-btn").click()');
  await until(() => js('!!document.querySelector(".nav-history-action")'), 'navigation loaded');
  await js('document.querySelector(".nav-history-action").click()');
  await until(() => js('!!document.querySelector(".nav-history-menu--portal")'), 'thread menu');
  await js('Array.from(document.querySelectorAll(".nav-history-menu-item")).find(button => button.textContent.trim() === "Export").click()');
  await until(() => js('document.querySelector(".dialog-title")?.textContent === "Export conversation"'), 'export from navigation');
  assert.equal(await js('!!document.querySelector(".nav-history-menu--portal")'), false);
  await js('document.querySelector(".dialog-close-btn").click()');

  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await navigate(`/c/${encodeURIComponent(threadId)}`);
  await openMenu();
  assert.equal(await js('(() => { const rect = document.querySelector(".chat-actions-menu").getBoundingClientRect(); return rect.left >= 0 && rect.right <= innerWidth; })()'), true, 'actions fit mobile viewport');
  await js('document.querySelector(".chat-actions-menu").dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))');
  await openExport();
  assert.equal(await js('(() => { const rect = document.querySelector(".dialog").getBoundingClientRect(); return rect.left >= 0 && rect.right <= innerWidth; })()'), true, 'dialog fits mobile viewport');
  await js('document.querySelector(".dialog-close-btn").click()');
  await call('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 1 });
  const touchPoint = await transcriptPoint();
  await call('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [touchPoint] });
  await call('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ ...touchPoint, y: touchPoint.y + 80 }] });
  await call('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await until(() => js('document.querySelector(".transcript").textContent.includes("Revised prompt")'), 'touch gesture loads older history');
  await call('Emulation.setTouchEmulationEnabled', { enabled: false });
  await navigate('/chat');
  await until(() => js('!!document.querySelector(".chat-header")'), 'new chat loaded');
  await openMenu();
  assert.equal(await js('Array.from(document.querySelectorAll(".chat-actions-menu > button")).every(button => button.disabled)'), true, 'new chats disable conversation actions');
  assert.ok((await js('document.querySelector(".chat-session-insights").textContent')).includes('Unavailable'));
  assert.equal(writes.length, 2, 'only the explicit rename attempts write to the server');

  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  historyPageSize = 8;
  history.active_checkpoint_id = 'scroll-checkpoint';
  history.messages = Array.from({ length: 24 }, (_, index) => ({
    id: index === 8 ? null : `scroll-message-${index}`,
    role: index % 2 ? 'assistant' : 'user',
    content: [`History message ${index}`, ...Array.from({ length: 5 + (index % 5) }, () => 'A paragraph from this conversation.')].join('\n\n'),
  }));
  history.messages.splice(14, 3,
    { id: 'history-call', role: 'assistant', content: '', tool_calls: [
      { id: 'read-first', name: 'read_file', args: { path: 'first.txt' } },
      { id: 'read-second', name: 'read_file', args: { path: 'second.txt' } },
    ] },
    { id: 'history-first-result', role: 'tool', name: 'read_file', tool_call_id: 'read-first', content: 'First result' },
    { id: 'history-second-result', role: 'tool', name: 'read_file', tool_call_id: 'read-second', content: 'Second result' },
  );
  const threadPath = `/c/${encodeURIComponent(threadId)}`;
  await navigate(threadPath);
  await until(() => js('document.querySelector(".transcript").textContent.includes("History message 23")'), 'latest page is rendered');
  await until(async () => {
    const { top, max } = await transcriptPosition();
    return max > 0 && Math.abs(top - max) <= 1;
  }, 'the latest page starts at the bottom');
  const firstRow = selector => js(`(() => {
    const transcript = document.querySelector(".transcript");
    const row = transcript.querySelector(${JSON.stringify(selector)});
    return row ? { top: row.getBoundingClientRect().top - transcript.getBoundingClientRect().top,
      id: row.dataset.transcriptItemId ?? null } : null;
  })()`);
  holdHistory = true;
  await call('Input.dispatchMouseEvent', { type: 'mouseWheel', ...await transcriptPoint(), deltaX: 0, deltaY: -10000 });
  await until(() => !!heldHistory, 'wheel at the top requests the previous page');
  await until(() => firstRow('.activity-group'), 'leading tool group is rendered');
  const toolPosition = await firstRow('.activity-group');
  assert.deepEqual(historyRequests.at(-1), { checkpointId: 'scroll-checkpoint', before: '16' });
  releaseHistory();
  await until(async () => {
    const group = await firstRow('.activity-group');
    return group && Math.abs(group.top - toolPosition.top) <= 2 && (await transcriptPosition()).top > 1;
  }, 'an activity group keeps its position across the page boundary');

  holdHistory = true;
  await js('document.querySelector(".transcript").scrollTop = 0');
  await until(() => !!heldHistory, 'scrolling back to the top requests the next page');
  await until(() => js('document.querySelector(".transcript .message")?.textContent.includes("History message 8")'), 'first row of the previous page is visible');
  await js('document.querySelector(".transcript").scrollTop = 64');
  const messagePosition = await firstRow('.message');
  assert.deepEqual(historyRequests.at(-1), { checkpointId: 'scroll-checkpoint', before: '8' });
  releaseHistory();
  await until(async () => {
    const message = await firstRow(`[data-transcript-item-id="${messagePosition.id}"]`);
    return message && Math.abs(message.top - messagePosition.top) <= 2 && (await transcriptPosition()).top > 1;
  }, 'a saved message without an ID keeps its identity and position');

  await navigate('/chat');
  await navigate(threadPath);
  await until(() => js('document.querySelector(".transcript").textContent.includes("History message 23")'), 'latest page opens again');
  holdHistory = true;
  await keyboardUp();
  await until(() => !!heldHistory, 'a history page is loading before navigation');
  await navigate('/chat');
  await until(() => heldHistory.res.destroyed, 'switching conversations aborts the previous request');
  releaseHistory();
  await until(() => js('!!document.querySelector(".chat-welcome-hero")'), 'new chat after cancellation');
  assert.equal(await historyLoading(), false);

  historyPageSize = 100;
  history.active_checkpoint_id = 'long-checkpoint';
  const previousTurns = Array.from({ length: 126 }, (_, index) => [
    { id: `user-${index}`, role: 'user', content: `Question ${index}`,
      message_index: index * 2, source_checkpoint_id: 'prior-checkpoint' },
    { id: `answer-${index}`, role: 'assistant', content: `Answer ${index}`,
      message_index: index * 2 + 1, source_checkpoint_id: 'prior-checkpoint' },
  ]).flat();
  const promptIndex = previousTurns.length;
  history.messages = [
    ...previousTurns,
    { id: 'long-prompt', role: 'user', content: 'Long original question',
      message_index: promptIndex, source_checkpoint_id: 'prompt-checkpoint' },
    ...Array.from({ length: 110 }, (_, index) => [
      { id: `long-call-${index}`, role: 'assistant', content: '',
        tool_calls: [{ id: `long-tool-${index}`, name: 'read_file', args: { path: `file-${index}` } }] },
      { id: `long-result-${index}`, role: 'tool', tool_call_id: `long-tool-${index}`,
        name: 'read_file', content: `Result ${index}` },
    ]).flat(),
    { id: 'long-answer', role: 'assistant', content: 'Long final answer',
      message_index: promptIndex + 221, source_checkpoint_id: 'long-checkpoint' },
  ];
  await navigate(`/c/${encodeURIComponent(threadId)}`);
  await until(() => js('document.querySelector(\'button[aria-label="Regenerate response"]\')?.disabled === false'), 'long turn loaded');
  assert.equal(await js('document.querySelector(".transcript").textContent.includes("Long original question")'), false);
  assert.equal(await userMessageCount(), 127, 'all 127 user messages are counted with none on the latest page');
  historyError = historyUnavailable;
  await js('document.querySelector(\'button[aria-label="Regenerate response"]\').click()');
  await until(() => js('document.body.textContent.includes("History is temporarily unavailable.")'), 'regeneration history error');
  assert.equal(edits.length, 0, 'failed prompt loading never starts a new run');
  historyError = undefined;
  const requestsBeforeRegeneration = historyRequests.length;
  await js('document.querySelector(\'button[aria-label="Regenerate response"]\').click()');
  await until(() => edits.length === 1, 'regeneration finds the original prompt');
  assert.equal(edits[0].message, 'Long original question');
  assert.equal(edits[0].message_index, promptIndex);
  assert.equal(edits[0].source_checkpoint_id, 'prompt-checkpoint');
  assert.deepEqual(historyRequests.slice(requestsBeforeRegeneration).filter(request => request.before !== null), [
    { checkpointId: 'long-checkpoint', before: '374' },
    { checkpointId: 'long-checkpoint', before: '274' },
  ]);
  await until(() => js('document.querySelector(".transcript").textContent.includes("Regenerated answer")'), 'regenerated answer rendered');
  assert.equal(await userMessageCount(), 127, 'regeneration preserves the total user message count');
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: conversation menus, scroll-triggered history, session counts, long-turn regeneration, complete exports, selected branches, retries, cancellation, and mobile layout.');
} finally {
  heldHistory?.res.end();
  if (call && ws?.readyState === WebSocket.OPEN) await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws?.close();
  browser.kill();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  const resolved = path.resolve(profile);
  assert.equal(path.dirname(resolved), path.resolve(os.tmpdir()));
  assert.ok(path.basename(resolved).startsWith('k41-chat-export-'));
  fs.rmSync(resolved, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
}
