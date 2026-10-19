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
const realDiagrams = process.argv.includes('--real-diagrams');
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
        if (['markdown-harness', 'pele/lazy'].includes(id) || (!realDiagrams && id === 'mermaid')) return `\0${id}`;
      },
      load(id) {
        if (id === '\0markdown-harness') return `
          import { createSignal } from 'solid-js';
          import { render } from 'solid-js/web';
          import DOMPurify from 'dompurify';
          import { marked } from 'marked';
          import { Markdown } from '@/components/Markdown';
          import tokenStyles from '@/styles/tokens.css?inline';
          import markdownStyles from '@/styles/markdown.css?inline';
          const style = document.createElement('style');
          style.textContent = tokenStyles + '\\n' + markdownStyles;
          document.head.appendChild(style);
          window.highlightRequests = []; window.mermaidRequests = []; window.peleRequests = [];
          window.mermaidLoads = 0;
          window.sanitizedInputs = [];
          window.lexedLengths = []; window.markdownRootQueries = [];
          const lex = marked.lexer;
          marked.lexer = (source, ...args) => { window.lexedLengths.push(source.length); return lex(source, ...args); };
          const queryAll = Element.prototype.querySelectorAll;
          Element.prototype.querySelectorAll = function(selector) {
            if (this.classList.contains('markdown')) window.markdownRootQueries.push(selector);
            return queryAll.call(this, selector);
          };
          const sanitize = DOMPurify.sanitize.bind(DOMPurify);
          DOMPurify.sanitize = (...args) => { window.sanitizedInputs.push(String(args[0])); return sanitize(...args); };
          window.escapeHtml = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
          const [text, setText] = createSignal('');
          const [deferHighlight, setDeferHighlight] = createSignal(false);
          const [deferMermaid, setDeferMermaid] = createSignal(false);
          Object.assign(window, { setText, setDeferHighlight, setDeferMermaid });
          window.disposeMarkdown = render(() => Markdown({
            class: 'message-markdown',
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
        if (id === '\0pele/lazy' && realDiagrams) return `
          import { renderAsync as renderPele } from ${JSON.stringify(fileURLToPath(import.meta.resolve('pele/lazy')).replaceAll('\\', '/'))};
          export const renderAsync = (source, options) => window.forceMermaid
            ? Promise.reject(new Error('Simulated Pele failure')) : renderPele(source, options);
        `;
        if (id === '\0pele/lazy') return `
          export const renderAsync = (source, options) => new Promise((resolve, reject) => {
            window.peleRequests.push({ source, options, reject,
              resolve: (svg = '<svg class="pele"><text fill="var(--pele-fg)">' + window.escapeHtml(source) + '</text></svg>') => resolve({ svg }) });
          });
        `;
        if (id === '\0mermaid') return `
          window.mermaidLoads += 1;
          let config;
          export default {
            initialize(options) { config = options; },
            render: (id, source, container) => new Promise((resolve, reject) => {
              const temporary = document.createElement('div');
              temporary.id = 'd' + id;
              (container || document.body).appendChild(temporary);
              window.mermaidRequests.push({ source, container, config,
                reject: error => {
                  temporary.innerHTML = '<svg><text>Syntax error in text</text></svg>';
                  reject(error);
                },
                resolve: () => {
                  temporary.remove();
                  resolve({ svg: '<svg><text>' + window.escapeHtml(source) + '</text></svg>' });
                } });
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
  const assertNoDiagramArtifacts = async () => {
    assert.deepEqual(await js('Array.from(document.querySelectorAll("body > :not(#app):not(script):not(.mermaidTooltip)"), element => element.outerHTML.slice(0, 500))'), [],
      'diagram rendering must not leave temporary containers or error SVGs outside the chat');
  };
  const waitRequests = async (name, count) => {
    try { await until(() => js(`${name}.length === ${count}`), `${name}: ${count}`); }
    catch (error) {
      console.error(await js('({ text: document.getElementById("app").textContent, highlights: highlightRequests.length, pele: peleRequests.length, mermaid: mermaidRequests.length })'), exceptions);
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

  if (realDiagrams) {
    for (const [type, source] of [
      ['flowchart', 'flowchart LR\nA[Request] --> B[Answer]'],
      ['sequence', 'sequenceDiagram\nA->>B: Request\nB-->>A: Answer'],
      ['class', 'classDiagram\nAgent --> Tool'],
      ['state', 'stateDiagram-v2\n[*] --> Idle\nIdle --> Done\nDone --> [*]'],
      ['er', 'erDiagram\nUSER ||--o{ MESSAGE : sends'],
      ['mindmap', 'mindmap\n  root((Agent))\n    Tools\n    Memory'],
      ['gantt', 'gantt\n  dateFormat YYYY-MM-DD\n  section Work\n  Research :2026-10-19, 2d'],
      ['pie', 'pie\n  "Input" : 40\n  "Output" : 60'],
    ]) {
      await setText(fence('mermaid', source));
      await until(() => js(`!!document.querySelector('.markdown-mermaid svg.pele-${type}')`), `real ${type} diagram`);
      assert.equal(await js('document.querySelector(".markdown-mermaid-frame").dataset.diagramRenderer'), 'pele');
    }
    await setText(fence('mmd', 'flowchart LR\nA[Custom]:::custom --> B[Default]\nclassDef custom fill:#112233,color:#ffffff'));
    await until(() => js('!!document.querySelector(".markdown-mermaid svg.pele-flowchart")'), 'styled diagram');
    await js('window.mermaidFrame = document.querySelector(".markdown-mermaid-frame"); window.peleSvg = mermaidFrame.querySelector(".markdown-mermaid svg")');
    assert.equal(await js('getComputedStyle(peleSvg.querySelector(\'[data-id="A"] text\')).fill'), 'rgb(255, 255, 255)');
    assert.equal(await js('getComputedStyle(peleSvg.querySelector(\'[data-id="B"] text\')).fill'), 'rgb(10, 10, 10)');
    await js('document.documentElement.classList.add("dark")');
    await until(() => js('mermaidFrame.dataset.mermaidTheme === "dark"'), 'dark diagram theme');
    assert.equal(await js('peleSvg === mermaidFrame.querySelector(".markdown-mermaid svg")'), true);
    assert.equal(await js('getComputedStyle(peleSvg.querySelector(\'[data-id="B"] text\')).fill'), 'rgb(250, 250, 250)');
    await js('document.documentElement.classList.remove("dark"); mermaidFrame.querySelector(".markdown-mermaid-toggle").click()');
    await js('window.forceMermaid = true');
    await setText(fence('mermaid', 'flowchart LR\nA[Mermaid] --> B[Fallback]'));
    await until(() => js('mermaidFrame.dataset.diagramRenderer === "mermaid"'), 'real Mermaid fallback');
    assert.equal(await js('mermaidFrame.querySelector(".markdown-mermaid svg").viewBox.baseVal.width > 0 && mermaidFrame.querySelector(".markdown-mermaid svg").viewBox.baseVal.height > 0'), true,
      'hidden rendering must preserve diagram measurements');
    await assertNoDiagramArtifacts();
    await js('window.forceMermaid = false');
    await setText(fence('mermaid', 'flowchart LR\nA[Updated] --> B[Answer]'));
    await until(() => js('!!mermaidFrame.querySelector(".markdown-mermaid svg")?.textContent.includes("Updated")'), 'real source update');
    assert.equal(await js('mermaidFrame.dataset.mermaidView'), 'source');
    await setText(fence('mermaid', 'flowchart LR\nA -->'));
    await until(() => js('mermaidFrame.classList.contains("markdown-mermaid-frame-error")'), 'invalid source fallback');
    assert.equal(await js('mermaidFrame.dataset.mermaidView'), 'source');
    await assertNoDiagramArtifacts();
    await setText(['invalid diagram', 'flowchart LR\nA -->', 'sequenceDiagram\nA->>']
      .map(source => fence('mermaid', source)).join('\n\n') + '\n\n' + fence('mermaid', 'flowchart LR\nA[Valid] --> B[Neighbor]') + '\n\nChat remains usable.');
    await until(() => js('document.querySelectorAll(".markdown-mermaid-frame-error").length === 3 && !!document.querySelector("svg.pele")'), 'multiple invalid diagrams with a valid neighbor');
    assert.equal(await js('document.querySelectorAll(\'.markdown-mermaid-frame-error[data-mermaid-view="source"]\').length'), 3);
    assert.equal(await js('document.querySelector("#app p:last-child").textContent'), 'Chat remains usable.');
    await assertNoDiagramArtifacts();
    await setText(fence('mermaid', 'flowchart LR\nA[Recovered] --> B[Answer]'));
    await until(() => js('!!mermaidFrame.querySelector("svg.pele")?.textContent.includes("Recovered")'), 'real diagram recovery');
    console.log('PASS: real renderers preserve diagram measurements and themes, isolate simultaneous syntax errors without leaking SVGs, and recover after invalid source.');
  } else {
    await setText(fence('mermaid', 'graph TD\nA-->B'));
    await waitRequests('peleRequests', 1);
    assert.equal(await js('mermaidLoads'), 0);
    assert.equal(await js('peleRequests[0].options.links'), false);
    assert.equal(await js('peleRequests[0].options.images'), false);
    assert.equal(await js('peleRequests[0].options.maxEdges'), 500);
    await js('peleRequests[0].resolve()');
    await until(() => js('!!document.querySelector(".markdown-mermaid svg")'), 'initial diagram');
    assert.equal(await js('document.querySelector(".markdown-mermaid-frame").dataset.diagramRenderer'), 'pele');
    await js('window.mermaidFrame = document.querySelector(".markdown-mermaid-frame"); mermaidFrame.querySelector(".markdown-mermaid-toggle").click()');
    await setText(fence('mermaid', 'graph TD\nA-->C'));
    assert.equal(await js('!!mermaidFrame.querySelector(".markdown-mermaid svg")'), false);
    await waitRequests('peleRequests', 2);
    await setText(fence('mermaid', 'graph TD\nA-->D'));
    await js('peleRequests[1].resolve()');
    assert.equal(await js('!!mermaidFrame.querySelector(".markdown-mermaid svg")'), false);
    await waitRequests('peleRequests', 3);
    await js('peleRequests[2].resolve()');
    await until(() => js('mermaidFrame.querySelector(".markdown-mermaid svg")?.textContent.includes("A-->D")'), 'updated diagram');
    assert.equal(await js('mermaidFrame === document.querySelector(".markdown-mermaid-frame") && mermaidFrame.dataset.mermaidView === "source"'), true);
    assert.equal(await js('mermaidFrame.querySelector(".markdown-mermaid-raw code").textContent'), 'graph TD\nA-->D');
    assert.equal(await js('mermaidFrame.querySelectorAll(".markdown-code-frame").length'), 0);
    await js('setDeferMermaid(true)');
    await setText(fence('mmd', 'graph TD\nA-->E', false));
    await delay(800);
    assert.equal(await js('peleRequests.length'), 3);
    await js('setDeferMermaid(false)');
    await delay(800);
    assert.equal(await js('peleRequests.length'), 3);
    await setText(fence('mmd', 'graph TD\nA-->E'));
    await waitRequests('peleRequests', 4);
    await js('peleRequests[3].resolve()');
    await until(() => js('mermaidFrame.querySelector(".markdown-mermaid svg")?.textContent.includes("A-->E")'), 'completed fence');
    await js('window.peleSvg = mermaidFrame.querySelector(".markdown-mermaid svg"); document.documentElement.classList.add("dark")');
    await until(() => js('mermaidFrame.dataset.mermaidTheme === "dark"'), 'Pele theme update');
    assert.equal(await js('peleSvg === mermaidFrame.querySelector(".markdown-mermaid svg") && peleRequests.length === 4'), true);
    assert.equal(await js('getComputedStyle(peleSvg.querySelector("text")).fill'), 'rgb(250, 250, 250)');
    await js('document.documentElement.classList.remove("dark")');
    await until(() => js('mermaidFrame.dataset.mermaidTheme === "light"'), 'Pele light theme');

    await setText(fence('mermaid', 'graph TD\nA-->F'));
    await waitRequests('peleRequests', 5);
    await setText(fence('mermaid', 'graph TD\nA-->G'));
    await js('peleRequests[4].reject(new Error("stale Pele failure"))');
    await waitRequests('peleRequests', 6);
    assert.equal(await js('mermaidLoads'), 0, 'stale Pele failures must not load Mermaid');
    await js('peleRequests[5].resolve()');
    await until(() => js('mermaidFrame.querySelector(".markdown-mermaid svg")?.textContent.includes("A-->G")'), 'current Pele diagram');
    assert.equal(await js('new Set(peleRequests.map(request => request.options.idPrefix)).size'), 6);

    await setText(fence('mermaid', 'graph TD\nA-->H'));
    await waitRequests('peleRequests', 7);
    await js('peleRequests[6].reject(new Error("Pele unsupported syntax"))');
    await waitRequests('mermaidRequests', 1);
    assert.equal(await js('mermaidLoads'), 1);
    assert.equal(await js('mermaidRequests[0].config.suppressErrorRendering'), true);
    assert.equal(await js('mermaidRequests[0].container?.isConnected && getComputedStyle(mermaidRequests[0].container).visibility === "hidden" && getComputedStyle(mermaidRequests[0].container).position === "fixed"'), true,
      'Mermaid must render inside a hidden container outside the chat layout');
    await js('mermaidRequests[0].resolve()');
    await until(() => js('mermaidFrame.dataset.diagramRenderer === "mermaid"'), 'Mermaid fallback');
    await assertNoDiagramArtifacts();
    assert.equal(await js('mermaidFrame.dataset.mermaidView'), 'source');

    await js('document.documentElement.classList.add("dark")');
    await waitRequests('peleRequests', 8);
    await js('peleRequests[7].reject(new Error("Pele unsupported syntax"))');
    await waitRequests('mermaidRequests', 2);
    await js('mermaidRequests[1].resolve()');
    await until(() => js('mermaidFrame.dataset.diagramRenderer === "mermaid" && mermaidFrame.dataset.mermaidTheme === "dark"'), 'fallback dark theme');

    await setText(fence('mermaid', 'graph TD\nA-->I'));
    await waitRequests('peleRequests', 9);
    await js('peleRequests[8].reject(new Error("Pele failure"))');
    await waitRequests('mermaidRequests', 3);
    await setText(fence('mermaid', 'graph TD\nA-->J'));
    await js('mermaidRequests[2].resolve()');
    assert.equal(await js('!!mermaidFrame.querySelector(".markdown-mermaid svg")'), false, 'stale Mermaid results must be ignored');
    await assertNoDiagramArtifacts();
    await waitRequests('peleRequests', 10);
    await js('peleRequests[9].resolve()');
    await until(() => js('mermaidFrame.querySelector(".markdown-mermaid svg")?.textContent.includes("A-->J")'), 'Pele after stale fallback');

    await setText(fence('mermaid', 'invalid diagram'));
    await waitRequests('peleRequests', 11);
    await js('peleRequests[10].reject(new Error("Pele syntax error"))');
    await waitRequests('mermaidRequests', 4);
    await js('mermaidRequests[3].reject(new Error("Mermaid syntax error"))');
    await until(() => js('mermaidFrame.classList.contains("markdown-mermaid-frame-error")'), 'both renderers failed');
    assert.equal(await js('mermaidFrame.dataset.mermaidView'), 'source');
    assert.equal(await js('mermaidFrame.querySelector(".markdown-mermaid-raw code").textContent'), 'invalid diagram');
    await assertNoDiagramArtifacts();

    await setText(fence('mermaid', 'graph TD\nA-->K'));
    await waitRequests('peleRequests', 12);
    await js('peleRequests[11].resolve(\'<svg class="pele" onload="window.unsafeDiagram = true"><script>window.unsafeDiagram = true</script><text>Recovered</text></svg>\')');
    await until(() => js('mermaidFrame.querySelector(".markdown-mermaid svg")?.textContent === "Recovered"'), 'sanitized diagram recovery');
    assert.equal(await js('mermaidFrame.classList.contains("markdown-mermaid-frame-error")'), false);
    assert.equal(await js('!!mermaidFrame.querySelector("svg[onload], svg script") || window.unsafeDiagram === true'), false);
    await js('document.documentElement.classList.remove("dark")');
    await until(() => js('mermaidFrame.dataset.mermaidTheme === "light"'), 'recovered diagram light theme');
    console.log('PASS: Pele is the default, Mermaid loads only on failure, stale results and failures are ignored, and SVG output is sanitized.');
  }

  await js('setDeferHighlight(true)');
  const diagramRequestsBeforeStream = await js('peleRequests.length');
  const history = Array.from({ length: 48 }, (_, index) => `Completed paragraph ${index}. ${'Stable context '.repeat(24)}\n\n`).join('');
  const streamingDiagram = 'flowchart LR\nS[Streaming] --> T[Complete]';
  const streamedPrefix = history + fence('mermaid', streamingDiagram) + '\n\n';
  const nextFrames = () => js('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
  await setText(history + fence('mermaid', streamingDiagram, false));
  await nextFrames();
  assert.equal(await js('document.querySelectorAll(".markdown-mermaid svg").length'), 0, 'an open fence must not render');
  assert.equal(await js('peleRequests.length'), diagramRequestsBeforeStream);
  await setText(streamedPrefix + 'Tail');
  await nextFrames();
  if (!realDiagrams) {
    assert.equal(await js('peleRequests.length'), diagramRequestsBeforeStream + 1, 'a closed fence must start rendering in the next frame');
    await js(`peleRequests[${diagramRequestsBeforeStream}].resolve()`);
  }
  await until(() => js('!!document.querySelector(".markdown-mermaid svg.pele")'), 'diagram before stream completion');
  await js('window.streamingSvg = document.querySelector(".markdown-mermaid svg")');
  const measureTail = prefix => js(`(async () => {
    const samples = [];
    const stableParagraph = document.querySelector('.markdown p');
    let stableMutations = 0;
    const observer = new MutationObserver(records => { stableMutations += records.length; });
    observer.observe(stableParagraph, { childList: true, characterData: true, attributes: true, subtree: true });
    observer.observe(streamingSvg, { childList: true, characterData: true, attributes: true, subtree: true });
    lexedLengths.length = 0; markdownRootQueries.length = 0; sanitizedInputs.length = 0;
    for (let index = 0; index < 100; index++) {
      const started = performance.now();
      setText(${JSON.stringify(prefix)} + 'Tail ' + 'streamed text '.repeat(index + 1));
      await Promise.resolve();
      samples.push(performance.now() - started);
      await new Promise(requestAnimationFrame);
    }
    observer.disconnect();
    samples.sort((a, b) => a - b);
    return {
      sourceChars: ${prefix.length},
      updates: samples.length,
      meanMs: samples.reduce((sum, value) => sum + value, 0) / samples.length,
      p95Ms: samples[Math.floor(samples.length * 0.95)],
      svgPreserved: streamingSvg === document.querySelector('.markdown-mermaid svg'),
      maxLexedChars: Math.max(...lexedLengths),
      rootQueries: markdownRootQueries.length,
      sanitizedBlocks: sanitizedInputs.length,
      stableMutations,
    };
  })()`);
  const assertTailWork = metrics => {
    assert.equal(metrics.svgPreserved, true, 'streamed paragraphs must preserve the completed SVG');
    assert.ok(metrics.maxLexedChars < 1500, 'only the mutable tail should be lexed');
    assert.equal(metrics.rootQueries, 0, 'tail updates must not scan the Markdown container');
    assert.equal(metrics.sanitizedBlocks, 100, 'only the changed block should be sanitized');
    assert.equal(metrics.stableMutations, 0, 'completed paragraphs and SVGs must not be mutated');
  };
  const streamMetrics = await measureTail(streamedPrefix);
  assertTailWork(streamMetrics);
  assert.equal(streamMetrics.svgPreserved, true, 'streamed paragraphs must preserve the completed SVG');
  if (!realDiagrams) assert.equal(await js('peleRequests.length'), diagramRequestsBeforeStream + 1, 'tail updates must not rerender a completed diagram');
  await setText(streamedPrefix + fence('mmd', streamingDiagram, false));
  await nextFrames();
  assert.equal(await js('document.querySelectorAll(".markdown-mermaid svg").length'), 1, 'an identical unfinished block must not reuse an earlier closing fence');
  await setText(streamedPrefix + fence('mmd', streamingDiagram));
  await nextFrames();
  if (!realDiagrams) {
    assert.equal(await js('peleRequests.length'), diagramRequestsBeforeStream + 2);
    await js(`peleRequests[${diagramRequestsBeforeStream + 1}].resolve()`);
  }
  await until(() => js('document.querySelectorAll(".markdown-mermaid svg.pele").length === 2'), 'second completed diagram during streaming');

  const duplicateRequests = await js('peleRequests.length');
  await setText([
    '- ```mermaid',
    '  flowchart LR',
    '  D[Duplicate] --> E[Diagram]',
    '',
    '- ```mermaid',
    '  flowchart LR',
    '  D[Duplicate] --> E[Diagram]',
    '  ```',
    '',
  ].join('\n'));
  if (!realDiagrams) {
    await waitRequests('peleRequests', duplicateRequests + 1);
    await js(`peleRequests[${duplicateRequests}].resolve()`);
  }
  await until(() => js('document.querySelectorAll(".markdown-mermaid svg").length === 1'), 'the closed duplicate fence renders');
  assert.equal(await js('!!document.querySelectorAll(".markdown ul > li")[0]?.querySelector("pre.markdown-mermaid-source")'), true,
    'the unclosed duplicate must remain source');
  assert.equal(await js('!!document.querySelectorAll(".markdown ul > li")[1]?.querySelector(".markdown-mermaid-frame")'), true,
    'the closed duplicate must render');

  const bothClosedRequests = await js('peleRequests.length');
  await setText([
    '- ```mermaid',
    '  flowchart LR',
    '  D[Duplicate] --> E[Diagram]',
    '  ```',
    '',
    '- ```mermaid',
    '  flowchart LR',
    '  D[Duplicate] --> E[Diagram]',
    '  ```',
    '',
  ].join('\n'));
  if (!realDiagrams) {
    await waitRequests('peleRequests', bothClosedRequests + 1);
    await js(`peleRequests[${bothClosedRequests}].resolve()`);
  }
  await until(() => js('document.querySelectorAll(".markdown-mermaid svg").length === 2'), 'both duplicate fences render after closing');
  assert.equal(await js('document.querySelectorAll(".markdown ul > li .markdown-mermaid-frame").length'), 2,
    'closing a fence with unchanged HTML must promote its source to a diagram');

  const nestedPrefix = [
    '- Diagram item',
    '',
    '  ```mermaid',
    '  flowchart LR',
    '  N[Nested] --> O[Stable]',
    '  ```',
    '',
    '- Next item',
  ].join('\n');
  const nestedRequests = await js('peleRequests.length');
  await setText(nestedPrefix);
  if (!realDiagrams) {
    await waitRequests('peleRequests', nestedRequests + 1);
    await js(`peleRequests[${nestedRequests}].resolve()`);
  }
  await until(() => js('!!document.querySelector(".markdown-mermaid-frame svg.pele")'), 'nested diagram');
  await js('window.nestedFrame = document.querySelector(".markdown-mermaid-frame"); window.nestedSvg = nestedFrame.querySelector(".markdown-mermaid svg"); nestedFrame.querySelector(".markdown-mermaid-toggle").click()');
  assert.equal(await js('nestedFrame.dataset.mermaidView'), 'source');
  for (let index = 0; index < 5; index++) {
    await setText(`${nestedPrefix} grows ${index}`);
    await nextFrames();
  }
  assert.equal(await js('nestedFrame === document.querySelector(".markdown-mermaid-frame") && nestedSvg === document.querySelector(".markdown-mermaid svg")'), true,
    'a nested diagram must survive its list growing');
  assert.equal(await js('nestedFrame.dataset.mermaidView'), 'source', 'the selected diagram view must survive list updates');
  if (!realDiagrams) assert.equal(await js('peleRequests.length'), nestedRequests + 1, 'a nested diagram must not rerender while its source is unchanged');

  await js('setDeferHighlight(false)');
  console.log(`PASS: completed fences render during streaming, unfinished duplicates stay deferred, and 100 tail updates preserve the SVG (${realDiagrams ? 'real Pele' : 'mock'}: ${JSON.stringify(streamMetrics)}).`);

  for (const paragraphs of [240, 960]) {
    const history = Array.from({ length: paragraphs }, (_, index) => `Completed paragraph ${index}. ${'Stable context '.repeat(24)}\n\n`).join('');
    const prefix = history + fence('mermaid', streamingDiagram) + '\n\n';
    await setText('');
    const requests = await js('peleRequests.length');
    await setText(prefix + 'Tail');
    await nextFrames();
    if (!realDiagrams) {
      await waitRequests('peleRequests', requests + 1);
      await js(`peleRequests[${requests}].resolve()`);
    }
    await until(() => js('!!document.querySelector(".markdown-mermaid svg.pele")'), 'long message diagram');
    await js('window.streamingSvg = document.querySelector(".markdown-mermaid svg")');
    const metrics = await measureTail(prefix);
    assertTailWork(metrics);
    console.log(`PASS: ${paragraphs} completed paragraphs stay untouched during 100 tail updates (${realDiagrams ? 'real Pele' : 'mock'}: ${JSON.stringify(metrics)}).`);
  }

  await setText(fence('js', 'const afterDiagram = true;'));
  await waitRequests('highlightRequests', 6);
  assert.equal(await js('document.querySelectorAll(".markdown-mermaid-frame").length'), 0);

  await js('setDeferHighlight(true)');
  const prefix = '# Stable heading\n\nCompleted **paragraph**.\n\n';
  await setText(prefix + 'Tail');
  await js('window.stableHeading = document.querySelector(".markdown h1"); sanitizedInputs.length = 0');
  for (const tail of ['Tail grows', 'Tail grows again', 'Tail grows **again**']) await setText(prefix + tail);
  assert.equal(await js('sanitizedInputs.length'), 3, 'only the changing block is sanitized during streaming');
  assert.equal(await js('stableHeading === document.querySelector(".markdown h1")'), true, 'completed blocks retain their DOM');
  await setText('[reference][target]\n\nParagraph');
  assert.equal(await js('document.querySelector(".markdown a")'), null);
  await setText('[reference][target]\n\nParagraph\n\n[target]: https://example.com');
  assert.equal(await js('document.querySelector(".markdown a").href'), 'https://example.com/');
  for (const [text, tags] of [
    ['# Heading\n\nTail', ['H1', 'P']],
    ['# Heading\n---\nTail', ['H1', 'HR', 'P']],
    ['# Heading\n\nTail', ['H1', 'P']],
    ['[target]: https://example.com\n\nTail', ['P']],
    ['# Heading\n---\nTail', ['H1', 'HR', 'P']],
  ]) {
    await setText(text);
    assert.deepEqual(await js('Array.from(document.querySelector(".markdown").children, element => element.tagName)'), tags,
      'block insertion and removal must retain document order when empty tokens are reclassified');
  }
  await setText('<div class="outer">\n\n**Nested content**\n\n<img src="x" onerror="window.unsafeMarkdown = true">\n\n</div>');
  assert.equal(await js('document.querySelector(".markdown .outer strong").textContent'), 'Nested content');
  assert.equal(await js('document.querySelector(".markdown img").hasAttribute("onerror")'), false);
  console.log('PASS: streamed blocks reuse sanitized output and DOM, late references update, and raw HTML retains nesting with unsafe attributes removed.');

  if (!realDiagrams) {
    const peleCount = await js('peleRequests.length');
    const mermaidCount = await js('mermaidRequests.length');
    await setText(fence('mermaid', 'invalid diagram after unmount'));
    await waitRequests('peleRequests', peleCount + 1);
    await js(`peleRequests[${peleCount}].reject(new Error("Pele failure"))`);
    await waitRequests('mermaidRequests', mermaidCount + 1);
    await js('disposeMarkdown(); highlightRequests[5].resolve()');
    await js(`mermaidRequests[${mermaidCount}].reject(new Error("Mermaid failure after unmount"))`);
  } else {
    await js('disposeMarkdown(); highlightRequests[5].resolve()');
  }
  await assertNoDiagramArtifacts();
  assert.equal(await js('document.getElementById("app").childNodes.length'), 0);
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: diagram source updates retain source view, respect deferral and complete fences, allow switching block types, and clean up after unmount.');
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
