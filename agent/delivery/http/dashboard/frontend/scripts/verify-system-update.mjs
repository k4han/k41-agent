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

let scenario = 'managed';
let posts = 0;
let probes = 0;
let pages = 0;
let currentVersion = '0.1.2';
const fixtures = {
  '/dashboard-api/catalog': {},
  '/dashboard-api/sessions': { sessions: [] },
  '/dashboard-api/chat-history': { threads: [], has_more: false },
  '/dashboard-api/workspace/default': { workspace: null },
};
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname.startsWith('/dashboard-api') || url.pathname === '/health') {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('X-CSRF-Token', 'test-csrf');
    if (url.pathname === '/dashboard-api/system/version') {
      res.end(JSON.stringify({
        current_version: currentVersion, latest_version: '0.1.3', has_update: currentVersion !== '0.1.3',
        release_name: 'Test release', release_notes: 'Update regression check', release_url: '', published_at: null,
        is_managed_install: scenario !== 'development', install_type: scenario === 'development' ? 'development' : 'managed',
        error: scenario === 'check-error' ? 'GitHub unavailable' : null,
      })); return;
    }
    if (url.pathname === '/dashboard-api/system/update' && req.method === 'POST') {
      posts++;
      assert.equal(req.headers['x-csrf-token'], 'test-csrf');
      if (scenario === 'launch-error') {
        res.writeHead(500); res.end(JSON.stringify({ detail: 'Cannot launch updater' })); return;
      }
      res.end(JSON.stringify({ status: 'started', update_id: 'test-job' })); return;
    }
    if (url.pathname === '/dashboard-api/system/update/status') {
      probes++;
      assert.equal(url.searchParams.get('update_id'), 'test-job');
      if (scenario === 'download-error') {
        res.end(JSON.stringify({ status: 'failed', error: 'Download unavailable' })); return;
      }
      if (probes > 1) {
        if (scenario === 'rollback') {
          res.end(JSON.stringify({ status: 'failed', error: 'Dependency sync failed; previous source restored' })); return;
        }
        currentVersion = '0.1.3';
        res.end(JSON.stringify({ status: 'updated', latest_version: currentVersion })); return;
      }
      res.end(JSON.stringify({ status: 'running' })); return;
    }
    if (url.pathname === '/health') {
      if (scenario === 'rollback' && probes === 1) { res.writeHead(503); res.end('{}'); return; }
      res.end(JSON.stringify({ status: 'ok', version: currentVersion, started_at: probes + 1 })); return;
    }
    res.end(JSON.stringify(fixtures[url.pathname] || {})); return;
  }
  const relative = url.pathname.startsWith('/dashboard-assets/') ? url.pathname.slice('/dashboard-assets/'.length) : 'index.html';
  const target = path.resolve(staticRoot, relative);
  if (!target.startsWith(staticRoot + path.sep)) { res.writeHead(403); res.end(); return; }
  try {
    if (relative === 'index.html') pages++;
    const types = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' };
    res.setHeader('Content-Type', types[path.extname(target)] || 'application/octet-stream');
    res.end(fs.readFileSync(target));
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const scratchRoot = path.join(os.tmpdir(), 'k41-update-ui');
fs.mkdirSync(scratchRoot, { recursive: true });
const profilePath = fs.mkdtempSync(path.join(scratchRoot, 'update-browser-'));
const browser = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run',
  '--disable-background-networking', '--remote-debugging-port=0', `--user-data-dir=${profilePath}`, 'about:blank'],
{ windowsHide: true, stdio: 'ignore' });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(callback, label, timeout = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await callback()) return;
    await delay(100);
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
  const pending = new Map();
  const exceptions = [];
  call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
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
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails);
  });
  await call('Page.enable'); await call('Runtime.enable');
  const js = async expression => {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const updateButton = `Array.from(document.querySelectorAll('.dialog-footer button')).find(button => button.textContent.includes('Update to'))`;
  const open = async mode => {
    scenario = mode; posts = 0; probes = 0; currentVersion = '0.1.2';
    await call('Page.navigate', { url: base + '/history' });
    try {
      await until(() => js(`document.querySelector('.brand-version-btn')?.textContent.includes('0.1.2')`), 'version button');
    } catch (error) {
      console.log(await js('document.body.textContent.slice(0, 2000)'));
      console.log(JSON.stringify(exceptions));
      throw error;
    }
    await js(`document.querySelector('.brand-version-btn').click()`);
    await until(() => js(`!!document.querySelector('[role="dialog"]')`), 'update dialog');
  };
  await open('check-error');
  assert.equal(await js(`document.querySelector('[role="alert"]').textContent.includes('GitHub unavailable')`), true);
  assert.equal(await js(`${updateButton}.disabled`), true);
  assert.equal(await js(`!!document.querySelector('.update-up-to-date-box')`), false);
  await open('development');
  assert.equal(await js(`!!(${updateButton})`), false);
  assert.equal(await js(`document.querySelector('.update-dev-code-row').textContent.includes('uv sync')`), true);
  for (const [mode, message] of [
    ['launch-error', 'Cannot launch updater'], ['download-error', 'Download unavailable'],
    ['rollback', 'previous source restored'],
  ]) {
    await open(mode);
    const pagesBefore = pages;
    await js(`${updateButton}.click()`);
    if (mode === 'rollback') {
      await until(() => js(`document.querySelector('.update-dialog-progress-title')?.textContent.includes('Reconnecting')`), 'restart progress');
      await js(`window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))`);
      assert.equal(await js(`!!document.querySelector('[role="dialog"]')`), true);
    }
    await until(() => js(`document.querySelector('.update-dialog-progress-desc')?.textContent.includes(${JSON.stringify(message)})`), mode);
    await delay(1800);
    assert.equal(pages, pagesBefore, 'Failed updates must not reload or report success.');
    assert.equal(posts, 1);
  }
  await open('success');
  const pagesBefore = pages;
  await js(`(() => { const button = ${updateButton}; button.click(); button.click(); })()`);
  await until(() => js(`document.querySelector('.update-dialog-progress-title')?.textContent.includes('Completed')`), 'update success');
  assert.equal(await js(`document.querySelector('.update-dialog-progress-desc').textContent.includes('0.1.3')`), true);
  await until(() => pages > pagesBefore, 'automatic reload');
  await until(() => js(`document.querySelector('.brand-version-btn')?.textContent.includes('0.1.3')`), 'new version after reload');
  assert.equal(posts, 1);
  assert.deepEqual(exceptions, []);
  console.log('PASS: real update clicks, CSRF, lookup failure, development mode, launch failure, download failure, rollback, duplicate clicks, success and reload.');
} finally {
  await Promise.race([call?.('Browser.close').catch(() => {}), delay(2000)]);
  ws?.close(); browser.kill();
  if (browser.exitCode === null && browser.signalCode === null) {
    await Promise.race([new Promise(resolve => browser.once('exit', resolve)), delay(3000)]);
  }
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  if (path.dirname(profilePath) !== scratchRoot || !path.basename(profilePath).startsWith('update-browser-')) {
    throw new Error('Refusing to remove a browser profile outside the test directory.');
  }
  try { fs.rmSync(profilePath, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); }
  catch (error) {
    if (!['EPERM', 'EACCES', 'EBUSY', 'ENOTEMPTY'].includes(error.code)) throw error;
    console.log('Browser profile is still locked; retained in the temporary directory.');
  }
}
