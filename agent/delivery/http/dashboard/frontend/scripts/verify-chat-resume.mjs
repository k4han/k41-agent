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
const originalErrorText = 'BadRequestError("Error code: 400 - {\'error\': {\'message\': \'The selected model cannot read images.\'}}")\nRequest ID: original-request-123';
const friendlyErrorText = 'The selected model cannot read images.';

const history = {
  thread_id: 'failed-turn', active_checkpoint_id: 'before-failure', messages: [],
  platform: 'api', user_id: 'dashboard', agent_name: 'default',
  resume_error: null,
};
const fixtures = {
  '/dashboard-api/catalog': { backends: [] },
  '/dashboard-api/agents/cards': { cards: [{ name: 'default', display_name: 'Default', provider: 'test', model: 'demo-model', valid: true }] },
  '/dashboard-api/agents/providers': { provider_names: ['test'], default_provider: 'test', default_model: 'demo-model', model_catalogs: [{ provider: 'test', default_model: 'demo-model', models: [{ id: 'demo-model', label: 'Demo model' }] }] },
  '/dashboard-api/workspace/default': { workspace: null },
  '/dashboard-api/chat-history': { threads: [
    { thread_id: 'failed-turn', title: 'Failed conversation', platform: 'api', user_id: 'dashboard', agent_name: 'default' },
    { thread_id: 'other-turn', title: 'Other conversation', platform: 'api', user_id: 'dashboard', agent_name: 'default' },
  ] },
  '/dashboard-api/chat-history/other-turn': {
    thread_id: 'other-turn', active_checkpoint_id: 'other-checkpoint',
    platform: 'api', user_id: 'dashboard', agent_name: 'default',
    messages: [{ id: 'other-answer', role: 'assistant', content: 'Other conversation answer' }],
    resume_error: null,
  },
};
const sentMessages = [];
let resumeResponse;
let historyRefreshes = 0;
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/api/chat/events') {
    let body = '';
    for await (const chunk of req) body += chunk;
    const payload = JSON.parse(body);
    sentMessages.push(payload);
    res.writeHead(200, { 'Content-Type': 'application/x-ndjson' });
    res.flushHeaders();
    if (payload.resume) {
      resumeResponse = res;
    } else {
      history.active_checkpoint_id = 'failed-checkpoint';
      history.resume_error = {
        checkpoint_id: 'failed-checkpoint', code: 'unknown', content: originalErrorText, message: friendlyErrorText,
      };
      history.messages = [
        { id: 'original-user', role: 'user', content: payload.message },
        { id: 'completed-call', role: 'assistant', content: '', tool_calls: [{ id: 'completed-tool', name: 'read', args: { path: 'source.txt' } }] },
        { id: 'completed-result', role: 'tool', tool_call_id: 'completed-tool', name: 'read', content: 'Already read' },
      ];
      res.end(JSON.stringify({ type: 'error', code: 'unknown', content: originalErrorText, message: friendlyErrorText }) + '\n');
    }
    return;
  }
  if (url.pathname.startsWith('/dashboard-api/') || url.pathname === '/health') {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('X-CSRF-Token', 'resume-test-csrf');
    if (url.pathname === '/dashboard-api/chat-history/failed-turn') {
      historyRefreshes += 1;
      res.end(JSON.stringify(history));
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
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-chat-resume-'));
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
  const continueButton = `document.querySelector('button[aria-label="Continue response"]')`;
  const clickContinue = () => js(`${continueButton}.click()`);
  await call('Page.enable');
  await call('Runtime.enable');
  await call('Page.navigate', { url: base + '/c/failed-turn' });
  await until(() => js(`document.querySelector('.chat-prompt-input')?.disabled === false`), 'loaded conversation');
  await js(`(() => {
    const input = document.querySelector('.chat-prompt-input');
    input.value = 'Finish the original task';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector('button[aria-label="Send message"]').click();
  })()`);
  await until(() => historyRefreshes >= 2, 'failed checkpoint refreshed');
  await until(() => js(`${continueButton}?.disabled === false`), 'inline continuation action after refresh');
  assert.equal(await js('document.querySelectorAll(".message.user").length'), 1);
  assert.equal(await js('document.querySelector(".message.error .message-text").textContent'), friendlyErrorText);
  assert.equal(await js('document.body.textContent.includes("BadRequestError")'), false, 'raw diagnostics must not appear in the transcript or toast');
  await call('Page.reload', { ignoreCache: true });
  await until(() => js(`${continueButton}?.disabled === false`), 'continuation restored after page reload');
  assert.equal(await js('document.querySelectorAll(".message.error").length'), 1, 'restoring must not duplicate the error');
  assert.equal(await js('document.querySelector(".message.error .message-text").textContent'), friendlyErrorText, 'page reload must show only the friendly message');
  await until(() => js(`!!document.querySelector('a[href="/c/other-turn"]')`), 'other conversation link');
  await js(`document.querySelector('a[href="/c/other-turn"]').click()`);
  await until(() => js('document.body.textContent.includes("Other conversation answer")'), 'other conversation loaded');
  assert.equal(await js(`!!${continueButton}`), false, 'completed conversations must not inherit the failed action');
  await js(`document.querySelector('a[href="/c/failed-turn"]').click()`);
  await until(() => js(`${continueButton}?.disabled === false`), 'continuation restored after switching conversations');
  assert.equal(await js('document.querySelector(".message.error .message-text").textContent'), friendlyErrorText, 'reopening must show only the friendly message');
  assert.equal(sentMessages.length, 1, 'reopening must not automatically restart the failed run');
  await js(`(() => {
    const input = document.querySelector('.chat-prompt-input');
    input.value = 'Keep this unsent draft';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    const transfer = new DataTransfer();
    transfer.items.add(new File(['draft'], 'draft.txt', { type: 'text/plain' }));
    const files = document.querySelector('.chat-composer input[type=file]');
    files.files = transfer.files;
    files.dispatchEvent(new Event('change', { bubbles: true }));
  })()`);
  await until(() => js('document.querySelectorAll(".chat-attachment-chip").length === 1'), 'unsent draft attachment');
  await clickContinue();
  await until(() => Boolean(resumeResponse), 'checkpoint continuation request');
  assert.equal(sentMessages.at(-1).resume, true);
  assert.equal(sentMessages.at(-1).message, '');
  assert.equal(sentMessages.at(-1).thread_id, 'failed-turn');
  assert.equal(sentMessages.at(-1).checkpoint_id, undefined, 'resume uses the latest failed checkpoint');
  assert.equal(sentMessages.at(-1).attachments, undefined, 'draft attachments must not be resent');
  assert.equal(await js(`${continueButton}.disabled`), true);
  assert.equal(await js('document.querySelectorAll(".message.user").length'), 1, 'resume must not create a user message');
  const requestCount = sentMessages.length;
  await clickContinue();
  assert.equal(sentMessages.length, requestCount, 'busy continuation cannot start another run');
  const beforeSecondFailureRefresh = historyRefreshes;
  history.resume_error.content = 'TimeoutError("Raw provider diagnostics for the second failure")';
  history.resume_error.code = 'timeout';
  history.resume_error.message = 'Provider timed out again. Please try again.';
  resumeResponse.end(JSON.stringify({ type: 'error', code: 'timeout', content: history.resume_error.content, message: history.resume_error.message }) + '\n');
  resumeResponse = null;
  await until(() => historyRefreshes > beforeSecondFailureRefresh, 'second failure refreshed');
  await until(() => js(`${continueButton}?.disabled === false
    && document.querySelectorAll('.message.error').length === 1
    && document.querySelector('.message.error').textContent.includes('Provider timed out again')`), 'continuation enabled after another failure');
  await clickContinue();
  await until(() => Boolean(resumeResponse), 'second continuation request');
  const beforeSuccessRefresh = historyRefreshes;
  history.resume_error = null;
  history.messages.push({ id: 'recovered-answer', role: 'assistant', content: 'Task recovered successfully' });
  resumeResponse.end(JSON.stringify({ type: 'final', content: 'Task recovered successfully' }) + '\n');
  resumeResponse = null;
  await until(() => historyRefreshes > beforeSuccessRefresh, 'recovered checkpoint refreshed');
  await until(() => js('document.body.textContent.includes("Task recovered successfully") && !document.querySelector(".message.error")'), 'success clears continuation action');
  assert.equal(await js('document.querySelectorAll(".message.user").length'), 1);
  assert.equal(await js('document.querySelector(".chat-prompt-input").value'), 'Keep this unsent draft');
  assert.equal(await js('document.querySelectorAll(".chat-attachment-chip").length'), 1);
  await call('Page.reload', { ignoreCache: true });
  await until(() => js('document.body.textContent.includes("Task recovered successfully")'), 'completed conversation reloaded');
  assert.equal(await js(`!!${continueButton}`), false, 'completed runs must stay completed after reload');
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: continuation survives reloads and conversation switches, resumes repeated failures, and stays cleared after completion.');
} finally {
  resumeResponse?.end();
  if (call && ws?.readyState === WebSocket.OPEN) await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws?.close();
  browser.kill();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  const resolved = path.resolve(profile);
  assert.equal(path.dirname(resolved), path.resolve(os.tmpdir()));
  assert.ok(path.basename(resolved).startsWith('k41-chat-resume-'));
  fs.rmSync(resolved, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
}
