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
let version = "1.5.10";
let signedIn = false;
let authError = "";
let failNext = false;
let refreshes = 0;
let operation = null;
const mutations = [];
const revision = () => installed ? `managed-${version}` : "missing";
const agent = () => ({
  id: "antigravity-cli", display_name: "Google Antigravity (agy acp)",
  installed, version: installed ? version : null,
  latest_version: "1.5.12", management_source: installed ? "managed" : null,
  installation_revision: revision(), operation,
  actions: { install: !installed, update: installed, uninstall: installed },
  executable_path: installed ? "C:/K41/acp/agy.exe" : null,
  login_command: "agy acp --login",
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
    if (req.method !== "GET" && url.pathname.startsWith('/dashboard-api/acp/agents/')) {
      const action = req.method === "DELETE" ? "uninstall" : url.pathname.split("/").at(-1);
      mutations.push({ action, csrf: req.headers["x-csrf-token"], revision: req.headers["if-match"] });
      operation = { id: `operation-${mutations.length}`, action, state: "running", error: null };
      res.writeHead(202);
      res.end(JSON.stringify({ operation }));
      return;
    }
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
  const button = (name, scope = "section.panel") =>
    `([...document.querySelectorAll(${JSON.stringify(scope + " button")})].find(button => button.textContent.trim() === ${JSON.stringify(name)}))`;
  const shows = (text) => js(`document.body.textContent.includes(${JSON.stringify(text)})`);
  await call('Page.navigate', { url: base + '/settings/acp' });
  await until(() => shows('Not installed'), "missing CLI");
  assert.equal(await js("document.querySelectorAll('section.panel').length"), 1);
  assert.equal(await js("document.querySelectorAll('section input, section textarea').length"), 0);
  assert.equal(await shows('Latest available: 1.5.12'), true);
  await js(button("Download and install") + ".click()");
  await until(() => shows("Downloading and installing…"), "installation progress");
  assert.equal(await js(button("Download and install") + ".disabled"), true);
  await call('Page.reload');
  await until(() => shows("Downloading and installing…"), "installation progress after reload");
  installed = true;
  operation = { ...operation, state: "succeeded" };
  await until(() => shows("Version 1.5.10, not signed in"), "installed CLI after polling");
  assert.equal(await shows('C:/K41/acp/agy.exe'), true);
  assert.equal(await shows('Installation source: K41'), true);
  assert.equal(await shows('agy acp --login'), true);

  const refreshStatus = async (expected) => {
    const before = refreshes;
    await js(refresh + ".click()");
    await until(() => refreshes > before, "refresh query");
    await until(() => js("!" + refresh + ".disabled && document.body.textContent.includes(" + JSON.stringify(expected) + ")"), expected);
  };
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

  await js(button("Update") + ".click()");
  await until(() => shows("Updating…"), "update progress");
  operation = { ...operation, state: "failed", error: "The download failed its checksum verification." };
  await until(() => shows("checksum verification"), "update error");
  assert.equal(await shows("Version 1.5.10, not signed in"), true);
  await js(button("Update") + ".click()");
  await until(() => shows("Updating…"), "retry progress");
  version = "1.5.12";
  operation = { ...operation, state: "succeeded" };
  await until(() => shows("Version 1.5.12, not signed in"), "updated CLI version");

  await js(button("Uninstall") + ".click()");
  await until(() => shows("Uninstall ACP agent?"), "uninstall confirmation");
  await js(button("Cancel", '[role="dialog"]') + ".click()");
  assert.equal(mutations.length, 3);
  await js(button("Uninstall") + ".click()");
  await js(button("Uninstall", '[role="dialog"]') + ".click()");
  await until(() => shows("Uninstalling…"), "uninstall progress");
  installed = false;
  operation = { ...operation, state: "succeeded" };
  await until(() => shows("Not installed"), "uninstalled CLI");
  const before = polls;
  await delay(1700);
  assert.equal(polls, before, "Polling stops when all operations are finished");
  assert.deepEqual(mutations, [
    { action: "install", csrf: "test-csrf", revision: '"missing"' },
    { action: "update", csrf: "test-csrf", revision: '"managed-1.5.10"' },
    { action: "update", csrf: "test-csrf", revision: '"managed-1.5.10"' },
    { action: "uninstall", csrf: "test-csrf", revision: '"managed-1.5.12"' },
  ]);
  assert.deepEqual(exceptions, []);
  console.log("PASS: ACP installs, updates, uninstall confirmation, refresh and operation recovery work.");
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
