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
let installed = false;
let signedIn = false;
let authError = "";
let failNext = false;
let refreshes = 0;
const mutations = [];
const agent = () => ({
  id: "antigravity-cli", display_name: "Google Antigravity (agy acp)",
  installed, version: installed ? "1.5.10" : null,
  executable_path: installed ? "C:/Program Files/agy/agy.exe" : null,
  auth: {
    signed_in: signedIn, state: authError ? "error" : !installed ? "not_installed" : signedIn ? "signed_in" : "signed_out",
    plan: signedIn ? "Pro" : "", error: authError, config_options: [],
  },
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
    if (req.method !== "GET") mutations.push(req.url);
    if (url.pathname === '/dashboard-api/acp/agents') {
      polls++;
      if (url.searchParams.get("refresh") === "true") refreshes++;
      if (failNext) {
        failNext = false;
        res.writeHead(503);
        res.end(JSON.stringify({ detail: "CLI status unavailable." }));
      } else {
        res.end(JSON.stringify({ agents: [agent()] }));
      }
      return;
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
  const panel = 'document.querySelector("section.panel")';
  const refresh = 'document.querySelector("section button")';
  await call('Page.navigate', { url: base + '/settings/acp' });
  await until(() => js("document.body.textContent.includes('Not installed')"), "missing CLI");
  assert.equal(await js("document.querySelectorAll('section.panel').length"), 1);
  assert.equal(await js("document.querySelectorAll('section input, section textarea').length"), 0);
  assert.equal(await js("document.body.textContent.includes('agy acp --login')"), true);
  assert.equal(await js("document.body.textContent.includes('agy update')"), true);
  assert.equal(await js("document.querySelectorAll('section button').length"), 1);

  const refreshStatus = async (expected) => {
    const before = refreshes;
    await js(refresh + ".click()");
    await until(() => refreshes > before, "refresh query");
    await until(() => js("!" + refresh + ".disabled && document.body.textContent.includes(" + JSON.stringify(expected) + ")"), expected);
  };
  installed = true;
  await refreshStatus("Version 1.5.10, not signed in");
  assert.equal(await js("document.body.textContent.includes('C:/Program Files/agy/agy.exe')"), true);
  signedIn = true;
  await refreshStatus("Version 1.5.10, signed in (Pro)");
  signedIn = false;
  authError = "Run agy update, then refresh.";
  await refreshStatus(authError);
  assert.equal(await js(panel + ".querySelector('[role=alert]').textContent.includes('agy update')"), true);
  failNext = true;
  await refreshStatus("CLI status unavailable.");
  authError = "";
  await refreshStatus("Version 1.5.10, not signed in");
  const before = polls;
  await delay(1700);
  assert.equal(polls, before, "Read-only CLI status must not poll installation or sign-in jobs");
  assert.deepEqual(mutations, []);
  assert.deepEqual(exceptions, []);
  console.log("PASS: CLI setup, status, refresh, errors and recovery work without managed installation or sign-in.");
} finally {
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
