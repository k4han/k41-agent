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
assert.ok(executable, 'Set BROWSER_EXECUTABLE to run browser setup UI checks.');

const settings = Object.fromEntries([
  ['executable_path', 'Chrome executable', '', 'text'],
  ['storage_root', 'Browser storage root', '', 'text'],
  ['idle_ttl_seconds', 'Browser idle timeout (seconds)', 600, 'number'],
  ['operation_timeout_seconds', 'Browser operation timeout (seconds)', 75, 'number'],
  ['stream_quality', 'Live image quality', 50, 'number'],
  ['stream_max_width', 'Maximum live image width', 1280, 'number'],
  ['stream_max_height', 'Maximum live image height', 720, 'number'],
].map(([suffix, label, value, input_type]) => [`browser.${suffix}`, {
  key: `browser.${suffix}`, label, value, input_type, source: 'default', category: 'browser',
  description: label, restart_required: suffix === 'storage_root',
}]));
let installed = false, supported = true, hold = true, failNext = false, launchError = false;
let job = null, probes = 0;
const actions = [], saves = [];
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname.startsWith('/dashboard-api/') || (url.pathname === '/settings' && req.method === 'PUT')) {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('X-CSRF-Token', 'browser-setup-csrf');
    if (url.pathname === '/dashboard-api/browser/profiles') { res.end(JSON.stringify({ profiles: [] })); return; }
    if (['/dashboard-api/browser/default-profile', '/dashboard-api/browser/temporary-profile'].includes(url.pathname)) { res.end(JSON.stringify({ profile_id: null })); return; }
    if (url.pathname === '/dashboard-api/browser/configuration') {
      res.end(JSON.stringify({ settings, by_category: { browser: settings }, settings_sources: {} })); return;
    }
    if (url.pathname === '/settings') {
      assert.equal(req.headers['x-csrf-token'], 'browser-setup-csrf');
      let raw = ''; for await (const data of req) raw += data;
      const values = JSON.parse(raw).values;
      saves.push(values);
      for (const [key, value] of Object.entries(values)) settings[key].value = value;
      res.end(JSON.stringify({ status: 'success' })); return;
    }
    if (url.pathname === '/dashboard-api/browser/setup') {
      if (req.method === 'POST') {
        assert.equal(req.headers['x-csrf-token'], 'browser-setup-csrf');
        let raw = ''; for await (const data of req) raw += data;
        const action = JSON.parse(raw).action;
        actions.push(action);
        if (launchError) { res.writeHead(500); res.end(JSON.stringify({ detail: 'Cannot start browser setup worker' })); return; }
        job = { id: String(actions.length), action, state: 'running', output: '', error: null,
          configured_executable_path: settings['browser.executable_path'].value };
        res.writeHead(202); res.end(JSON.stringify(job)); return;
      }
      probes++;
      if (job?.state === 'running' && !hold) {
        job.state = failNext ? 'failed' : 'succeeded';
        job.error = failNext ? 'Package installation failed' : null;
        job.output = failNext ? 'sudo: a password is required' : 'Browser is ready for browser tools.';
        if (!failNext) installed = true;
      }
      res.end(JSON.stringify({ installed, installation_supported: supported,
        installation_unavailable_reason: supported ? null : 'Automatic installation supports Debian/Ubuntu x86_64 and ARM64.',
        executable_path: settings['browser.executable_path'].value || (installed ? '/usr/bin/google-chrome' : null),
        configured_executable_path: settings['browser.executable_path'].value, job })); return;
    }
    if (url.pathname === '/dashboard-api/system/version') {
      res.end(JSON.stringify({ current_version: '0.1.35', latest_version: '0.1.35', has_update: false })); return;
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
const scratchRoot = path.join(os.tmpdir(), 'k41-browser-setup-ui');
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
  const button = label => `Array.from(document.querySelectorAll('button')).find(button => button.textContent.trim() === ${JSON.stringify(label)})`;
  const status = text => js(`document.querySelector('.browser-setup-status')?.textContent.includes(${JSON.stringify(text)})`);
  const open = async () => {
    await call('Page.navigate', { url: base + '/settings/browser' });
    await until(() => js(`document.querySelectorAll('.browser-settings-fields input').length === 6`), 'browser settings');
  };
  await open();
  await until(() => status('Browser not installed'), 'initial state');
  assert.equal(await js(`!!document.querySelector('a[href="/settings/browser"]')`), true);
  await js(`(() => { const button = ${button('Install browser')}; button.click(); button.click(); })()`);
  await until(() => status('Installing Chrome'), 'background installation');
  assert.deepEqual(actions, ['install']);
  assert.equal(await js(`${button('Check browser')}.disabled`), true);
  await open();
  await until(() => status('Installing Chrome'), 'operation survives reload');
  hold = false;
  await until(() => status('Browser ready'), 'installation success');

  hold = true; failNext = true;
  await js(`${button('Reinstall browser')}.click()`);
  await until(() => status('Installing Chrome'), 'repair starts');
  hold = false;
  await until(() => status('Last operation failed'), 'repair fails');
  assert.equal(await js(`document.querySelector('.browser-setup-output')?.textContent.includes('sudo: a password is required')`), true);
  assert.equal(await js(`document.querySelector('.browser-setup-result details').open`), true);

  await js(`(() => { const input = Array.from(document.querySelectorAll('.setting-row')).find(row => row.textContent.includes('Chrome executable')).querySelector('input'); input.value = '/custom/chrome'; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  await until(() => js(`!!document.querySelector('.settings-pending-bar')`), 'draft path');
  assert.equal(await js(`${button('Check browser')}.disabled`), true);
  await js(`${button('Save Changes')}.click()`);
  await until(() => js(`!document.querySelector('.settings-pending-bar')`), 'save path');
  assert.equal(saves.at(-1)['browser.executable_path'], '/custom/chrome');
  failNext = false; hold = true;
  await js(`${button('Check browser')}.click()`);
  await until(() => status('Checking browser'), 'check starts');
  assert.equal(job.configured_executable_path, '/custom/chrome');
  await call('Page.navigate', { url: base + '/settings/security' });
  await delay(500);
  const probesBefore = probes;
  await delay(1800);
  assert.equal(probes, probesBefore, 'Polling must stop after leaving browser settings');
  await open();
  hold = false;
  await until(() => status('Browser ready'), 'check resumes after returning');

  launchError = true;
  await js(`${button('Check browser')}.click()`);
  await until(() => js(`document.querySelector('.browser-setup-error')?.textContent.includes('Cannot start browser setup worker')`), 'launch error remains visible');
  await delay(300);
  assert.equal(await js(`document.querySelector('.browser-setup-error')?.textContent.includes('Cannot start browser setup worker')`), true);
  launchError = false; supported = false; job = null;
  await js(`${button('Refresh')}.click()`);
  await until(() => js(`${button('Reinstall browser')}.disabled`), 'unsupported installation disabled');
  assert.equal(await js(`${button('Check browser')}.disabled`), false);
  const slider = async (id, value) => js(`(() => { const input = document.getElementById(${JSON.stringify(id)}); input.value = ${value}; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  assert.equal(await js(`document.querySelector('output[for="browser-stream-size"]').textContent`), '1280 × 720');
  for (const [index, size] of ['800 × 600', '1024 × 600', '1280 × 720', '1600 × 900', '1920 × 1080'].entries()) {
    await slider('browser-stream-size', index);
    assert.equal(await js(`document.querySelector('output[for="browser-stream-size"]').textContent`), size);
  }
  await slider('browser-stream-quality', 100);
  await js(`${button('Save Changes')}.click()`);
  await until(() => js(`!document.querySelector('.settings-pending-bar')`), 'save viewer settings');
  assert.equal(saves.at(-1)['browser.stream_quality'], 100);
  assert.equal(saves.at(-1)['browser.stream_max_width'], 1920);
  assert.equal(saves.at(-1)['browser.stream_max_height'], 1080);
  await open();
  assert.equal(await js(`document.getElementById('browser-stream-quality').value`), '100');
  assert.equal(await js(`document.querySelector('output[for="browser-stream-size"]').textContent`), '1920 × 1080');
  await slider('browser-stream-size', 0);
  await js(`${button('Discard')}.click()`);
  assert.equal(await js(`document.querySelector('output[for="browser-stream-size"]').textContent`), '1920 × 1080');
  await js(`${button('Reset viewer defaults')}.click()`);
  assert.equal(await js(`document.getElementById('browser-stream-quality').value`), '50');
  assert.equal(await js(`document.querySelector('output[for="browser-stream-size"]').textContent`), '1280 × 720');
  await js(`${button('Save Changes')}.click()`);
  await until(() => js(`!document.querySelector('.settings-pending-bar')`), 'save viewer defaults');
  settings['browser.stream_max_width'].value = 1500;
  settings['browser.stream_max_height'].value = 850;
  await open();
  assert.equal(await js(`document.querySelector('output[for="browser-stream-size"]').textContent`), 'Custom: 1500 × 850');
  assert.equal(await js(`!!document.querySelector('.settings-pending-bar')`), false, 'Loading custom dimensions must not change them');
  await slider('browser-stream-size', 1);
  await js(`document.querySelectorAll('.settings-group')[0].querySelector('[aria-label="Undo change"]').click()`);
  assert.equal(await js(`document.querySelector('output[for="browser-stream-size"]').textContent`), 'Custom: 1500 × 850');
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await delay(300);
  assert.equal(await js('document.documentElement.scrollWidth <= window.innerWidth'), true, 'Mobile page must not overflow horizontally');
  assert.deepEqual(exceptions, []);
  console.log('PASS: browser setup, viewer sliders, all size presets, save/reload, discard, undo, defaults, custom dimensions and mobile layout.');
} finally {
  await Promise.race([call?.('Browser.close').catch(() => {}), delay(2000)]);
  ws?.close(); browser.kill();
  if (browser.exitCode === null && browser.signalCode === null) {
    await Promise.race([new Promise(resolve => browser.once('exit', resolve)), delay(3000)]);
  }
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  if (path.dirname(profilePath) !== scratchRoot || !path.basename(profilePath).startsWith('browser-')) {
    throw new Error('Refusing to remove a profile outside the test directory.');
  }
  try { fs.rmSync(profilePath, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); }
  catch (error) { if (!['EPERM', 'EACCES', 'EBUSY', 'ENOTEMPTY'].includes(error.code)) throw error; }
}
