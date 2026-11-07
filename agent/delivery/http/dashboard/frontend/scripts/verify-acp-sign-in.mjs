import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const staticRoot = fileURLToPath(new URL('../../static', import.meta.url));
const executable = process.env.BROWSER_EXECUTABLE || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].find(candidate => fs.existsSync(candidate));
assert.ok(executable, 'Set BROWSER_EXECUTABLE to run browser checks.');

let polls = 0;
let reversed = false;
let attempts = 0;
let attempt = null;
let completion;
const submissions = [];
const agent = (id, signIn = null) => ({
  id, display_name: id, terms_url: '', installed: true, version: `1.0.${polls}`, license: 'proprietary', job: null,
  auth: { signed_in: false, state: 'signed_out', methods: [
    { id: 'oauth', name: 'Browser sign-in', description: '' },
    { id: 'gemini-api-key', name: 'API key', description: '' },
  ] },
  sign_in: signIn,
});
const fixtures = {
  '/dashboard-api/nav-projects': { projects: [], has_more: false, next_cursor: null },
  '/dashboard-api/catalog': {},
  '/dashboard-api/sessions': { sessions: [] },
  '/dashboard-api/chat-history': { threads: [], has_more: false },
  '/dashboard-api/workspace/default': { workspace: null },
};
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname.startsWith('/dashboard-api') || url.pathname === '/health') {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('X-CSRF-Token', 'test-csrf');
    if (url.pathname === '/dashboard-api/acp/agents') {
      polls++;
      const agents = [agent('antigravity', attempt), agent('other')];
      res.end(JSON.stringify({ agents: reversed ? agents.reverse() : agents }));
      return;
    }
    if (req.method === 'POST') {
      const body = await new Promise(resolve => {
        let value = '';
        req.on('data', chunk => value += chunk);
        req.on('end', () => resolve(JSON.parse(value || '{}')));
      });
      assert.equal(req.headers['x-csrf-token'], 'test-csrf');
      if (url.pathname.endsWith('/sign-in')) {
        attempt = {
          attempt_id: `attempt-${++attempts}`, method_id: body.method_id, status: 'pending', error: '',
          authorization_url: `https://example.com/authorize?state=state-${attempts}`,
          expires_at: Date.now() / 1000 + 300,
        };
        res.end(JSON.stringify(attempt));
        return;
      }
      if (url.pathname.endsWith('/complete')) {
        submissions.push({ path: url.pathname, ...body });
        completion = res;
        return;
      }
      if (url.pathname.endsWith('/cancel')) {
        attempt = { ...attempt, status: 'cancelled', authorization_url: '' };
        res.end(JSON.stringify(attempt));
        return;
      }
    }
    res.end(JSON.stringify(fixtures[url.pathname] || {}));
    return;
  }
  const relative = url.pathname.startsWith('/dashboard-assets/') ? url.pathname.slice('/dashboard-assets/'.length) : 'index.html';
  const target = path.resolve(staticRoot, relative);
  if (!target.startsWith(staticRoot + path.sep)) { res.writeHead(403); res.end(); return; }
  try {
    const types = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' };
    res.setHeader('Content-Type', types[path.extname(target)] || 'application/octet-stream');
    res.end(fs.readFileSync(target));
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-acp-ui-'));
const browser = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run',
  '--disable-background-networking', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'],
{ windowsHide: true, stdio: 'ignore' });
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
  const panel = `document.querySelector('section[aria-label="antigravity"]')`;
  const callback = `document.querySelector('textarea[aria-label="Sign-in callback address"]')`;
  const button = text => `Array.from(${panel}.querySelectorAll('button')).find(button => button.textContent.trim() === ${JSON.stringify(text)})`;
  await call('Page.enable');
  await call('Runtime.enable');
  await call('Page.navigate', { url: base + '/settings/acp' });
  await until(() => js(`!!${panel}`), 'ACP agent panel');
  await js(`${button('Browser sign-in')}.click()`);
  await until(() => js(`!!${callback} && !${callback}.disabled`), 'callback input');
  const value = 'http://127.0.0.1:12345/?state=test-state&code=test-code';
  await js(`window.originalCallback = ${callback}; originalCallback.focus()`);
  await call('Input.insertText', { text: value });
  await js('originalCallback.setSelectionRange(12, 19)');
  const beforePolls = polls;
  await until(() => polls >= beforePolls + 3, 'three polling snapshots');
  // Wait for the browser to consume the last snapshot, not just for the server to send it.
  await until(() => js(`${panel}.textContent.includes('Version 1.0.${polls},')`), 'latest snapshot applied');
  assert.equal(await js(`${callback} === originalCallback`), true, 'Polling must retain the input node');
  assert.equal(await js('document.activeElement === originalCallback'), true, 'Polling must retain focus');
  assert.deepEqual(await js('[originalCallback.value, originalCallback.selectionStart, originalCallback.selectionEnd]'), [value, 12, 19]);

  reversed = true;
  await until(() => js(`document.querySelector('section.panel')?.getAttribute('aria-label') === 'other'`), 'reordered snapshot');
  assert.equal(await js(`${callback} === originalCallback && originalCallback.value === ${JSON.stringify(value)}`), true, 'Form state must follow the agent ID when agents reorder');

  await js(`${button('Complete sign-in')}.click()`);
  await until(() => submissions.length === 1, 'callback submission');
  const duringSubmit = polls;
  await until(() => polls >= duringSubmit + 2, 'polling during submission');
  assert.equal(await js(`${callback}.disabled`), true, 'Polling must preserve busy state');
  assert.equal(await js(`${button('Complete sign-in')}.disabled`), true);
  assert.equal(await js(`${callback}.value`), '', 'Submitted callbacks must be cleared');
  assert.deepEqual(submissions, [{ path: '/dashboard-api/acp/agents/antigravity/sign-in/attempt-1/complete', callback_url: value }]);
  completion.writeHead(400);
  completion.end(JSON.stringify({ detail: 'Invalid callback URL.' }));
  await until(() => js(`!${callback}.disabled && document.body.textContent.includes('Invalid callback URL.')`), 'callback rejection');

  await js(`${callback}.focus()`);
  await call('Input.insertText', { text: 'old-attempt-address' });
  await js(`${button('Cancel')}.click()`);
  await until(() => js(`!${callback} && !${button('Browser sign-in')}.disabled`), 'cancelled sign-in');
  await js(`${button('Browser sign-in')}.click()`);
  await until(() => js(`!!${callback} && !${callback}.disabled`), 'new sign-in attempt');
  assert.equal(await js(`${callback}.value`), '', 'A new attempt must not reuse the old callback');
  attempt = { ...attempt, status: 'succeeded', authorization_url: '' };
  await until(() => js(`!${callback}`), 'completed attempt removes callback form');
  assert.deepEqual(exceptions, []);
  console.log('PASS: ACP polling preserves input, focus, selection and busy state; forms follow agent IDs after reordering; submission, rejection, cancellation and completion work.');
} finally {
  completion?.end();
  if (call && ws?.readyState === WebSocket.OPEN) await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws?.close();
  browser.kill();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  const resolvedProfile = path.resolve(profile);
  assert.equal(path.dirname(resolvedProfile), path.resolve(os.tmpdir()));
  assert.ok(path.basename(resolvedProfile).startsWith('k41-acp-ui-'));
  try { fs.rmSync(resolvedProfile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); }
  catch (error) {
    if (!['EPERM', 'EACCES', 'EBUSY', 'ENOTEMPTY'].includes(error.code)) throw error;
    console.log('Browser profile is still locked; retained in the temporary directory.');
  }
}
