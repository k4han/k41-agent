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
    name: 'workspace-test-page',
    configureServer(vite) {
      vite.middlewares.use((req, res, next) => {
        if (req.url !== '/__workspace') return next();
        res.setHeader('Content-Type', 'text/html');
        res.end('<!doctype html><html><body><div id="root"></div><script type="module" src="/tests/fixtures/workspace.tsx"></script></body></html>');
      });
    },
  }],
  server: { host: '127.0.0.1', port: 0, open: false },
});
await server.listen();
const base = `http://127.0.0.1:${server.httpServer.address().port}`;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-workspace-'));
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
  await call('Page.navigate', { url: base + '/__workspace' });

  await until(() => js('!!window.workspaceTest && document.querySelectorAll(".workspace-tree-row").length === 201'), 'initial workspace tree');
  await until(() => js(`!document.querySelector('button[aria-label="Refresh workspace"]').disabled`), 'initial workspace load');
  const row = path => `Array.from(document.querySelectorAll('.workspace-tree-row')).find(row => row.title === ${JSON.stringify(path)})`;
  const tab = title => `Array.from(document.querySelectorAll('.workspace-tab')).find(tab => tab.title === ${JSON.stringify(title)})`;
  const refreshDone = () => js(`!document.querySelector('button[aria-label="Refresh workspace"]').disabled`);

  await js(`${row('src')}.click()`);
  await until(() => js(`!!(${row('src/nested.txt')})`), 'expanded directory');
  await js(`window.originalRows = new Map(Array.from(document.querySelectorAll('.workspace-tree-row'), row => [row.title, row]));
    workspaceTest.hold('tree:'); workspaceTest.hold('changes:'); workspaceTest.updateEntries(); workspaceTest.refresh();`);
  await until(() => js('workspaceTest.pendingCount("tree:") === 1'), 'refresh in flight');
  assert.equal(await js('Array.from(document.querySelectorAll(".workspace-tree-row")).every(row => originalRows.get(row.title) === row)'), true, 'refresh must not remount rows while loading');
  await js(`${row('src')}.click(); workspaceTest.release('tree:'); workspaceTest.release('changes:');`);
  await until(refreshDone, 'refreshed tree');
  assert.equal(await js(`!!(${row('src/nested.txt')})`), false, 'refresh must not reopen a folder collapsed during loading');
  assert.equal(await js('Array.from(document.querySelectorAll(".workspace-tree-row")).every(row => originalRows.get(row.title) === row)'), true, 'reordered snapshots must preserve row identity');
  assert.equal(await js(`${row('file-0.txt')}.querySelector('.workspace-tree-meta').textContent`), '4.0 KB');
  assert.equal(await js('document.querySelectorAll(".workspace-tree-row")[1].title'), 'file-199.txt');
  await js('workspaceTest.removeEntry(); workspaceTest.refresh();');
  await until(() => js('document.querySelectorAll(".workspace-tree-row").length === 200'), 'removed tree entry');
  assert.equal(await js(`!!(${row('file-199.txt')})`), false);
  await until(refreshDone, 'removal refresh');
  console.log('PASS: 200 tree rows survive refresh and reorder, changed metadata updates, and removals and folder toggles remain correct.');

  const countBefore = await js('workspaceTest.requests.length');
  await js('workspaceTest.replaceWorkspaceObject(); workspaceTest.refresh("another-thread");');
  await delay(100);
  assert.equal(await js('workspaceTest.requests.length'), countBefore, 'equivalent workspace props and unrelated events must not refetch');
  await js(`workspaceTest.hold('file:file-0.txt'); ${row('file-0.txt')}.click();`);
  await until(() => js('workspaceTest.pendingCount("file:file-0.txt") === 1'), 'pending file preview');
  assert.equal(await js('getComputedStyle(document.querySelector(".workspace-tree-section")).display'), 'none');
  const rootsBefore = await js('workspaceTest.requests.filter(request => request.kind === "tree" && !request.path).length');
  await js('workspaceTest.refresh(); workspaceTest.refresh();');
  await until(refreshDone, 'coalesced refresh');
  assert.equal(await js('workspaceTest.requests.filter(request => request.kind === "tree" && !request.path).length'), rootsBefore + 1);
  await js('workspaceTest.release("file:file-0.txt")');
  await until(() => js('document.querySelector(".workspace-file-panel").textContent.includes("Preview of file-0.txt")'), 'file preview after refresh');
  await js(`${tab('Files Explorer')}.click()`);
  assert.equal(await js(`${row('file-0.txt')} === originalRows.get('file-0.txt')`), true, 'switching tabs must preserve the tree');
  assert.notEqual(await js('getComputedStyle(document.querySelector(".workspace-tree-section")).display'), 'none');
  console.log('PASS: tab switches retain the tree; duplicate events coalesce and pending file previews complete across refresh.');

  await js(`document.querySelector('button[aria-label="New tab"]').click();
    Array.from(document.querySelectorAll('.workspace-plus-menu-item')).find(item => item.textContent.includes('Git Changes')).click();
    workspaceTest.hold('diff:file-0.txt'); document.querySelector('.workspace-change-row').click();`);
  await until(() => js('workspaceTest.pendingCount("diff:file-0.txt") === 1'), 'pending diff');
  await js(`window.originalChange = document.querySelector('.workspace-change-row');
    workspaceTest.hold('changes:'); workspaceTest.refresh();`);
  await until(() => js('workspaceTest.pendingCount("changes:") === 1'), 'changes refresh');
  assert.equal(await js('document.querySelector(".workspace-change-row") === originalChange'), true, 'changes must remain mounted while refreshing');
  await js('workspaceTest.release("changes:"); workspaceTest.release("diff:file-0.txt");');
  await until(() => js('document.querySelector(".workspace-change-diff").textContent.includes("Diff of file-0.txt")'), 'diff after refresh');
  assert.equal(await js('document.querySelector(".workspace-change-row") === originalChange'), true);
  assert.equal(await js('document.querySelector(".workspace-change-additions").textContent.trim()'), '+5');
  console.log('PASS: Git changes retain their DOM and expanded diff during background refresh.');

  await js(`${tab('Files Explorer')}.click(); ${row('src')}.click();`);
  await until(() => js(`!!(${row('src/nested.txt')})`), 'expanded directory before workspace change');
  await js(`workspaceTest.hold('tree:'); workspaceTest.hold('file:file-1.txt'); workspaceTest.refresh(); ${row('file-1.txt')}.click();`);
  await until(() => js('workspaceTest.pendingCount("tree:") === 1 && workspaceTest.pendingCount("file:file-1.txt") === 1'), 'old workspace requests');
  // Allow new root requests through without completing the old response yet.
  await js('workspaceTest.setLocator("/next");');
  await until(() => js('workspaceTest.pendingCount("tree:") === 2'), 'new workspace request');
  await js('workspaceTest.release("tree:"); workspaceTest.release("file:file-1.txt");');
  await until(() => js('document.querySelectorAll(".workspace-tree-row").length === 1 && document.querySelector(".workspace-tree-row").title === "next.txt"'), 'new workspace tree');
  await until(refreshDone, 'new workspace load');
  assert.ok(await js('workspaceTest.aborted()') >= 2, 'workspace changes must abort old requests');
  assert.equal(await js('workspaceTest.requests.some(request => request.locator === "/next" && request.path === "src")'), false, 'expanded paths must not leak across workspaces');
  assert.equal(await js('document.querySelectorAll(".workspace-tab").length'), 1);
  assert.equal(await js('!!document.querySelector(".workspace-file-panel")'), false);
  const beforeUnmount = await js('workspaceTest.requests.length');
  await js('workspaceTest.refresh(); workspaceTest.unmount();');
  await delay(100);
  assert.equal(await js('workspaceTest.requests.length'), beforeUnmount, 'queued refresh must stop after unmount');
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: workspace changes cancel obsolete reads, reset tabs and expansion, and disposed explorers ignore queued refresh.');
} finally {
  if (call && ws?.readyState === WebSocket.OPEN) await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws?.close();
  browser.kill();
  await server.close();
  const resolvedProfile = path.resolve(profile);
  assert.equal(path.dirname(resolvedProfile), path.resolve(os.tmpdir()));
  assert.ok(path.basename(resolvedProfile).startsWith('k41-workspace-'));
  fs.rmSync(resolvedProfile, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
}
