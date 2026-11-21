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
          import { CHAT_PANEL_MIN_WIDTH, WORKSPACE_EXPLORER_MIN_WIDTH } from '@/lib/chatTypes';
          import { highlightCode } from '@/lib/codeHighlight';
          import tokenStyles from '@/styles/tokens.css?inline';
          import baseStyles from '@/styles/base.css?inline';
          import chatStyles from '@/styles/chat.css?inline';
          import transcriptStyles from '@/styles/transcript.css?inline';
          import markdownStyles from '@/styles/markdown.css?inline';
          import responsiveStyles from '@/styles/responsive.css?inline';
          const style = document.createElement('style');
          style.textContent = tokenStyles + baseStyles + chatStyles + transcriptStyles + markdownStyles + responsiveStyles
            + '.chat-shell { height: 600px; --workspace-explorer-width: 480px; transition: none;'
            + '--chat-panel-min-width: ' + CHAT_PANEL_MIN_WIDTH + 'px;'
            + '--workspace-explorer-min-width: ' + WORKSPACE_EXPLORER_MIN_WIDTH + 'px; }'
            + '#app { min-height: 0; display: flex; flex: 1; }';
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
              messageIndex: index, sourceCheckpointId: 'checkpoint',
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
      res.end('<main class="chat-shell chat-shell-resizable workspace-closed"><section class="chat-panel"><div class="chat-column" id="layout-reference"></div><div id="app"></div></section><aside class="workspace-drawer"></aside></main><script type="module" src="/harness.js"></script>');
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
  const assertRowLayout = async (label) => {
    const gaps = await js(`new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => {
      const rows = Array.from(document.querySelectorAll('.transcript-virtual-row'));
      resolve(rows.slice(1).flatMap((row, index) => {
        const previous = rows[index];
        if (Number(row.dataset.index) !== Number(previous.dataset.index) + 1) return [];
        return [row.getBoundingClientRect().top - previous.getBoundingClientRect().bottom];
      }));
    })))`);
    assert.ok(gaps.length > 0 && gaps.every(gap => Math.abs(gap) <= 1), `Rows stay contiguous ${label}: ${gaps}`);
  };
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
  await assertRowLayout('after narrowing the viewport');
  await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 820, deviceScaleFactor: 1, mobile: false });
  await until(() => js('transcript.clientWidth > 1000'), 'wide viewport remeasurement');
  await assertRowLayout('after widening the viewport');
  const setWorkspaceOpen = open => js(`(() => {
    const shell = document.querySelector('.chat-shell');
    shell.classList.toggle('workspace-open', ${open});
    shell.classList.toggle('workspace-closed', ${!open});
  })()`);
  await setWorkspaceOpen(true);
  await assertRowLayout('after opening the workspace');
  await setWorkspaceOpen(false);
  await assertRowLayout('after closing the workspace');
  await js('document.querySelector(\'[data-transcript-item-id="0"] [aria-label="Edit and regenerate"]\').click()');
  await setWorkspaceOpen(true);
  await js('document.querySelector(".chat-shell").style.setProperty("--workspace-explorer-width", "900px")');
  await until(() => js(`(() => {
    const bubble = document.querySelector('.message-bubble.editing');
    const editor = bubble.querySelector('.message-edit');
    const input = editor.querySelector('textarea');
    return bubble.getBoundingClientRect().right >= input.getBoundingClientRect().right
      && editor.scrollWidth <= editor.clientWidth;
  })()`), 'message editor fits the narrowed chat panel');
  await js('document.querySelector(\'[aria-label="Cancel edit"]\').click(); document.querySelector(".chat-shell").style.removeProperty("--workspace-explorer-width")');
  await setWorkspaceOpen(false);
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

  await js(`(() => {
    const row = document.querySelector('.transcript-virtual-row');
    const added = document.createElement('section');
    added.dataset.layoutCase = 'new';
    added.textContent = 'A component without a registered chat class';
    const wrapper = document.createElement('div');
    wrapper.innerHTML = '<div class="message assistant" data-layout-case="wrapped">An existing component inside another wrapper</div>';
    row.append(added, wrapper);
    const status = document.createElement('section');
    status.dataset.layoutCase = 'status';
    status.textContent = 'A new component outside the virtual list';
    transcript.append(status);
  })()`);
  for (const width of [360, 768, 1280, 1920]) {
    await call('Emulation.setDeviceMetricsOverride', { width, height: 820, deviceScaleFactor: 1, mobile: false });
    for (const workspaceOpen of [false, true]) {
      await setWorkspaceOpen(workspaceOpen);
      await js('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
      const dimensions = await js(`(() => {
        const row = document.querySelector('.transcript-virtual-row').getBoundingClientRect();
        const reference = document.querySelector('#layout-reference').getBoundingClientRect();
        const container = transcript.getBoundingClientRect();
        return {
          row: { left: row.left, right: row.right, width: row.width },
          referenceWidth: reference.width,
          containerWidth: transcript.clientWidth,
          leftInset: row.left - container.left,
          gutter: parseFloat(getComputedStyle(document.querySelector('.chat-shell')).getPropertyValue('--chat-column-gutter')),
          pageWidth: document.documentElement.scrollWidth,
          elements: Array.from(document.querySelectorAll('[data-layout-case]')).map(element => {
            const bounds = element.getBoundingClientRect();
            return { kind: element.dataset.layoutCase, left: bounds.left, right: bounds.right };
          }),
        };
      })()`);
      const label = `${width}px with workspace ${workspaceOpen ? 'open' : 'closed'}`;
      assert.ok(Math.abs(dimensions.row.width - Math.min(960, dimensions.containerWidth - 2 * dimensions.gutter)) < 1, `Shared column width at ${label}: ${JSON.stringify(dimensions)}`);
      assert.ok(Math.abs(dimensions.leftInset - (dimensions.containerWidth - dimensions.row.width) / 2) < 1, `Shared column centering at ${label}`);
      assert.ok(Math.abs(dimensions.referenceWidth - dimensions.row.width) <= 10, `Panel content follows the same column at ${label}`);
      assert.equal(dimensions.elements.length, 3);
      for (const element of dimensions.elements) {
        assert.ok(Math.abs(element.left - dimensions.row.left) < 1 && Math.abs(element.right - dimensions.row.right) < 1,
          `${element.kind} must follow the shared column at ${label}: ${JSON.stringify(dimensions)}`);
      }
      assert.ok(dimensions.pageWidth <= width, `No document overflow at ${label}`);
    }
  }
  await call('Emulation.setDeviceMetricsOverride', { width: 1920, height: 820, deviceScaleFactor: 1, mobile: false });
  await js(`(() => {
    const row = document.querySelector('.transcript-virtual-row');
    const wrapper = document.createElement('div');
    wrapper.innerHTML = '<section class="tool-html-preview">HTML preview</section>';
    row.append(wrapper);
    document.querySelector('.tool-html-preview').classList.add('is-fullwidth');
  })()`);
  await until(() => js('Math.abs(document.querySelector(".tool-html-preview").getBoundingClientRect().width - transcript.clientWidth) < 1'), 'full-width HTML preview inside a wrapper');
  await js('document.querySelector(".tool-html-preview").classList.remove("is-fullwidth")');
  await until(() => js('Math.abs(document.querySelector(".transcript-virtual-row").getBoundingClientRect().width - 960) < 1'), 'HTML preview restores the shared column');
  await js('document.querySelectorAll("[data-layout-case], .tool-html-preview").forEach(element => element.remove())');
  console.log('PASS: New components, extra wrappers and standalone status rows share one centered column across four widths and both workspace states; HTML previews can expand and restore.');

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
