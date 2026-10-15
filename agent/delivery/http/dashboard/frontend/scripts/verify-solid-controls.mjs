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

const server = await createServer({
  root: frontendRoot,
  configFile: path.join(frontendRoot, 'vite.config.ts'),
  base: '/',
  plugins: [{
    name: 'solid-controls-test-page',
    configureServer(vite) {
      vite.middlewares.use((req, res, next) => {
        if (req.url !== '/__solid-controls') return next();
        res.setHeader('Content-Type', 'text/html');
        res.end('<!doctype html><html><body><div id="root"></div><script type="module" src="/tests/fixtures/solid-controls.tsx"></script></body></html>');
      });
    },
  }],
  server: { host: '127.0.0.1', port: 0, open: false },
});
await server.listen();
const base = `http://127.0.0.1:${server.httpServer.address().port}`;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-solid-controls-'));
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
  await call('Page.enable');
  await call('Runtime.enable');
  await call('Page.navigate', { url: base + '/__solid-controls' });
  await until(() => js('!!window.solidControls && !!document.querySelector(".form-select-trigger")'), 'control fixtures');
  await js('document.querySelector(".form-select-trigger").click()');
  await until(() => js('document.querySelectorAll("[role=option]").length === 3'), 'multi-select options');
  await js('window.originalOptions = Array.from(document.querySelectorAll("[role=option]")); originalOptions[0].click()');
  assert.deepEqual(await js('solidControls.values()'), ['a']);
  assert.deepEqual(await js('originalOptions.map(option => [option.getAttribute("aria-selected"), option.disabled])'), [
    ['true', false], ['false', true], ['false', true],
  ]);
  assert.equal(await js('originalOptions.every((option, index) => document.querySelectorAll("[role=option]")[index] === option)'), true);
  await js('originalOptions[0].click()');
  assert.deepEqual(await js('solidControls.values()'), []);
  await js('solidControls.setValues(["b"])');
  assert.equal(await js('originalOptions[1].getAttribute("aria-selected")'), 'true');
  await js('solidControls.setLimit(2); originalOptions[0].click()');
  assert.deepEqual(await js('solidControls.values()'), ['b', 'a']);
  assert.equal(await js('document.querySelectorAll(".form-select-tag").length'), 2);
  await js(`document.querySelector('button[aria-label="Remove Beta"]').click()`);
  assert.deepEqual(await js('solidControls.values()'), ['a']);
  assert.equal(await js('originalOptions[1].disabled'), false);
  assert.equal(await js('originalOptions[2].disabled'), true);
  await js('document.querySelector(".form-select-trigger").click()');
  console.log('PASS: multi-select updates in place, allows deselection at its limit, and reacts to external changes.');

  await js('solidControls.openMcp()');
  await until(() => js('!!document.querySelector("input[placeholder=TOKEN_A]")'), 'first MCP target');
  await js('{ window.dialogBody = document.querySelector(".dialog-body"); const input = document.querySelector("input[placeholder=TOKEN_A]"); input.value = "old-secret"; input.dispatchEvent(new Event("input", { bubbles: true })); }');
  const targetSelect = 'Array.from(document.querySelectorAll(".dialog-body .field")).find(field => field.querySelector("label")?.textContent.trim() === "Install target").querySelector("select")';
  await js(`const select = ${targetSelect}; select.value = 'second'; select.dispatchEvent(new Event('change', { bubbles: true }));`);
  await until(() => js('!!document.querySelector("input[placeholder=TOKEN_B]")'), 'second MCP target');
  assert.equal(await js('!!document.querySelector("input[placeholder=TOKEN_A]")'), false);
  assert.equal(await js('document.querySelector(".dialog-body").textContent.includes("second-command")'), true);
  assert.equal(await js('document.querySelector("input[placeholder=TOKEN_B]").value'), '');
  assert.equal(await js('dialogBody === document.querySelector(".dialog-body")'), true);
  await js('{ const input = document.querySelector("input[placeholder=TOKEN_B]"); input.value = "new-secret"; input.dispatchEvent(new Event("input", { bubbles: true })); Array.from(document.querySelectorAll(".dialog-footer button")).find(button => button.textContent.trim() === "Install").click(); }');
  await until(() => js('solidControls.submissions.length === 1'), 'second MCP submission');
  assert.deepEqual(await js('({ target: solidControls.submissions[0].target_id, inputs: solidControls.submissions[0].input_values })'), {
    target: 'second', inputs: { TOKEN_B: 'new-secret' },
  });

  await js('solidControls.replaceServer()');
  await until(() => js('!!document.querySelector("input[placeholder=TOKEN_C]")'), 'replacement MCP server');
  assert.equal(await js('document.querySelector(".dialog-body").textContent.includes("Replacement description")'), true);
  assert.equal(await js('document.querySelector(".dialog-body").textContent.includes("replacement-command")'), true);
  assert.equal(await js(`(${targetSelect}).value`), 'replacement');
  await js('Array.from(document.querySelectorAll(".dialog-footer button")).find(button => button.textContent.trim() === "Cancel").click(); solidControls.openMcp();');
  await until(() => js('!!document.querySelector("input[placeholder=TOKEN_C]")'), 'reopened MCP dialog');
  assert.equal(await js('document.querySelector("input[placeholder=TOKEN_C]").value'), '');
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: MCP target and server changes update the form and submit only the current target inputs.');
} finally {
  if (call && ws?.readyState === WebSocket.OPEN) await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws?.close();
  browser.kill();
  await server.close();
  const resolvedProfile = path.resolve(profile);
  assert.equal(path.dirname(resolvedProfile), path.resolve(os.tmpdir()));
  assert.ok(path.basename(resolvedProfile).startsWith('k41-solid-controls-'));
  fs.rmSync(resolvedProfile, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
}
