import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const staticRoot = fileURLToPath(new URL('../../static/', import.meta.url));
const executable = process.env.BROWSER_EXECUTABLE || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].find(candidate => fs.existsSync(candidate));
assert.ok(executable, 'Set BROWSER_EXECUTABLE to run browser checks.');
assert.ok(fs.existsSync(path.join(staticRoot, 'index.html')), 'Run pnpm build before checking the production chat.');

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-workspace-chat-'));
const filePath = path.join(root, 'source.txt');
fs.writeFileSync(filePath, 'initial content');
const workspace = { backend: 'local', locator: root, metadata: {} };
const requests = [];
const sentMessages = [];
const workspaceResolutions = [];
let composerScenario = false;
let failRepositories = false;
let chatResponse;
const fixtures = {
  '/dashboard-api/catalog': { backends: [
    { name: 'local', title: 'Local', enabled: true },
    { name: 'daytona', title: 'Daytona', enabled: true, capabilities: ['sandbox_inventory'] },
    { name: 'modal', title: 'Modal', enabled: false },
  ] },
  '/dashboard-api/github': { repositories: [{ repository_id: 42, full_name: 'team/composer-project', default_branch: 'main' }] },
  '/dashboard-api/sandboxes': { sandboxes: [{ sandbox_id: 'composer-sandbox', label: 'Composer sandbox', backend: 'daytona', root: '/workspace' }] },
  '/dashboard-api/workspace/browse': { path: root, parent: path.dirname(root), entries: [], roots: [], truncated: false },
  '/dashboard-api/agents/cards': { cards: [{ name: 'default', display_name: 'Default agent with a long display name', provider: 'test', model: 'demo-model', valid: true }] },
  '/dashboard-api/agents/providers': { provider_names: ['test'], default_provider: 'test', default_model: 'demo-model', model_catalogs: [{ provider: 'test', provider_type: 'openai', default_model: 'demo-model', models: [{ id: 'demo-model', label: 'Demo model', source: 'config' }] }] },
  '/dashboard-api/chat-history/workspace-chat': {
    thread_id: 'workspace-chat',
    messages: Array.from({ length: 30 }, (_, index) => ({
      id: `history-${index}`,
      role: index % 2 ? 'assistant' : 'user',
      content: `History message ${index}\n\n${'Conversation content for scroll preservation. '.repeat(12)}`,
    })),
    platform: 'api', user_id: 'dashboard', channel_id: '', agent_name: 'default', workspace: { execution: workspace },
  },
  '/dashboard-api/workspace/default': { workspace },
};
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/api/chat/events') {
    let body = '';
    for await (const chunk of req) body += chunk;
    sentMessages.push(JSON.parse(body));
    if (composerScenario) {
      res.setHeader('Content-Type', 'application/x-ndjson');
      res.end();
      return;
    }
    chatResponse = res;
    res.setHeader('Content-Type', 'application/x-ndjson');
    res.setHeader('Cache-Control', 'no-cache');
    res.write(JSON.stringify({ type: 'tool_call', id: 'write-source', name: 'shell', args: { command: 'write source.txt' } }) + '\n');
    return;
  }
  if (url.pathname.startsWith('/dashboard-api/') || url.pathname === '/health') {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('X-CSRF-Token', 'workspace-test-csrf');
    const reply = value => res.end(JSON.stringify(value));
    if (url.pathname === '/dashboard-api/github' && failRepositories) {
      res.statusCode = 503;
      return reply({ detail: 'Repository service unavailable' });
    }
    if (url.pathname === '/dashboard-api/workspace/resolve') {
      let body = '';
      for await (const chunk of req) body += chunk;
      const selection = JSON.parse(body);
      workspaceResolutions.push(selection);
      const backend = selection.backend || selection.workspace?.backend || 'local';
      return reply({ workspace: { execution: {
        backend,
        locator: selection.workspace?.locator || (backend === 'local' ? root : 'composer-sandbox'),
        label: selection.kind === 'github' ? 'team/composer-project' : 'Composer project',
        metadata: selection.kind === 'github' ? { source: 'github', repository_id: 42, repository_full_name: 'team/composer-project' } : {},
      } } });
    }
    if (fixtures[url.pathname]) return reply(fixtures[url.pathname]);
    if (url.pathname.startsWith('/dashboard-api/workspace/')) {
      requests.push({ kind: url.pathname.split('/').pop(), path: url.searchParams.get('path') });
      assert.equal(url.searchParams.get('locator'), root);
      if (url.pathname.endsWith('/tree')) return reply({ root, path: root, entries: fs.readdirSync(root).map(name => ({ name, path: path.join(root, name), kind: 'file', size: fs.statSync(path.join(root, name)).size, modified_at: 1 })), truncated: false });
      if (url.pathname.endsWith('/changes')) return reply({ root, is_git_repo: false, changes: [], message: 'Workspace is not a Git repository.' });
      if (url.pathname.endsWith('/file')) return reply({ root, path: filePath, content: fs.readFileSync(filePath, 'utf8'), mime_type: 'text/plain', size: fs.statSync(filePath).size, binary: false, truncated: false, message: '' });
    }
    return reply({ tools: [], agents: [], sessions: [], threads: [], packages: [], settings: {}, by_category: {}, backends: [], providers: [], current_version: 'test' });
  }
  const relative = url.pathname.startsWith('/dashboard-assets/') ? url.pathname.slice('/dashboard-assets/'.length) : 'index.html';
  const target = path.resolve(staticRoot, relative);
  if (!target.startsWith(path.resolve(staticRoot) + path.sep)) { res.writeHead(403); res.end(); return; }
  try {
    res.setHeader('Content-Type', { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' }[path.extname(target)] || 'application/octet-stream');
    res.end(fs.readFileSync(target));
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-workspace-chat-browser-'));
const browser = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-networking', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(callback, label, timeout = 15000) {
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
  composerScenario = true;
  await call('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await call('Page.navigate', { url: base + '/chat' });
  await until(() => js('!!document.querySelector(".composer-workspace-trigger")'), 'composer project picker');
  await js(`document.querySelector('button[aria-label="Close workspace explorer"]')?.click()`);
  await delay(300);
  assert.equal(await js('document.querySelector(".composer-workspace-label").textContent'), 'Select project');
  assert.equal(await js('!!document.querySelector(".chat-welcome-hero .workspace-selector")'), false, 'welcome must not duplicate project selection');
  const click = async selector => {
    await js('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
    const point = await js(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      el.scrollIntoView({ block: 'nearest' });
      const r = el.getBoundingClientRect();
      const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      if (!el.contains(hit)) throw new Error('Covered target: ' + ${JSON.stringify(selector)} + ', hit=' + hit?.className);
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    })()`);
    await call('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', clickCount: 1, ...point });
    await call('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...point });
  };
  const escape = () => call('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
  const openPicker = async () => {
    await click('.composer-workspace-trigger');
    await until(() => js('!!document.querySelector(".composer-workspace-popover .workspace-selector")'), 'project popover');
  };
  const screenshot = async name => {
    const directory = process.env.COMPOSER_SCREENSHOT_DIR;
    if (!directory) return;
    fs.mkdirSync(directory, { recursive: true });
    const { data } = await call('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(directory, `${name}.png`), Buffer.from(data, 'base64'));
  };
  const source = title => js(`Array.from(document.querySelectorAll('.workspace-tab-btn')).find(el => el.textContent.trim() === ${JSON.stringify(title)}).click()`);
  const send = async text => {
    const count = sentMessages.length;
    await js(`(() => {
      const input = document.querySelector('.chat-prompt-input');
      input.value = ${JSON.stringify(text)};
      input.dispatchEvent(new Event('input', { bubbles: true }));
      document.querySelector('button[aria-label="Send message"]').click();
    })()`);
    await until(() => sentMessages.length === count + 1, 'composer message sent');
    await until(() => js(`!!document.querySelector('button[aria-label="Send message"]')`), 'composer stream completed');
  };
  for (const dark of [false, true]) {
    await js(`document.documentElement.classList.toggle('dark', ${dark})`);
    for (const width of [320, 375, 399, 400, 480, 559, 560, 960]) {
      await js(`document.querySelector('.chat-composer').style.width = '${width}px'`);
      await delay(50);
      const bounds = await js(`(() => {
        const composer = document.querySelector('.chat-composer');
        const bar = composer.querySelector('.chat-composer-toolbar');
        const outer = bar.getBoundingClientRect();
        const controls = ['.chat-composer-more-wrapper > button', '.agent-picker-trigger', '.model-picker-trigger', '.composer-workspace-trigger', '.chat-composer-send'];
        const rects = controls.map(selector => composer.querySelector(selector)).filter(el => getComputedStyle(el).display !== 'none').map(el => el.getBoundingClientRect());
        return {
          fits: rects.every(r => r.left >= outer.left && r.right <= outer.right + 1 && r.width >= 40 && r.height >= 40),
          row: rects.every(r => Math.abs(r.top - rects[0].top) < 1),
          overflow: bar.scrollWidth > bar.clientWidth,
          separateEffort: !!composer.querySelector('.chat-effort-button'),
          project: getComputedStyle(composer.querySelector('.composer-workspace-label')).display !== 'none',
          contentWidth: composer.clientWidth,
          grouped: composer.querySelector('.agent-picker-trigger').parentElement === composer.querySelector('.model-picker-trigger').parentElement,
          packed: rects.slice(1, -1).every((r, index) => r.left >= rects[index].right && r.left - rects[index].right <= 12),
          sendRight: outer.right - rects.at(-1).right <= 12,
          contextBesideSend: (() => {
            const context = composer.querySelector('.context-window-circle-btn')?.getBoundingClientRect();
            const send = rects.at(-1);
            return !context || (context.right <= send.left && send.left - context.right <= 6);
          })(),
        };
      })()`);
      assert.equal(bounds.fits && bounds.row && !bounds.overflow, true, `single-row composer at ${width}px, dark=${dark}: ${JSON.stringify(bounds)}`);
      assert.equal(bounds.grouped && bounds.packed && bounds.sendRight && bounds.contextBesideSend, true, `grouped left controls and right send at ${width}px: ${JSON.stringify(bounds)}`);
      assert.equal(bounds.separateEffort, false);
      assert.equal(bounds.project, width - 2 >= 560);
      await click('.agent-picker-trigger');
      await until(() => js('!!document.querySelector(".agent-card-item")'), 'independent agent menu');
      assert.equal(await js('!!document.querySelector(".model-item-row")'), false);
      await click('.model-picker-trigger');
      await until(() => js('!!document.querySelector(".model-item-row")'), 'independent model menu');
      assert.equal(await js('!!document.querySelector(".agent-card-item")'), false);
      await escape();
      assert.equal(await js('document.activeElement === document.querySelector(".model-picker-trigger")'), true);
      await openPicker();
      assert.equal(await js(`(() => {
        const r = document.querySelector('.composer-workspace-popover').getBoundingClientRect();
        return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight;
      })()`), true, 'project popover stays within the viewport');
      await escape();
      await until(() => js('!document.querySelector(".composer-workspace-popover")'), 'Escape closes project popover');
      assert.equal(await js('document.activeElement === document.querySelector(".composer-workspace-trigger")'), true, 'Escape restores project trigger focus');
    }
  }
  await js('document.querySelector(".chat-composer").style.removeProperty("width")');
  await js(`(() => {
    const transfer = new DataTransfer();
    for (let i = 0; i < 10; i++) transfer.items.add(new File(['Test attachment'], 'attachment-with-a-long-name-' + i + '.txt', { type: 'text/plain' }));
    const input = document.querySelector('.chat-composer input[type=file]');
    input.files = transfer.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
  })()`);
  await until(() => js('document.querySelectorAll(".chat-attachment-chip").length === 10'), 'multiple pending attachments');
  assert.equal(await js(`(() => {
    const list = document.querySelector('.chat-attachment-list');
    return list.getBoundingClientRect().height <= 96 && list.scrollHeight > list.clientHeight;
  })()`), true, 'pending attachment list must stay bounded and scrollable');
  await js('Array.from(document.querySelectorAll(".chat-attachment-remove")).forEach(button => button.click())');
  await send('Use an automatic temporary workspace');
  assert.equal(workspaceResolutions.length, 0, 'unselected project must preserve automatic temporary provisioning');
  assert.equal(sentMessages.at(-1).workspace ?? null, null);
  await openPicker();
  await source('Temporary');
  assert.equal(await js('document.querySelector(".composer-workspace-label").textContent'), 'Temporary');
  await source('Local Folder');
  await js(`(() => {
    const input = document.querySelector('.workspace-path-input');
    input.value = '/unapplied-project';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await click('.chat-header-title');
  assert.equal(await js('!!document.querySelector(".composer-workspace-popover")'), false);
  assert.equal(await js('document.querySelector(".composer-workspace-label").textContent'), 'Temporary', 'closing must not commit a typed path');
  await openPicker();
  await source('Local Folder');
  await click('.workspace-browse-btn');
  await until(() => js('!!document.querySelector(".dialog-backdrop") && !!document.querySelector(".workspace-browser-path-val")'), 'nested folder browser');
  await escape();
  assert.equal(await js('!!document.querySelector(".dialog-backdrop")'), false);
  assert.equal(await js('!!document.querySelector(".composer-workspace-popover")'), true, 'nested Escape must preserve project popover');
  await click('.workspace-browse-btn');
  await until(() => js('document.querySelector(".workspace-browser-path-val")?.textContent !== "(none)"'), 'folder browse result');
  await click('.workspace-browser-actions .btn-primary');
  assert.equal(await js('!!document.querySelector(".composer-workspace-popover")'), true, 'choosing a folder must preserve the project popover');
  await escape();
  await send('Use the selected local project');
  assert.equal(workspaceResolutions.at(-1).kind, 'local');
  assert.equal(sentMessages.at(-1).workspace.locator, root);
  await openPicker();
  await source('GitHub');
  await until(() => js('!!document.querySelector(".workspace-item-card")'), 'GitHub repository list');
  await click('.workspace-item-card');
  assert.equal(await js('document.querySelector(".composer-workspace-label").textContent'), 'team/composer-project');
  await screenshot('project-popover');
  await escape();
  await send('Use the selected repository');
  assert.equal(workspaceResolutions.at(-1).repository_id, 42);
  assert.equal(workspaceResolutions.at(-1).backend, 'local');
  await openPicker();
  await click('.workspace-backend-control .select-control-trigger');
  assert.equal(await js('document.querySelectorAll(".workspace-selector [role=option]").length'), 2, 'disabled backend must not be selectable');
  await escape();
  assert.equal(await js('!!document.querySelector(".composer-workspace-popover") && !document.querySelector(".workspace-selector [role=listbox]")'), true, 'backend Escape must preserve project popover');
  await escape();
  assert.equal(await js('!!document.querySelector(".composer-workspace-popover")'), false, 'a second Escape closes the project popover');
  await openPicker();
  await click('.workspace-backend-control .select-control-trigger');
  await click('.workspace-selector [role=option]:last-child');
  assert.equal(await js('!!document.querySelector(".composer-workspace-popover")'), true, 'backend selection must not dismiss the project popover');
  await source('Cloud Sandbox');
  await until(() => js('!!document.querySelector(".workspace-panel-sandbox .workspace-item-card")'), 'sandbox inventory');
  await click('.workspace-panel-sandbox .workspace-item-card');
  await escape();
  await send('Use the selected sandbox');
  assert.equal(workspaceResolutions.at(-1).kind, 'daytona');
  assert.equal(sentMessages.at(-1).workspace.backend, 'daytona');
  failRepositories = true;
  await openPicker();
  await source('GitHub');
  await until(() => js('!!document.querySelector(".workspace-panel-github .error")'), 'repository error feedback');
  await escape();
  failRepositories = false;
  console.log('PASS: composer container widths, themes, project popover, nested folder browser, backend selection, temporary/local/GitHub/sandbox sends, and repository errors.');
  composerScenario = false;
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await call('Page.navigate', { url: base + '/c/workspace-chat' });
  await until(() => js('!!document.querySelector(".chat-prompt-input") && !document.querySelector(".chat-prompt-input").disabled'), 'existing conversation');
  await js(`document.querySelector('button[aria-label="Open workspace"]')?.click()`);
  await until(() => js('!!document.querySelector(".workspace-tree-row") && !!document.querySelector(".chat-prompt-input") && !document.querySelector(".chat-prompt-input").disabled'), 'production chat and local workspace');
  await openPicker();
  assert.equal(await js('!!document.querySelector(".workspace-selector.locked") && !document.querySelector(".workspace-tab-btn")'), true, 'existing conversation project must remain locked');
  await escape();
  await js(`window.chatTranscript = document.querySelector('.transcript');
    chatTranscript.scrollTop = Math.floor((chatTranscript.scrollHeight - chatTranscript.clientHeight) / 2);
    chatTranscript.dispatchEvent(new Event('scroll'));
    window.chatScrollTop = chatTranscript.scrollTop;
    window.chatWasDetached = false;
    window.chatObserver = new MutationObserver(records => {
      if (records.some(record => Array.from(record.removedNodes).some(node => node === chatTranscript || node.contains?.(chatTranscript)))) {
        chatWasDetached = true;
      }
    });
    chatObserver.observe(document.body, { childList: true, subtree: true });`);
  assert.ok(await js('chatScrollTop') > 0, 'history must be scrollable before opening a file');
  await js('document.querySelector(".workspace-tree-row").click();');
  await until(() => js('document.querySelector(".workspace-file-panel")?.textContent.includes("initial content")'), 'initial file preview');
  await until(() => js('!!document.querySelector(".workspace-file-view-shiki")'), 'initial code rendering');
  assert.equal(await js('chatWasDetached'), false, 'opening a file must not detach the chat while highlighting loads');
  assert.equal(await js('document.querySelector(".transcript") === chatTranscript && chatTranscript.scrollTop === chatScrollTop'), true, 'opening a file must preserve the chat scroll position');
  await js('document.documentElement.classList.toggle("dark");');
  await delay(100);
  assert.equal(await js('!chatWasDetached && chatTranscript.scrollTop === chatScrollTop'), true, 'changing preview theme must preserve the chat scroll position');
  await js('document.querySelector(".workspace-tab.active .workspace-tab-close").click();');
  await until(() => js('!!document.querySelector(".workspace-tree-row")'), 'file tab closed');
  await js('document.querySelector(".workspace-tree-row").click();');
  await until(() => js('!!document.querySelector(".workspace-file-view-shiki")'), 'file preview reopened');
  assert.equal(await js('!chatWasDetached && chatTranscript.scrollTop === chatScrollTop'), true, 'closing and reopening a file must preserve the chat scroll position');
  await js('chatObserver.disconnect();');
  console.log('PASS: opening, closing, reopening, and changing the theme of a workspace file preserve chat scroll without detaching the chat.');
  const desktopSelectors = ['.chat-header-title', '.chat-prompt-input', '.workspace-tab', '.workspace-file-view-shiki .shiki code'];
  const fontSizes = selectors => js(`(${JSON.stringify(selectors)}).map(selector => getComputedStyle(document.querySelector(selector)).fontSize)`);
  assert.deepEqual(await fontSizes(desktopSelectors), desktopSelectors.map(() => '14px'), 'chat and workspace use 14px text on desktop');
  await js('document.documentElement.style.fontSize = "20px"');
  assert.deepEqual(await fontSizes(desktopSelectors), desktopSelectors.map(() => '17.5px'), 'chat and workspace scale with the root font size');
  await js('document.documentElement.style.removeProperty("font-size")');
  await js(`document.querySelector('button[aria-label="New tab"]').click();
    Array.from(document.querySelectorAll('.workspace-plus-menu-item')).find(item => item.textContent.includes('Git Changes')).click();
    Array.from(document.querySelectorAll('.workspace-tab')).find(tab => tab.title.endsWith('source.txt')).click();`);
  await until(() => js('!!document.querySelector(".workspace-file-view-shiki")'), 'cached code rendering');
  await js(`(() => {
    const input = document.querySelector('.chat-prompt-input');
    input.value = 'Update source.txt';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector('button[aria-label="Send message"]').click();
  })()`);
  await until(() => Boolean(chatResponse), 'chat stream starts');
  assert.equal(await js(`(() => {
    const bar = document.querySelector('.chat-composer-toolbar').getBoundingClientRect();
    const stop = document.querySelector('.chat-composer-stop').getBoundingClientRect();
    const context = document.querySelector('.context-window-circle-btn').getBoundingClientRect();
    return bar.right - stop.right <= 12 && context.right <= stop.left && stop.left - context.right <= 6;
  })()`), true, 'stop stays at the right edge beside context while streaming');

  const scrollMetrics = () => js(`(() => {
    const transcript = document.querySelector('.transcript');
    return { top: transcript.scrollTop, height: transcript.scrollHeight, viewport: transcript.clientHeight };
  })()`);
  const toolOutput = 'Command exited with code 0.\n' + 'Expanded tool output.\n'.repeat(40);
  chatResponse.write(JSON.stringify({ type: 'tool_result', tool_call_id: 'write-source', name: 'shell', content: toolOutput }) + '\n');
  await until(() => js('document.querySelector(".tool-call-body")?.textContent.includes("Expanded tool output.")'), 'tool output arrives');
  await delay(100);
  const collapsed = await scrollMetrics();
  await click('.tool-call > summary');
  await delay(100);
  const expanded = await scrollMetrics();
  assert.ok(expanded.height > collapsed.height, 'expanding a tool must increase the main scroll range');
  assert.ok(Math.abs(expanded.top - collapsed.top) <= 1, 'expanding a tool must preserve the reading position');

  for (let index = 0; index < 2; index++) {
    const id = `scroll-tool-${index}`;
    chatResponse.write(JSON.stringify({ type: 'tool_call', id, name: 'shell', args: { command: `inspect output ${index}` } }) + '\n');
    chatResponse.write(JSON.stringify({ type: 'tool_result', tool_call_id: id, name: 'shell', content: toolOutput }) + '\n');
  }
  await until(() => js('document.querySelectorAll(".tool-call").length === 3'), 'more tools arrive while paused');
  await js('document.querySelectorAll(".tool-call:not([open]) > summary").forEach(summary => summary.click())');
  await until(() => js('!!document.querySelector(".scroll-to-bottom-btn")'), 'expanded content exposes the bottom button');
  assert.ok(Math.abs((await scrollMetrics()).top - expanded.top) <= 1, 'more streamed tools must not move the reading position');
  const transcriptBounds = await js(`(() => {
    const transcript = document.querySelector('.transcript');
    return { ...transcript.getBoundingClientRect().toJSON(), scrollbarWidth: transcript.offsetWidth - transcript.clientWidth };
  })()`);
  await call('Input.dispatchMouseEvent', {
    type: 'mouseWheel', x: transcriptBounds.left + 4, y: transcriptBounds.top + 40,
    deltaX: 0, deltaY: 100000,
  });
  const atBottom = async () => {
    const metrics = await scrollMetrics();
    return Math.abs(metrics.height - metrics.viewport - metrics.top) <= 1;
  };
  await until(atBottom, 'mouse wheel reaches the expanded conversation bottom');
  assert.equal(await js('!!document.querySelector(".transcript-anchor-spacer")'), false, 'reaching the content bottom releases blank anchor space');

  await js('document.querySelector(".transcript").scrollTop = 0');
  await until(() => js('!!document.querySelector(".scroll-to-bottom-btn")'), 'scroll back through history');
  const metrics = await scrollMetrics();
  const thumbCenter = transcriptBounds.top + metrics.viewport * metrics.viewport / metrics.height / 2;
  const scrollbarX = transcriptBounds.right - transcriptBounds.scrollbarWidth / 2;
  await call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: scrollbarX, y: thumbCenter });
  await call('Input.dispatchMouseEvent', { type: 'mousePressed', x: scrollbarX, y: thumbCenter, button: 'left', buttons: 1, clickCount: 1 });
  await call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: scrollbarX, y: transcriptBounds.bottom - 2, button: 'left', buttons: 1 });
  await delay(100);
  await call('Input.dispatchMouseEvent', { type: 'mouseReleased', x: scrollbarX, y: transcriptBounds.bottom - 2, button: 'left', clickCount: 1 });
  await until(atBottom, 'dragging the main scrollbar reaches the expanded conversation bottom');
  console.log('PASS: expanding streamed tools updates the main scroll range, preserves position, and allows wheel and scrollbar navigation to the bottom.');

  await js(`window.previewNode = document.querySelector('.workspace-file-view-shiki').firstChild;
    window.backgroundMutations = [];
    window.backgroundObserver = new MutationObserver(records => backgroundMutations.push(...records));
    backgroundObserver.observe(document.querySelector('.workspace-explorer'), {
      childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'disabled', 'title'],
    });`);
  const quietReads = requests.filter(request => request.kind === 'tree').length;
  await until(() => requests.filter(request => request.kind === 'tree').length >= quietReads + 2, 'two unchanged running polls', 25000);
  await delay(150);
  assert.equal(await js('backgroundMutations.length'), 0, 'unchanged running polls must not flash loading states or rebuild content');
  assert.equal(await js('document.querySelector(".workspace-file-view-shiki").firstChild === previewNode'), true, 'unchanged polling must preserve code DOM');
  await js('backgroundObserver.disconnect();');
  const readsBeforeWrite = requests.filter(request => request.kind === 'tree').length;
  fs.writeFileSync(filePath, 'written while tool is running');
  fs.writeFileSync(path.join(root, 'created.txt'), 'new file');
  await until(() => js('document.querySelector(".workspace-file-panel")?.textContent.includes("written while tool is running")'), 'live preview before tool_result', 18000);
  assert.equal(chatResponse.writableEnded, false, 'chat must still be streaming');
  assert.ok(requests.filter(request => request.kind === 'tree').length > readsBeforeWrite, 'running chat refreshes the tree');

  fs.writeFileSync(filePath, 'written at tool completion');
  const event = JSON.stringify({ type: 'tool_result', tool_call_id: 'write-source', name: 'shell', content: 'Command exited with code 0.' }) + '\n';
  chatResponse.write(event.slice(0, 20));
  chatResponse.write(event.slice(20));
  await until(() => js('document.querySelector(".workspace-file-panel")?.textContent.includes("written at tool completion")'), 'tool_result updates preview during the stream');
  await js('Array.from(document.querySelectorAll(".workspace-tab")).find(tab => tab.title === "Files Explorer").click();');
  assert.equal(await js('Array.from(document.querySelectorAll(".workspace-tree-row")).some(row => row.textContent.includes("created.txt"))'), true, 'newly written files appear during chat');
  chatResponse.write(JSON.stringify({ type: 'final', content: 'Done.\n\n```text\nUpdated files\n```\n\n| File | Status |\n| --- | --- |\n| source.txt | Updated |' }) + '\n');
  await until(() => js('!!document.querySelector(".message-markdown .markdown-code-frame pre code") && !!document.querySelector(".message-markdown td")'), 'formatted assistant response');
  const messageSelectors = ['.message.user .message-text', '.message-markdown', '.message-markdown .markdown-code-frame pre code', '.message-markdown td', '.workspace-tree-row'];
  assert.deepEqual(await fontSizes(messageSelectors), messageSelectors.map(() => '14px'), 'messages, code, tables, and file rows use 14px text');
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  assert.deepEqual(await fontSizes(messageSelectors), messageSelectors.map(() => '14px'), 'mobile retains readable message and workspace text');
  assert.deepEqual(await fontSizes(['.chat-prompt-input']), ['16px'], 'mobile input prevents automatic zoom');
  chatResponse.end();
  await until(() => js(`!!document.querySelector('button[aria-label="Send message"]')`), 'chat completes');
  await js(`document.querySelector('button[aria-label="Close workspace explorer"]')?.click()`);
  await delay(300);
  for (const width of [320, 375, 480, 560, 960]) {
    await call('Emulation.setDeviceMetricsOverride', { width, height: 844, deviceScaleFactor: 1, mobile: width < 768 });
    await delay(100);
    assert.equal(await js(`(() => {
      const composer = document.querySelector('.chat-composer');
      const toolbar = composer.querySelector('.chat-composer-toolbar');
      const bounds = composer.getBoundingClientRect();
      const context = composer.querySelector('.context-window-circle-btn').getBoundingClientRect();
      const send = composer.querySelector('.chat-composer-send').getBoundingClientRect();
      const agent = composer.querySelector('.agent-picker-trigger').getBoundingClientRect();
      const model = composer.querySelector('.model-picker-trigger').getBoundingClientRect();
      return toolbar.scrollWidth <= toolbar.clientWidth && context.width >= 40 && send.width >= 40
        && agent.width >= 40 && model.width >= 40 && model.left >= agent.right
        && context.right <= send.left && send.left - context.right <= 6 && bounds.right - send.right <= 12
        && send.right <= bounds.right && Math.abs(context.top - send.top) < 1;
    })()`), true, `context and send remain visible at ${width}px`);
    await openPicker();
    if (width === 375 || width === 960) await screenshot(`locked-project-${width}`);
    await escape();
    if (width === 375 || width === 960) await screenshot(`composer-${width}`);
  }
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: production Chat refreshes real local files and previews while tools run and on streamed tool results.');
} finally {
  chatResponse?.end();
  if (call && ws?.readyState === WebSocket.OPEN) await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws?.close();
  browser.kill();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  for (const directory of [profile, root]) {
    const resolved = path.resolve(directory);
    assert.equal(path.dirname(resolved), path.resolve(os.tmpdir()));
    assert.ok(path.basename(resolved).startsWith('k41-workspace-chat-'));
    fs.rmSync(resolved, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
  }
}
