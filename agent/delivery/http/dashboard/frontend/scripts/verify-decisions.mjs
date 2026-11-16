import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const frontendRoot = fileURLToPath(new URL('..', import.meta.url));
const executable = process.env.BROWSER_EXECUTABLE || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].find(candidate => fs.existsSync(candidate));
assert.ok(executable, 'Set BROWSER_EXECUTABLE to run browser checks.');

const field = (key, value, input_type, label, extra = {}) => ({ key, value, input_type, label, source: 'database', description: '', category: 'decision', ...extra });
const settings = {
  'decision.router.mode': field('decision.router.mode', 'cascade', 'select', 'Router Decision Strategy', { options: ['llm_only', 'clef_only', 'shadow', 'cascade'] }),
  'decision.default_provider': field('decision.default_provider', 'team', 'select', 'Decision Model'),
  'decision.router.llm_model': field('decision.router.llm_model', '', 'select', 'Router LLM Model'),
  'decision.router.threshold': field('decision.router.threshold', 0.75, 'number', 'Cascade Confidence Threshold'),
  'decision.router.log_telemetry': field('decision.router.log_telemetry', true, 'boolean', 'Log Routing Telemetry'),
};
let providers = [
  { name: 'team', type: 'cloudflare', fields: { model: '@cf/cloudflare/clef-flash' }, configured: true, is_default: true },
  { name: 'google', type: 'google', fields: { model: 'gemini-decision-test' }, configured: true, is_default: false },
  { name: 'incomplete', type: 'google', fields: { model: 'missing-key' }, configured: false, is_default: false },
];
let models = [{ value: 'main/chat', label: 'main / chat' }, { value: 'alt/org/router', label: 'alt / org/router' }];
const writes = [];
let failNextSave = false;
const server = await createServer({
  root: frontendRoot, configFile: path.join(frontendRoot, 'vite.config.ts'), base: '/',
  optimizeDeps: { entries: ['tests/fixtures/decisions.tsx'] },
  plugins: [{
    name: 'decision-settings-test-page',
    configureServer(vite) {
      vite.middlewares.use(async (req, res, next) => {
        const url = new URL(req.url, 'http://localhost');
        if (url.pathname === '/__decisions' || url.pathname === '/settings/providers') {
          res.setHeader('Content-Type', 'text/html');
          res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/fixtures/decisions.tsx"></script></body></html>');
          return;
        }
        if (!url.pathname.startsWith('/dashboard-api/')) return next();
        res.setHeader('Content-Type', 'application/json');
        res.setHeader('X-CSRF-Token', 'test-token');
        if (url.pathname !== '/dashboard-api/decisions') { res.end('{}'); return; }
        if (req.method === 'PUT') {
          const body = JSON.parse(await new Promise(resolve => {
            let value = ''; req.on('data', chunk => value += chunk); req.on('end', () => resolve(value));
          }));
          writes.push(body.values);
          if (failNextSave) {
            failNextSave = false; res.statusCode = 400; res.end('{"detail":"Connection changed before save."}'); return;
          }
          for (const [key, value] of Object.entries(body.values)) settings[key].value = value;
          res.end('{"status":"success"}'); return;
        }
        await new Promise(resolve => setTimeout(resolve, 50));
        res.end(JSON.stringify({
          settings, by_category: {}, settings_sources: {}, active_nav: 'decisions',
          decision_providers: { providers, services: [], default_provider: settings['decision.default_provider'].value },
          llm_model_options: models,
        }));
      });
    },
  }],
  server: { host: '127.0.0.1', port: 0, open: false },
});
await server.listen();
const base = `http://127.0.0.1:${server.httpServer.address().port}`;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-decisions-'));
const browser = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run',
  '--disable-background-networking', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'],
{ windowsHide: true, stdio: 'ignore' });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(callback, label, timeout = 30000) {
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
      const entry = pending.get(message.id); pending.delete(message.id);
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
  const pick = async (label, text) => {
    await js(`document.querySelector('button[aria-label=${JSON.stringify(label)}]').click()`);
    await until(() => js(`Array.from(document.querySelectorAll('[role=option]')).some(option => option.textContent.trim() === ${JSON.stringify(text)})`), `option ${text}`);
    await js(`Array.from(document.querySelectorAll('[role=option]')).find(option => option.textContent.trim() === ${JSON.stringify(text)}).click()`);
  };
  const decision = name => js(`{ const select = document.querySelector('select[aria-label="Decision Model"]'); select.value = ${JSON.stringify(name)}; select.dispatchEvent(new Event('change', { bubbles: true })); }`);
  const click = text => js(`Array.from(document.querySelectorAll('button')).find(button => button.textContent.trim() === ${JSON.stringify(text)}).click()`);
  const save = async count => {
    await click('Save Changes');
    await until(() => js('!!document.querySelector("[role=dialog]")'), 'confirmation');
    await click(`Confirm Save (${count})`);
  };
  const decisionSelect = `document.querySelector('select[aria-label="Decision Model"]')`;
  const llmSelect = `document.querySelector('button[aria-label="Router LLM Model"]')`;
  const reload = async () => {
    const previous = await js('performance.timeOrigin');
    await call('Page.reload');
    await until(() => js(`performance.timeOrigin !== ${previous} && document.readyState === 'complete'`), 'new document');
  };
  await call('Page.enable');
  await call('Runtime.enable');
  await call('Page.navigate', { url: base + '/__decisions' });
  await until(() => js(`${decisionSelect}?.value === "team"`), 'saved decision after async load');
  assert.equal(await js('document.querySelector("option[value=incomplete]").disabled'), true);
  assert.equal(await js(`${llmSelect}.textContent.trim()`), 'Use current agent model selection');
  await decision('google');
  await pick('Router LLM Model', 'alt / org/router');
  await pick('Router Decision Strategy', 'llm_only');
  assert.equal(await js('!!document.querySelector("[data-decision-model-control]")'), false);
  await pick('Router Decision Strategy', 'clef_only');
  assert.equal(await js('!!document.querySelector("[data-router-llm-control]")'), false);
  assert.equal(await js(`${decisionSelect}.value`), 'google');
  await pick('Router Decision Strategy', 'cascade');
  assert.equal(await js(`${llmSelect}.textContent.trim()`), 'alt / org/router');
  await click('Discard');
  assert.equal(await js(`${decisionSelect}.value`), 'team');
  assert.equal(await js('!!document.querySelector(".settings-pending-bar")'), false);

  await decision('google');
  await pick('Router LLM Model', 'alt / org/router');
  failNextSave = true;
  await save(2);
  await until(() => js('document.body.textContent.includes("Connection changed before save.")'), 'failed save retains draft');
  assert.equal(await js('!!document.querySelector(".settings-pending-bar")'), true);
  await click('Confirm Save (2)');
  await until(() => js('!document.querySelector(".settings-pending-bar") && !document.querySelector("[role=dialog]")'), 'saved changes');
  assert.deepEqual(writes.at(-1), { 'decision.default_provider': 'google', 'decision.router.llm_model': 'alt/org/router' });
  await reload();
  await until(() => js(`${decisionSelect}?.value === "google"`), 'persisted decision');
  assert.equal(await js(`${llmSelect}.textContent.trim()`), 'alt / org/router');
  await pick('Router LLM Model', 'Use current agent model selection');
  await pick('Router Decision Strategy', 'llm_only');
  await save(2);
  await until(() => js('!document.querySelector(".settings-pending-bar")'), 'restored inherited model');
  assert.equal(settings['decision.default_provider'].value, 'google');
  assert.equal(settings['decision.router.llm_model'].value, '');

  providers = []; models = [];
  settings['decision.router.mode'].value = 'cascade';
  settings['decision.router.llm_model'].value = 'removed/old-model';
  await reload();
  await until(() => js('document.body.textContent.includes("No configured LLM models available.")'), 'empty configured models');
  assert.equal(await js(`${decisionSelect}.selectedOptions[0].disabled`), true);
  assert.equal(await js(`${llmSelect}.textContent.includes("unavailable")`), true);
  assert.equal(await js('document.body.textContent.includes("No configured Decision connections.")'), true);
  assert.equal(await js('!!document.querySelector(".settings-pending-bar")'), false);
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await until(() => js('innerWidth === 390'), 'mobile viewport');
  assert.equal(await js('document.documentElement.scrollWidth <= innerWidth'), true);
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: configured model dropdowns, mode switching, discard, atomic save, failed save, reload, and unavailable selections.');
} finally {
  if (call && ws?.readyState === WebSocket.OPEN) await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws?.close(); browser.kill();
  await server.close();
  const resolvedProfile = path.resolve(profile);
  assert.equal(path.dirname(resolvedProfile), path.resolve(os.tmpdir()));
  assert.ok(path.basename(resolvedProfile).startsWith('k41-decisions-'));
  fs.rmSync(resolvedProfile, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
}
