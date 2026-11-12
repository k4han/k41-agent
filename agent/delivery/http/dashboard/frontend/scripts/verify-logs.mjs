import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const staticRoot = fileURLToPath(new URL('../../static', import.meta.url));
const executable = process.env.BROWSER_EXECUTABLE || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe', '/usr/bin/google-chrome',
  '/usr/bin/chromium', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].find(candidate => fs.existsSync(candidate));
assert.ok(executable, 'Set BROWSER_EXECUTABLE to run log viewer UI checks.');
const output = {
  server: Array.from({ length: 50000 }, (_, index) => `line ${String(index).padStart(5, '0')} ${'x'.repeat(70)}\n`).join('')
    + '<img src=x onerror="window.logInjected=true">\nLATEST OUTPUT\n',
  tray: 'Tray output\n', update: '',
};
let failTray = false;
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname.startsWith('/dashboard-api/')) {
    res.setHeader('Content-Type', 'application/json');
    if (url.pathname === '/dashboard-api/logs') {
      res.end(JSON.stringify({ sources: Object.keys(output).map(id => ({ id, label: id, available: !!output[id], error: null })) }));
      return;
    }
    if (url.pathname.startsWith('/dashboard-api/logs/')) {
      const source = url.pathname.split('/').at(-1);
      if (source === 'tray' && failTray) {
        failTray = false;
        res.writeHead(503); res.end(JSON.stringify({ detail: 'Temporary read failure' })); return;
      }
      const text = output[source];
      let start, end;
      if (url.searchParams.has('after')) {
        start = Number(url.searchParams.get('after')); end = Math.min(text.length, start + 65536);
      } else {
        end = url.searchParams.has('before') ? Number(url.searchParams.get('before')) : text.length;
        start = Math.max(0, end - 65536);
      }
      res.end(JSON.stringify({ source, text: text.slice(start, end), bytes: end - start,
        before: String(start), after: String(end), has_older: start > 0, has_newer: end < text.length })); return;
    }
    res.end('{}'); return;
  }
  const relative = url.pathname.startsWith('/dashboard-assets/') ? url.pathname.slice('/dashboard-assets/'.length) : 'index.html';
  const target = path.resolve(staticRoot, relative);
  if (!target.startsWith(staticRoot + path.sep)) { res.writeHead(403); res.end(); return; }
  try {
    res.setHeader('Content-Type', { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' }[path.extname(target)] || 'application/octet-stream');
    res.end(fs.readFileSync(target));
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const scratchRoot = path.join(os.tmpdir(), 'k41-logs-ui');
fs.mkdirSync(scratchRoot, { recursive: true });
const profilePath = fs.mkdtempSync(path.join(scratchRoot, 'browser-'));
const browser = spawn(executable, ['--headless=new', '--no-first-run', '--no-sandbox',
  '--disable-background-networking', '--remote-debugging-port=0', `--user-data-dir=${profilePath}`, 'about:blank'],
{ windowsHide: true, stdio: 'ignore' });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(callback, label, timeout = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await callback()) return;
    await delay(50);
  }
  throw new Error(`Timeout: ${label}`);
}
let ws, call;
try {
  const portFile = path.join(profilePath, 'DevToolsActivePort');
  await until(() => fs.existsSync(portFile), 'browser debugger');
  const port = Number(fs.readFileSync(portFile, 'utf8').split('\n')[0]);
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  ws = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
  await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }));
  let sequence = 0;
  const pending = new Map(), exceptions = [];
  call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params }));
  });
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const entry = pending.get(message.id); pending.delete(message.id);
      if (message.error) entry?.reject(new Error(message.error.message)); else entry?.resolve(message.result);
    }
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails);
  });
  ws.addEventListener('close', () => {
    for (const entry of pending.values()) entry.reject(new Error('Browser closed'));
    pending.clear();
  });
  await call('Page.enable'); await call('Runtime.enable');
  const js = async expression => {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const text = () => js('document.querySelector(".logs-output")?.textContent || ""');
  const atBottom = () => js('(() => { const v = document.querySelector(".logs-viewport"); return v.scrollHeight - v.clientHeight - v.scrollTop < 2; })()');
  const choose = id => js(`(() => { const s = document.querySelector('#log-source'); s.value = ${JSON.stringify(id)}; s.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  const latest = () => js('Array.from(document.querySelectorAll("button")).find(b => b.textContent.trim() === "Latest logs").click()');
  await call('Page.navigate', { url: base + '/settings/logs' });
  await until(async () => (await text()).includes('LATEST OUTPUT'), 'initial tail');
  await until(atBottom, 'initial bottom position');
  assert.equal(await js(`!!document.querySelector('a[href="/settings/logs"]')`), true);
  assert.equal(await js('window.logInjected === undefined && !document.querySelector(".logs-output img")'), true);
  await js('(() => { const v = document.querySelector(".logs-viewport"); v.scrollTop = 100; window.savedChunk = document.querySelector("[data-log-chunk]"); window.savedTop = window.savedChunk.getBoundingClientRect().top; })()');
  await until(() => js('document.querySelectorAll("[data-log-chunk]").length === 2'), 'older chunk');
  await delay(100);
  assert.ok(await js('Math.abs(window.savedChunk.getBoundingClientRect().top - window.savedTop) < 2'), 'Prepending must preserve the visible position');
  const oldPosition = await js('document.querySelector(".logs-viewport").scrollTop');
  output.server += 'NEW WHILE READING HISTORY\n';
  await until(() => js('document.querySelector(".logs-status").textContent.includes("New logs available")'), 'new output indicator');
  assert.equal(await js('document.querySelector(".logs-viewport").scrollTop'), oldPosition);
  await latest();
  await until(async () => (await text()).includes('NEW WHILE READING HISTORY'), 'return to latest');
  await until(atBottom, 'latest bottom position');
  output.server += 'LIVE APPEND\n';
  await until(async () => (await text()).includes('LIVE APPEND'), 'live output');
  await until(atBottom, 'live output follows bottom');

  output.server += Array.from({ length: 2000 }, (_, index) => `burst ${index} ${'x'.repeat(70)}\n`).join('')
    + 'BURST COMPLETE\n';
  await until(async () => (await text()).includes('BURST COMPLETE'), 'multi-page burst');
  await until(atBottom, 'multi-page burst follows bottom');
  output.server += 'AFTER BURST\n';
  await until(async () => (await text()).includes('AFTER BURST'), 'live output after burst');
  await until(atBottom, 'live output still follows after burst');

  for (let index = 0; index < 34; index++) {
    const first = await js('document.querySelector("[data-log-chunk]").dataset.logChunk');
    await js('document.querySelector(".logs-viewport").scrollTop = 0');
    await until(() => js(`document.querySelector('[data-log-chunk]').dataset.logChunk !== ${JSON.stringify(first)}`), 'prepend window');
    await js('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
  }
  assert.ok(await js('new TextEncoder().encode(document.querySelector(".logs-output").textContent).length <= 2 * 1024 * 1024'), 'Rendered output must be bounded');
  const last = await js('document.querySelector("[data-log-chunk]:last-child").dataset.logChunk');
  await js('document.querySelector(".logs-viewport").scrollTop = document.querySelector(".logs-viewport").scrollHeight');
  await until(() => js(`document.querySelector('[data-log-chunk]:last-child').dataset.logChunk !== ${JSON.stringify(last)}`), 'reload discarded newer output');

  failTray = true;
  await choose('tray');
  await until(() => js('document.querySelector(".logs-error")?.textContent.includes("Temporary read failure")'), 'read failure');
  await js('Array.from(document.querySelectorAll("button")).find(b => b.textContent.trim() === "Retry").click()');
  await until(async () => (await text()) === output.tray, 'retry and source switch');
  await choose('update');
  await until(() => js('document.querySelector(".logs-empty")?.textContent.includes("No log output")'), 'empty source');
  output.update = 'First updater output\n';
  await until(async () => (await text()) === output.update, 'empty source receives output');
  await call('Page.reload');
  await until(async () => (await text()).includes('AFTER BURST'), 'direct route reload');
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await delay(100);
  assert.equal(await js('document.documentElement.scrollWidth <= window.innerWidth'), true, 'Mobile layout must not overflow');
  assert.deepEqual(exceptions, []);
  console.log('PASS: log tail, prepend anchor, multi-page live updates, bounded window, source switch, retry, empty source, reload, escaping and mobile layout.');
} finally {
  await Promise.race([call?.('Browser.close').catch(() => {}), delay(2000)]);
  ws?.close(); browser.kill();
  if (browser.exitCode === null && browser.signalCode === null) {
    await Promise.race([new Promise(resolve => browser.once('exit', resolve)), delay(3000)]);
  }
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  const resolved = path.resolve(profilePath);
  if (path.dirname(resolved) !== path.resolve(scratchRoot) || !path.basename(resolved).startsWith('browser-')) {
    throw new Error('Refusing to remove a profile outside the test directory.');
  }
  try { fs.rmSync(resolved, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); }
  catch (error) { if (!['EPERM', 'EACCES', 'EBUSY', 'ENOTEMPTY'].includes(error.code)) throw error; }
}
