import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'vite';
import solidPlugin from 'vite-plugin-solid';

const root = fileURLToPath(new URL('../', import.meta.url));
const executable = process.env.BROWSER_EXECUTABLE || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].find(candidate => fs.existsSync(candidate));
assert.ok(executable, 'Set BROWSER_EXECUTABLE to run browser checks.');
const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-markdown-check-'));
const output = path.join(workspace, 'dist');
const profile = path.join(workspace, 'browser');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const fence = (language, source, complete = true) => `\`\`\`${language}\n${source}\n${complete ? '\`\`\`' : ''}`;
let server, browser, ws, call;

async function until(callback, label, timeout = 15000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (await callback()) return;
    await delay(25);
  }
  throw new Error(`Timeout: ${label}`);
}

try {
  await build({
    configFile: false, root, logLevel: 'error',
    resolve: { alias: { '@': path.join(root, 'src') } },
    plugins: [solidPlugin(), {
      name: 'markdown-test-harness',
      enforce: 'pre',
      resolveId(id) {
        if (id === 'markdown-harness' || id === 'mermaid') return `\0${id}`;
      },
      load(id) {
        if (id === '\0markdown-harness') return `
          import { createSignal } from 'solid-js';
          import { render } from 'solid-js/web';
          import { Markdown } from '@/components/Markdown';
          window.highlightRequests = []; window.mermaidRequests = [];
          window.escapeHtml = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
          const [text, setText] = createSignal('');
          const [deferHighlight, setDeferHighlight] = createSignal(false);
          const [deferMermaid, setDeferMermaid] = createSignal(false);
          Object.assign(window, { setText, setDeferHighlight, setDeferMermaid });
          window.disposeMarkdown = render(() => Markdown({
            get text() { return text(); },
            get deferHighlight() { return deferHighlight(); },
            get deferMermaid() { return deferMermaid(); },
          }), document.getElementById('app'));
        `;
        if (id.replaceAll('\\', '/').endsWith('/src/lib/codeHighlight.ts')) return `
          export const languageFromName = value => value;
          export const highlightCode = (source, language) => new Promise((resolve, reject) => {
            window.highlightRequests.push({ source, language, reject,
              resolve: () => resolve('<pre><code><span class="token">' + window.escapeHtml(source) + '</span></code></pre>') });
          });
        `;
        if (id === '\0mermaid') return `
          export default {
            initialize() {},
            render: (id, source) => new Promise((resolve, reject) => {
              window.mermaidRequests.push({ source, reject,
                resolve: () => resolve({ svg: '<svg><text>' + window.escapeHtml(source) + '</text></svg>' }) });
            }),
          };
        `;
      },
    }],
    build: { outDir: output, rollupOptions: { input: 'markdown-harness', output: { entryFileNames: 'harness.js' } } },
  });
  server = http.createServer((req, res) => {
    if (req.url === '/') {
      res.setHeader('Content-Type', 'text/html');
      res.end('<div id="app"></div><script type="module" src="/harness.js"></script>');
      return;
    }
    const target = path.resolve(output, '.' + new URL(req.url, 'http://localhost').pathname);
    if (!target.startsWith(output + path.sep)) { res.writeHead(403); res.end(); return; }
    try { res.setHeader('Content-Type', 'text/javascript'); res.end(fs.readFileSync(target)); }
    catch { res.writeHead(404); res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  browser = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run',
    '--disable-background-networking', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'],
  { windowsHide: true, stdio: 'ignore' });
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
  await call('Runtime.enable');
  const js = async expression => {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const setText = text => js(`setText(${JSON.stringify(text)})`);
  const waitRequests = async (name, count) => {
    try { await until(() => js(`${name}.length === ${count}`), `${name}: ${count}`); }
    catch (error) {
      console.error(await js('({ text: document.getElementById("app").textContent, highlights: highlightRequests.length, mermaid: mermaidRequests.length })'), exceptions);
      throw error;
    }
  };
  const codeText = () => js('document.querySelector(".markdown-code-frame pre code")?.textContent');
  await call('Page.navigate', { url: `http://127.0.0.1:${server.address().port}/` });
  await until(() => js('typeof setText === "function"'), 'Markdown mounted');

  await setText(fence('js', 'const a = 1;'));
  await waitRequests('highlightRequests', 1);
  await js('window.codeFrame = document.querySelector(".markdown-code-frame"); window.codePre = codeFrame.querySelector("pre")');
  await setText(fence('js', 'const a = 1;\nconst b = 2;'));
  await waitRequests('highlightRequests', 2);
  await js('highlightRequests[0].resolve()');
  assert.equal(await codeText(), 'const a = 1;\nconst b = 2;\n');
  assert.equal(await js('codePre.classList.contains("markdown-code-highlighted")'), false);
  await setText(fence('python', 'print("latest")'));
  await waitRequests('highlightRequests', 3);
  await js('highlightRequests[2].resolve(); highlightRequests[1].reject(new Error("stale failure"))');
  assert.equal(await codeText(), 'print("latest")\n');
  assert.equal(await js('codeFrame === document.querySelector(".markdown-code-frame") && codePre.classList.contains("markdown-code-highlighted") && !codePre.classList.contains("markdown-code-plain")'), true);
  assert.equal(await js('document.querySelector(".markdown-code-language").textContent'), 'Python');
  assert.equal(await js('codePre.querySelector("code").className'), 'language-python');
  await js('setDeferHighlight(true)');
  await setText(fence('js', 'const deferred = true;'));
  assert.equal(await js('highlightRequests.length'), 3);
  await js('setDeferHighlight(false)');
  await waitRequests('highlightRequests', 4);
  await js('setDeferHighlight(true)');
  await setText(fence('js', 'const deferred = false;'));
  await js('highlightRequests[3].resolve()');
  assert.equal(await codeText(), 'const deferred = false;\n');
  await js('setDeferHighlight(false)');
  await waitRequests('highlightRequests', 5);
  await js('highlightRequests[4].resolve()');
  assert.equal(await codeText(), 'const deferred = false;\n');
  console.log('PASS: code updates preserve frames and reject stale highlights and failures, including deferred updates.');

  await setText(fence('mermaid', 'graph TD\nA-->B'));
  await waitRequests('mermaidRequests', 1);
  await js('mermaidRequests[0].resolve()');
  await until(() => js('!!document.querySelector(".markdown-mermaid svg")'), 'initial diagram');
  await js('window.mermaidFrame = document.querySelector(".markdown-mermaid-frame"); mermaidFrame.querySelector(".markdown-mermaid-toggle").click()');
  await setText(fence('mermaid', 'graph TD\nA-->C'));
  assert.equal(await js('!!mermaidFrame.querySelector(".markdown-mermaid svg")'), false);
  await waitRequests('mermaidRequests', 2);
  await setText(fence('mermaid', 'graph TD\nA-->D'));
  await js('mermaidRequests[1].resolve()');
  assert.equal(await js('!!mermaidFrame.querySelector(".markdown-mermaid svg")'), false);
  await waitRequests('mermaidRequests', 3);
  await js('mermaidRequests[2].resolve()');
  await until(() => js('mermaidFrame.querySelector(".markdown-mermaid svg")?.textContent.includes("A-->D")'), 'updated diagram');
  assert.equal(await js('mermaidFrame === document.querySelector(".markdown-mermaid-frame") && mermaidFrame.dataset.mermaidView === "source"'), true);
  assert.equal(await js('mermaidFrame.querySelector(".markdown-mermaid-raw code").textContent'), 'graph TD\nA-->D');
  assert.equal(await js('mermaidFrame.querySelectorAll(".markdown-code-frame").length'), 0);
  await js('setDeferMermaid(true)');
  await setText(fence('mmd', 'graph TD\nA-->E', false));
  await delay(800);
  assert.equal(await js('mermaidRequests.length'), 3);
  await js('setDeferMermaid(false)');
  await delay(800);
  assert.equal(await js('mermaidRequests.length'), 3);
  await setText(fence('mmd', 'graph TD\nA-->E'));
  await waitRequests('mermaidRequests', 4);
  await js('mermaidRequests[3].resolve()');
  await until(() => js('mermaidFrame.querySelector(".markdown-mermaid svg")?.textContent.includes("A-->E")'), 'completed fence');
  await setText(fence('js', 'const afterDiagram = true;'));
  await waitRequests('highlightRequests', 6);
  assert.equal(await js('document.querySelectorAll(".markdown-mermaid-frame").length'), 0);
  await js('disposeMarkdown(); highlightRequests[5].resolve()');
  assert.equal(await js('document.getElementById("app").childNodes.length'), 0);
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: Mermaid source updates rerender without nested code frames, retain source view, respect deferral and complete fences, and allow switching block types.');
} finally {
  if (call && ws?.readyState === WebSocket.OPEN) await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws?.close();
  browser?.kill();
  if (server) {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
  const resolvedWorkspace = path.resolve(workspace);
  assert.equal(path.dirname(resolvedWorkspace), path.resolve(os.tmpdir()));
  assert.ok(path.basename(resolvedWorkspace).startsWith('k41-markdown-check-'));
  fs.rmSync(resolvedWorkspace, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
}
