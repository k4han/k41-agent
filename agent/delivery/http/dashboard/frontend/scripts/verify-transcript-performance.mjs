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
const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-transcript-check-'));
const output = path.join(workspace, 'dist');
const profile = path.join(workspace, 'browser');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
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
      name: 'transcript-test-harness',
      resolveId(id) { if (id === 'transcript-harness') return path.join(root, 'transcript-harness.tsx'); },
      load(id) {
        if (id !== path.join(root, 'transcript-harness.tsx')) return;
        return `
          import { createSignal } from 'solid-js';
          import { render } from 'solid-js/web';
          import { ChatTranscript } from '@/components/ChatTranscript';
          import { ToastProvider } from '@/components/Toast';
          import { useChatScroll } from '@/lib/useChatScroll';
          import { createTranscriptItems } from '@/lib/chatStreamStore';
          import { highlightCode } from '@/lib/codeHighlight';
          import tokenStyles from '@/styles/tokens.css?inline';
          import chatStyles from '@/styles/chat.css?inline';
          import transcriptStyles from '@/styles/transcript.css?inline';
          import markdownStyles from '@/styles/markdown.css?inline';
          const style = document.createElement('style');
          style.textContent = tokenStyles + chatStyles + transcriptStyles + markdownStyles
            + '#app { height: 600px; display: flex; } body { margin: 0; }';
          document.head.appendChild(style);
          window.workerCount = 0;
          const NativeWorker = window.Worker;
          window.Worker = class extends NativeWorker {
            constructor(...args) { super(...args); window.workerCount++; }
          };
          window.highlightCode = highlightCode;
          function Harness() {
            let element;
            const scroll = useChatScroll(() => element);
            const [items, setItems] = createTranscriptItems(Array.from({ length: 2000 }, (_, index) => ({
              id: index, type: 'message', role: index % 2 ? 'assistant' : 'user',
              text: 'Message ' + index + '. ' + 'Variable height content. '.repeat(1 + index % 12),
            })));
            const [thread, setThread] = createSignal('large');
            const [streaming, setStreaming] = createSignal(false);
            Object.assign(window, { scroll, setItems, setThread, setStreaming, items });
            return <ChatTranscript
              setTranscriptRef={el => { element = el; window.transcript = el; scroll.observeTranscript(el); }}
              onScroll={scroll.handleTranscriptScroll} onUserScroll={scroll.handleUserScroll}
              items={items()} filteredItems={items()} currentThreadId={thread()}
              streaming={streaming()} backgroundLive={false} threadLoading={false}
              showScrollToBottom={scroll.showScrollToBottom()}
              turnAnchorItemId={scroll.turnAnchorItemId()}
              turnAnchorSpacerHeight={scroll.turnAnchorSpacerHeight()}
              onScrollToBottomClick={scroll.handleScrollToBottomClick}
              conversationBusy={false} agents={[]} activeAgentName="default"
              onEditMessage={() => {}} onBranchSelect={() => {}}
              onApprovePlanReview={() => {}} onRevisePlanReview={() => {}}
            />;
          }
          window.disposeTranscript = render(() => <ToastProvider><Harness /></ToastProvider>, document.getElementById('app'));
        `;
      },
    }],
    worker: { format: 'es' },
    build: { outDir: output, rollupOptions: { input: 'transcript-harness', output: { entryFileNames: 'harness.js' } } },
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
  await call('Page.navigate', { url: 'http://127.0.0.1:' + server.address().port + '/' });
  await until(() => js('!!window.transcript'), 'transcript mount');
  const count = () => js('document.querySelectorAll(".transcript-virtual-row").length');
  const bottom = () => js('Math.abs(transcript.scrollHeight - transcript.clientHeight - transcript.scrollTop) < 3');
  assert.ok(await count() < 40, '2000 messages must mount only a viewport and overscan');
  assert.equal(await js('workerCount'), 0, 'plain messages must not start the highlighter');
  await until(bottom, 'initial bottom following');
  await until(() => js('!!document.querySelector(\'[data-transcript-item-id="1999"]\')'), 'latest message at bottom');
  await js('scroll.pauseAutoScroll(); transcript.scrollTop = 0');
  await until(() => js('!!document.querySelector(\'[data-transcript-item-id="0"]\')'), 'oldest message');
  assert.ok(await count() < 40);
  await call('Emulation.setDeviceMetricsOverride', { width: 420, height: 820, deviceScaleFactor: 1, mobile: false });
  await until(() => js('transcript.clientWidth <= 420 && !!document.querySelector(\'[data-transcript-item-id="0"]\')'), 'narrow viewport remeasurement');
  assert.ok(await count() < 40);
  await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 820, deviceScaleFactor: 1, mobile: false });
  await until(() => js('transcript.clientWidth > 1000'), 'wide viewport remeasurement');
  await js('setStreaming(true); setItems(items => [...items, { id: 2000, type: "message", role: "user", text: "New turn" }]); scroll.setTurnAnchorItemId(2000); scroll.setAutoScroll(true); scroll.scrollToTurnAnchor(2000)');
  await until(() => js('(() => { const row = document.querySelector(\'[data-transcript-item-id="2000"]\'); return row && Math.abs(row.getBoundingClientRect().top - transcript.getBoundingClientRect().top) < 3; })()'), 'new turn anchor outside visible range');
  await js('setItems(items => [...items, { id: 2001, type: "message", role: "assistant", text: "Growing reply. ".repeat(700) }]); scroll.scrollToBottom()');
  await until(() => js('!!document.querySelector(\'[data-transcript-item-id="2001"]\')'), 'streamed reply');
  await js('scroll.handleScrollToBottomClick()');
  await until(bottom, 'bottom after streamed height change');
  await js('scroll.pauseAutoScroll(); transcript.scrollTop = 500');
  await delay(300);
  const readingTop = await js('transcript.scrollTop');
  await js('setItems(items => items.map(item => item.id === 2001 ? { ...item, text: item.text + "More streamed output. ".repeat(100) } : item)); scroll.scrollToBottom()');
  await delay(200);
  assert.equal(await js('transcript.scrollTop'), readingTop, 'offscreen output must not move a paused reader');
  await js('setStreaming(false); setThread("short"); setItems([{ id: 0, type: "message", role: "assistant", text: "Short thread" }]); scroll.clearTurnAnchor(); scroll.setAutoScroll(true); scroll.scrollToBottom(true)');
  await until(() => js('document.querySelectorAll(".transcript-virtual-row").length === 1 && transcript.scrollHeight <= transcript.clientHeight'), 'thread switch resets measurements');
  await js('setItems([{ id: 3000, type: "message", role: "error", text: "Replaced history", resumable: true }])');
  await until(() => js('document.querySelector(".message.error")?.textContent.includes("Replaced history")'), 'history replacement with the same row count');
  await js('setItems([])');
  await until(() => js('document.querySelectorAll(".transcript-virtual-row").length === 0'), 'empty history');
  console.log('PASS: 2000 variable-height messages mount fewer than 40 rows; scrolling, offscreen turn anchors, streaming, paused reading and thread changes work.');

  const html = await js('highlightCode("const value = 42;", "typescript", true)');
  assert.ok(html.includes('<span'), 'real worker must produce highlighted tokens');
  assert.equal(await js('workerCount'), 1);
  assert.ok(!fs.readdirSync(path.join(output, 'assets')).some(file => /wasm/i.test(file)), 'build must not include a WASM asset');
  const escaped = await js('highlightCode("<script>alert(1)</script>", "text", false)');
  assert.ok(escaped.includes('&lt;script&gt;'));
  assert.equal(await js('workerCount'), 1);
  await js('disposeTranscript()');
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: Shiki highlights through a lazy module worker without WASM; plain text is escaped safely.');

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
  assert.ok(path.basename(resolvedWorkspace).startsWith('k41-transcript-check-'));
  fs.rmSync(resolvedWorkspace, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
}
