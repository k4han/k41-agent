import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const staticRoot = fileURLToPath(new URL('../../static', import.meta.url));
const executable = process.env.BROWSER_EXECUTABLE || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].find(candidate => fs.existsSync(candidate));
if (!executable) {
  console.log('SKIP: Set BROWSER_EXECUTABLE to a Chromium browser to run UI checks.');
  process.exit(0);
}
const services = [
  { type: 'google', label: 'Google Search', fields: ['api_key', 'cse_id'], capabilities: ['search'] },
  { type: 'tavily', label: 'Tavily', fields: ['api_key'], capabilities: ['search', 'fetch'] },
  { type: 'firecrawl', label: 'Firecrawl', fields: ['api_key', 'base_url'], capabilities: ['search', 'fetch'] },
  { type: 'brave', label: 'Brave', fields: ['api_key'], capabilities: ['search'] },
  { type: 'bing', label: 'Bing', fields: ['api_key'], capabilities: ['search'] },
];
const connections = [{ name: 'team', type: 'tavily', fields: {}, has_api_key: true, configured: true,
  is_default: true, capabilities: ['search', 'fetch'], sources: { api_key: 'connection' } }];
const defaults = { tavily: 'team' };
const writes = [];
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname.startsWith('/dashboard-api') || url.pathname === '/settings') {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('X-CSRF-Token', 'test-csrf');
    const body = req.method === 'GET' ? '' : await new Promise(resolve => {
      let value = ''; req.on('data', chunk => value += chunk); req.on('end', () => resolve(value));
    });
    if (req.method !== 'GET') writes.push({ path: url.pathname, body: JSON.parse(body || '{}') });
    if (url.pathname === '/dashboard-api/web-connections') {
      if (req.method === 'POST') {
        const data = JSON.parse(body);
        connections.push({ name: data.name, type: data.type, fields: {}, has_api_key: !!data.api_key,
          configured: !!data.api_key, is_default: false, capabilities: ['search', 'fetch'], sources: { api_key: 'connection' } });
        res.end(JSON.stringify({ status: 'created', name: data.name }));
      } else res.end(JSON.stringify({ services, connections, defaults }));
      return;
    }
    if (url.pathname.startsWith('/dashboard-api/web-connections/defaults/')) {
      const kind = url.pathname.split('/').pop();
      defaults[kind] = JSON.parse(body).name;
      for (const entry of connections) if (entry.type === kind) entry.is_default = entry.name === defaults[kind];
      res.end('{"status":"updated"}'); return;
    }
    res.end(JSON.stringify({ settings: {}, by_category: {}, provider_rows: [], providers_catalog: {},
      provider_type_options: [], backend_catalog: [], backends: [], settings_sources: {} })); return;
  }
  const relative = url.pathname.startsWith('/dashboard-assets/') ? url.pathname.slice('/dashboard-assets/'.length) : 'index.html';
  const target = path.join(staticRoot, relative);
  try {
    const types = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml' };
    res.setHeader('Content-Type', types[path.extname(target)] || 'application/octet-stream');
    res.end(fs.readFileSync(target));
  } catch { res.statusCode = 404; res.end('Not found'); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const scratchRoot = path.join(os.tmpdir(), 'k41-provider-ui');
fs.mkdirSync(scratchRoot, { recursive: true });
const profile = fs.mkdtempSync(path.join(scratchRoot, 'provider-browser-'));
const browser = spawn(executable, [
  '--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-networking',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank',
], { windowsHide: true, stdio: 'ignore' });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(callback, label, timeout = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    try { const result = await callback(); if (result) return result; } catch {}
    await delay(100);
  }
  throw new Error(`Timeout: ${label}`);
}
let ws;
let closeBrowser;
try {
  const port = await until(() => fs.existsSync(path.join(profile, 'DevToolsActivePort')) &&
    Number(fs.readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]), 'browser debugger');
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  ws = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
  await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }));
  let sequence = 0;
  let acceptDialog = false;
  let dialogs = 0;
  const pending = new Map();
  const exceptions = [];
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
  closeBrowser = () => Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws.addEventListener('close', () => {
    for (const entry of pending.values()) entry.reject(new Error('Browser closed.'));
    pending.clear();
  });
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const entry = pending.get(message.id); pending.delete(message.id);
      if (message.error) entry?.reject(new Error(message.error.message)); else entry?.resolve(message.result);
    }
    if (message.method === 'Page.javascriptDialogOpening') {
      dialogs++; void call('Page.handleJavaScriptDialog', { accept: acceptDialog });
    }
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails.text);
  });
  await call('Page.enable'); await call('Runtime.enable');
  const js = async expression => {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  };
  const navigate = async suffix => {
    await call('Page.navigate', { url: base + suffix });
    await until(() => js('document.querySelectorAll("[role=tab]").length === 4'), 'provider tabs');
  };
  await navigate('/settings/providers?tab=web');
  await until(() => js('document.body.textContent.includes("team")'), 'web connections');
  assert.equal(await js('document.querySelectorAll("aside.sidebar").length'), 1);
  await js('Array.from(document.querySelectorAll("section.panel")).find(p => p.querySelector(".panel-title")?.textContent === "Tavily").querySelector("button").click()');
  await until(() => js('!!document.querySelector("form input[type=password]")'), 'create form');
  await js('const name = document.querySelector("form input.input"); name.value = "review"; name.dispatchEvent(new Event("input", { bubbles: true })); const key = document.querySelector("form input[type=password]"); key.value = "fake-key"; key.dispatchEvent(new Event("input", { bubbles: true }));');
  await js('Array.from(document.querySelectorAll("[role=tab]")).find(t => t.textContent.includes("Models")).click()');
  assert.equal(dialogs, 1);
  assert.equal(await js('document.querySelector("form input.input").value'), 'review');
  await js('document.querySelector("form").requestSubmit()');
  await until(() => js('!document.querySelector("form") && document.body.textContent.includes("review")'), 'saved connection');
  assert.equal(writes.find(write => write.path === '/dashboard-api/web-connections').body.name, 'review');
  await js('Array.from(document.querySelectorAll(".row-wrap")).find(r => r.querySelector("strong")?.textContent === "review").querySelector("button").click()');
  await until(() => js('document.querySelector("form input.input")?.value === "review"'), 'connection detail');
  assert.equal(await js('!!document.querySelector("form input[type=password]")'), false);
  await js('const input = document.querySelector("form input[type=checkbox]"); input.click();');
  await js('const replacementKey = document.querySelector("form input[type=password]"); replacementKey.value = "replacement"; replacementKey.dispatchEvent(new Event("input", { bubbles: true }));');
  acceptDialog = true;
  await js('Array.from(document.querySelectorAll("[role=tab]")).find(t => t.textContent.includes("Models")).click()');
  await until(() => js('new URLSearchParams(location.search).get("tab") === "llm"'), 'confirmed tab change');
  assert.equal(dialogs, 2);
  await navigate('/settings/providers/web');
  await until(() => js('location.pathname === "/settings/providers/llm/web"'), 'legacy provider named web');
  await navigate('/settings/backends/local');
  await until(() => js('location.pathname === "/settings/providers/workspace/local"'), 'legacy backend detail');
  await navigate('/settings/providers?tab=web');
  await until(() => js('document.body.textContent.includes("review")'), 'mobile page');
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await delay(250);
  assert.equal(await js('document.documentElement.scrollWidth <= window.innerWidth'), true);
  assert.deepEqual(exceptions, []);
  console.log('PASS: four tabs, one layout, create/edit, discard guard, legacy redirects, mobile width.');
} finally {
  await closeBrowser?.();
  ws?.close(); browser.kill();
  if (browser.exitCode === null && browser.signalCode === null) {
    await Promise.race([new Promise(resolve => browser.once('exit', resolve)), delay(3000)]);
  }
  await new Promise(resolve => server.close(resolve));
  if (path.dirname(profile) !== scratchRoot || !path.basename(profile).startsWith('provider-browser-')) {
    throw new Error('Refusing to remove a browser profile outside the test directory.');
  }
  try {
    fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  } catch (error) {
    if (!['EPERM', 'EACCES', 'EBUSY', 'ENOTEMPTY'].includes(error.code)) throw error;
    // Chromium subprocesses can retain profile locks briefly on Windows.
    console.log('Browser profile is still locked; retained in the system temporary directory.');
  }
}
