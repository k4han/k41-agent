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

const pools = [];
const writes = [];
let defaultModel = '';
const models = [
  { provider: 'primary', provider_type: 'openai_compatible', default_model: 'first', can_list_models: true,
    models: [{ id: 'first', label: 'First', source: 'configured' }] },
  { provider: 'backup', provider_type: 'google', default_model: 'second', can_list_models: true,
    models: [{ id: 'second', label: 'Second', source: 'configured' }] },
];
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname.startsWith('/dashboard-api') || url.pathname === '/providers/models' || url.pathname.startsWith('/settings/') && req.method === 'PUT') {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('X-CSRF-Token', 'test-csrf');
    const raw = req.method === 'GET' ? '' : await new Promise(resolve => {
      let value = ''; req.on('data', chunk => value += chunk); req.on('end', () => resolve(value));
    });
    const body = JSON.parse(raw || '{}');
    if (req.method !== 'GET') writes.push({ path: url.pathname, method: req.method, body });
    if (url.pathname === '/providers/models') {
      res.end(JSON.stringify({ providers: models })); return;
    }
    if (url.pathname === '/settings/llm.default_model') {
      defaultModel = body.value; res.end('{"status":"success"}'); return;
    }
    if (url.pathname === '/dashboard-api/model-pools') {
      if (req.method === 'POST') {
        pools.push({ retry_count: 2, cooldown_seconds: 60, quota_cooldown_seconds: 3600, ...body });
        res.end('{"status":"created"}');
      } else res.end(JSON.stringify({ pools: pools.map(pool => ({ ...pool,
        is_default: defaultModel === '@pool/' + pool.name,
        status: pool.members.map(member => ({ ...member, available: true, error_code: null, retry_at: null })),
      })) }));
      return;
    }
    const route = url.pathname.match(/^\/dashboard-api\/model-pools\/([^/]+)(\/reset)?$/);
    if (route) {
      const index = pools.findIndex(pool => pool.name === decodeURIComponent(route[1]));
      if (req.method === 'PUT') Object.assign(pools[index], body);
      if (req.method === 'DELETE') {
        if (defaultModel === '@pool/' + pools[index].name) {
          res.statusCode = 409; res.end('{"detail":"The default model pool cannot be deleted."}'); return;
        }
        pools.splice(index, 1);
      }
      res.end('{"status":"success"}'); return;
    }
    res.end(JSON.stringify({ settings: {}, provider_rows: [], providers_catalog: {}, provider_type_options: [],
      backend_catalog: [], backends: [], settings_sources: {} })); return;
  }
  const relative = url.pathname.startsWith('/dashboard-assets/') ? url.pathname.slice('/dashboard-assets/'.length) : 'index.html';
  const target = path.resolve(staticRoot, relative);
  if (!target.startsWith(path.resolve(staticRoot) + path.sep)) { res.statusCode = 403; res.end(); return; }
  try {
    res.setHeader('Content-Type', { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' }[path.extname(target)] || 'application/octet-stream');
    res.end(fs.readFileSync(target));
  } catch { res.statusCode = 404; res.end('Not found'); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const scratchRoot = path.join(os.tmpdir(), 'k41-model-pools-ui');
fs.mkdirSync(scratchRoot, { recursive: true });
const profile = fs.mkdtempSync(path.join(scratchRoot, 'pool-browser-'));
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
    await until(() => js('document.querySelectorAll("[role=tab]").length === 5'), 'provider tabs');
  };

  const idle = async () => until(() => js('!Array.from(document.querySelectorAll("button")).find(button => button.textContent.trim() === "New Pool")?.disabled'), 'editor ready');
  const click = async label => js(`Array.from(document.querySelectorAll('button')).find(button => button.textContent.trim() === ${JSON.stringify(label)}).click()`);
  const input = async (selector, value) => js(`(() => { const input = document.querySelector(${JSON.stringify(selector)}); input.value = ${JSON.stringify(value)}; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  const chooseModel = async (index, value) => js(`(() => { const input = document.querySelectorAll('.model-picker-input')[${index}]; input.focus(); input.value = ${JSON.stringify(value)}; input.dispatchEvent(new Event('input', { bubbles: true })); input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); })()`);
  await navigate('/settings/providers?tab=pools');
  await until(() => js('document.body.textContent.includes("No model pools yet")'), 'empty pool state');
  await click('New Pool');
  await input('#model-pool-name', 'small');
  await click('Add Model');
  await chooseModel(0, 'primary/custom/model');
  await click('Add Model');
  await chooseModel(1, 'backup/second');
  await js('document.querySelector(\'button[aria-label="Move model 2 up"]\').click()');
  await input('#pool-retry_count', '1');
  await js('document.querySelector("form").requestSubmit()');
  await until(() => pools.length === 1 && js('document.querySelector("#model-pool-name").disabled'), 'pool saved');
  await idle();
  assert.deepEqual(pools[0].members, [{ provider: 'backup', model: 'second' }, { provider: 'primary', model: 'custom/model' }]);
  assert.equal(pools[0].retry_count, 1);
  await input('#pool-retry_count', '0');
  await js('document.querySelector("form").requestSubmit()');
  await until(() => writes.some(write => write.method === 'PUT' && write.path.endsWith('/small')), 'pool updated');
  assert.equal(pools[0].retry_count, 0);
  await idle();
  await click('Reset State');
  await until(() => writes.some(write => write.path.endsWith('/small/reset')), 'recovery reset');
  await idle();
  await click('Set Default');
  await until(() => defaultModel === '@pool/small', 'pool selected as default');
  await idle();
  await click('New Pool');
  await input('#model-pool-name', 'unsaved');
  await js('document.querySelector(".model-pool-card").click()');
  await until(() => js('!!document.querySelector("[role=dialog]")'), 'discard dialog');
  await click('Cancel');
  assert.equal(await js('document.querySelector("#model-pool-name").value'), 'unsaved');
  await js('document.querySelector(".model-pool-card").click()');
  await click('Discard');
  assert.equal(await js('document.querySelector("#model-pool-name").value'), 'small');
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await delay(100);
  assert.equal(await js('document.documentElement.scrollWidth <= window.innerWidth'), true);
  await click('Delete');
  await js('Array.from(document.querySelectorAll("[role=dialog] button")).find(button => button.textContent.trim() === "Delete").click()');
  await until(() => js('document.body.textContent.includes("The default model pool cannot be deleted")'), 'reference error shown');
  await click('Cancel');
  defaultModel = '';
  await click('Delete');
  await js('Array.from(document.querySelectorAll("[role=dialog] button")).find(button => button.textContent.trim() === "Delete").click()');
  await until(() => pools.length === 0 && js('document.body.textContent.includes("No model pools yet")'), 'pool deleted');
  assert.deepEqual(exceptions, []);
  console.log('PASS: pool creation, manual model IDs, ordering, updates, default selection, reset, discard, reference errors, deletion and mobile layout.');
} finally {
  await closeBrowser?.();
  ws?.close(); browser.kill();
  if (browser.exitCode === null && browser.signalCode === null) {
    await Promise.race([new Promise(resolve => browser.once('exit', resolve)), delay(3000)]);
  }
  await new Promise(resolve => server.close(resolve));
  if (path.dirname(profile) !== scratchRoot || !path.basename(profile).startsWith('pool-browser-')) {
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
