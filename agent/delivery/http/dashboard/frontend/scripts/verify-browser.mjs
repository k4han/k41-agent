import { protocolFixture } from './protocol-fixture.mjs';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { deflateSync } from 'node:zlib';

const staticRoot = fileURLToPath(new URL('../../static/', import.meta.url));
const executable = process.env.BROWSER_EXECUTABLE || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe', '/usr/bin/google-chrome',
  '/usr/bin/chromium', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].find(candidate => fs.existsSync(candidate));
assert.ok(executable, 'Set BROWSER_EXECUTABLE to run browser UI checks.');

function chunk(type, bytes) {
  const payload = Buffer.concat([Buffer.from(type), bytes]);
  let crc = 0xffffffff;
  for (const byte of payload) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  const length = Buffer.alloc(4), checksum = Buffer.alloc(4);
  length.writeUInt32BE(bytes.length); checksum.writeUInt32BE((crc ^ 0xffffffff) >>> 0);
  return Buffer.concat([length, payload, checksum]);
}
const ihdr = Buffer.alloc(13);
ihdr.writeUInt32BE(1280); ihdr.writeUInt32BE(800, 4); ihdr[8] = 8; ihdr[9] = 2;
const pixels = Buffer.alloc((1280 * 3 + 1) * 800, 255);
for (let row = 0; row < 800; row++) pixels[row * (1280 * 3 + 1)] = 0;
let image = Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(pixels)), chunk('IEND', Buffer.alloc(0))]);
const status = { open: true, generation: 'session', profile_id: null, mode: 'human',
  tabs: [{ id: 'tab', title: 'Sign in', url: 'http://fixture.test/login' }],
  handoff: { handoff_id: 'handoff', reason: 'Sign in directly in the browser' }, handoff_expired: false, images: [] };
const history = { thread_id: 'browser-test', platform: 'api', user_id: 'dashboard', agent_name: 'default',
  workspace: { backend: 'local', locator: '/fixture', label: 'Fixture', metadata: {} },
  active_checkpoint_id: 'checkpoint', pending_browser_handoffs: [status.handoff],
  messages: [{ id: 'user', role: 'user', content: 'Sign in', message_index: 0, source_checkpoint_id: 'checkpoint',
    branch: { current: 2, total: 2, options: [
      { checkpoint_id: 'older-checkpoint', message: 'Earlier request' },
      { checkpoint_id: 'checkpoint', message: 'Sign in' },
    ] } }] };
const fixtures = {
  '/dashboard-api/catalog': { backends: [] },
  '/dashboard-api/agents/cards': { cards: [{ name: 'default', display_name: 'Default', provider: 'test', model: 'demo', valid: true }] },
  '/dashboard-api/agents/providers': { provider_names: ['test'], default_provider: 'test', default_model: 'demo', model_catalogs: [{ provider: 'test', default_model: 'demo', models: [{ id: 'demo', label: 'Demo' }] }] },
  '/dashboard-api/workspace/default': { workspace: null },
  '/dashboard-api/workspace/tree': { root: '/fixture', path: '', entries: [{ name: 'readme.txt', path: 'readme.txt', kind: 'file', size: 20, modified_at: 1 }], truncated: false },
  '/dashboard-api/workspace/changes': { root: '/fixture', is_git_repo: true, changes: [], message: '' },
  '/dashboard-api/workspace/file': { root: '/fixture', path: 'readme.txt', content: 'Workspace file preview', size: 20, modified_at: 1, binary: false, truncated: false, mime_type: 'text/plain', message: '' },
  '/dashboard-api/nav-projects': { projects: [], has_more: false },
  '/dashboard-api/chat-history/browser-test': history,
  '/dashboard-api/browser/threads/browser-test': status,
  '/dashboard-api/browser/profiles': { profiles: [] },
  '/dashboard-api/browser/threads/browser-test/viewer-ticket': { ticket: 'ticket' },
};
const inputs = [], resumes = [], viewers = new Set();
const profiles = [], checkpointLoads = [], invalidThreadRequests = [];
let profileLoads = 0, rejectProfileSelection = false;
let holdProfileSelection = false, releaseProfileSelection;
let browserStatusLoads = 0;
const sessionMutations = [];
const frameAcknowledgements = [], frameSequences = new WeakMap();
const viewerOptions = new WeakMap(), visibilityEvents = [], resizeEvents = [];
let connections = 0;
let ticketRequests = 0;
let holdBrowserStatus = false;
let releaseBrowserStatus;
function send(socket, value) {
  const binary = Buffer.isBuffer(value);
  const data = binary ? value : Buffer.from(JSON.stringify(value));
  const header = Buffer.alloc(data.length < 126 ? 2 : 4);
  header[0] = binary ? 0x82 : 0x81;
  if (data.length < 126) header[1] = data.length;
  else { header[1] = 126; header.writeUInt16BE(data.length, 2); }
  socket.write(Buffer.concat([header, data]));
}
function sendFrame(socket) {
  if (viewerOptions.get(socket)?.visible === false) return;
  const id = (frameSequences.get(socket) || 0) + 1;
  frameSequences.set(socket, id);
  const header = Buffer.alloc(12);
  header.writeUInt32BE(id); header.writeUInt32BE(1280, 4); header.writeUInt32BE(800, 8);
  send(socket, Buffer.concat([header, image]));
}
function sendStatus(socket, reset = false) {
  const options = viewerOptions.get(socket) || {};
  send(socket, { ...status, type: 'status', tab_id: options.follow_agent === false ? options.tab_id : status.tabs[0]?.id,
    follow_agent: options.follow_agent !== false, reset, paused: options.visible === false,
    visibility_revision: options.visibility_revision || 0,
    controller: status.mode === 'human' ? 'available' : 'agent', can_control: status.mode === 'human' });
}
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/v2/api/chat/events') {
    let raw = ''; for await (const data of req) raw += data;
    const request = JSON.parse(raw);
    if (!request.resume_payload) {
      assert.equal(request.message, 'Start another browser task');
      const handoff = { type: 'browser_handoff_request', handoff_id: 'live-handoff',
        thread_id: history.thread_id, tool_call_id: 'live-call', reason: 'Complete another sign in' };
      status.handoff = handoff; status.mode = 'human';
      history.pending_browser_handoffs = [handoff];
      res.writeHead(200, { 'Content-Type': 'application/x-ndjson' });
      protocolFixture(res, history.thread_id);
      res.end(JSON.stringify(handoff) + '\n'); return;
    }
    resumes.push(request);
    status.handoff = null; status.mode = 'agent'; status.handoff_expired = false;
    if (request.resume_payload.decision === 'cancel') { status.open = false; status.generation = null; }
    history.pending_browser_handoffs = [];
    for (const socket of viewers) sendStatus(socket);
    history.messages.push({ id: 'answer', role: 'assistant', content: 'Browser task resumed' });
    res.writeHead(200, { 'Content-Type': 'application/x-ndjson' });
    protocolFixture(res, history.thread_id);
    res.end(JSON.stringify({ type: 'final', content: 'Browser task resumed' }) + '\n'); return;
  }
  if (url.pathname.startsWith('/dashboard-api/') || url.pathname === '/health') {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Content-Type', 'application/json'); res.setHeader('X-CSRF-Token', 'browser-csrf');
    if (url.pathname === '/dashboard-api/browser/threads/browser-test/viewer-ticket' && ++ticketRequests === 1) {
      res.writeHead(503); res.end(JSON.stringify({ detail: 'Temporary viewer ticket failure' })); return;
    }
    if (url.pathname === '/dashboard-api/chat-history/browser-test') {
      const checkpoint = url.searchParams.get('checkpoint_id');
      if (checkpoint) checkpointLoads.push(checkpoint);
      res.end(JSON.stringify({ ...history, active_checkpoint_id: checkpoint || history.active_checkpoint_id,
        messages: history.messages.map(message => message.branch ? { ...message,
          branch: { ...message.branch, current: checkpoint === 'older-checkpoint' ? 1 : 2 } } : message) }));
      return;
    }
    if (url.pathname === '/dashboard-api/browser/profiles') {
      if (req.method === 'POST') {
        let raw = ''; for await (const data of req) raw += data;
        const profile = { profile_id: crypto.randomBytes(16).toString('hex'), name: JSON.parse(raw).name };
        profiles.push(profile); res.end(JSON.stringify(profile));
      } else { profileLoads += 1; res.end(JSON.stringify({ profiles })); }
      return;
    }
    if (url.pathname.startsWith('/dashboard-api/browser/profiles/') && req.method === 'DELETE') {
      const profileId = url.pathname.split('/').at(-1);
      profiles.splice(profiles.findIndex(profile => profile.profile_id === profileId), 1);
      if (status.profile_id === profileId) status.profile_id = null;
      res.end(JSON.stringify({ deleted: true })); return;
    }
    if (url.pathname === '/dashboard-api/browser/threads/') {
      invalidThreadRequests.push(req.method);
      res.writeHead(404); res.end(JSON.stringify({ detail: 'Thread is required.' })); return;
    }
    if (url.pathname === '/dashboard-api/browser/threads/browser-test' && req.method === 'PUT') {
      let raw = ''; for await (const data of req) raw += data;
      if (holdProfileSelection) await new Promise(resolve => { releaseProfileSelection = resolve; });
      if (rejectProfileSelection) {
        res.writeHead(409); res.end(JSON.stringify({ detail: { code: 'profile_busy', message: 'Profile selection rejected.' } })); return;
      }
      const choice = JSON.parse(raw);
      status.profile_mode = choice.mode;
      status.selected_profile_id = choice.profile_id;
      status.profile_id = choice.profile_id ?? 'd'.repeat(32);
    }
    if (url.pathname === '/dashboard-api/browser/threads/browser-test' && ['POST', 'DELETE'].includes(req.method)) {
      sessionMutations.push(req.method);
      status.open = req.method === 'POST';
      status.generation = status.open ? 'session' : null;
    }
    if (holdBrowserStatus && url.pathname === '/dashboard-api/browser/threads/browser-test') {
      holdBrowserStatus = false;
      const previous = JSON.stringify(status);
      releaseBrowserStatus = () => res.end(previous);
      return;
    }
    if (url.pathname === '/dashboard-api/browser/threads/browser-test' && req.method === 'GET') browserStatusLoads++;
    res.end(JSON.stringify(fixtures[url.pathname] || { sessions: [], threads: [], tools: [] })); return;
  }
  const relative = url.pathname.startsWith('/dashboard-assets/') ? url.pathname.slice('/dashboard-assets/'.length) : 'index.html';
  const target = path.resolve(staticRoot, relative);
  if (!target.startsWith(path.resolve(staticRoot) + path.sep)) { res.writeHead(403); res.end(); return; }
  try { res.setHeader('Content-Type', { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' }[path.extname(target)] || 'application/octet-stream'); res.end(fs.readFileSync(target)); }
  catch { res.writeHead(404); res.end(); }
});
server.on('upgrade', (req, socket) => {
  connections++;
  const accept = crypto.createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
  socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
  viewers.add(socket); socket.on('close', () => viewers.delete(socket)); socket.on('error', () => {});
  let buffered = Buffer.alloc(0);
  socket.on('data', bytes => {
    buffered = Buffer.concat([buffered, bytes]);
    while (buffered.length >= 6) {
      const opcode = buffered[0] & 15;
      let length = buffered[1] & 127, offset = 2;
      if (length === 126) { if (buffered.length < 8) return; length = buffered.readUInt16BE(2); offset = 4; }
      if (length === 127) throw new Error('Unexpected oversized test input');
      if (buffered.length < offset + 4 + length) return;
      const mask = buffered.subarray(offset, offset + 4), payload = Buffer.from(buffered.subarray(offset + 4, offset + 4 + length));
      buffered = buffered.subarray(offset + 4 + length);
      for (let i = 0; i < payload.length; i++) payload[i] ^= mask[i % 4];
      if (opcode === 8) { socket.end(Buffer.from([0x88, 0])); return; }
      if (opcode !== 1) continue;
      const message = JSON.parse(payload.toString());
      if (message.ticket) {
        assert.equal(message.ticket, 'ticket');
        assert.equal('width' in message, false, 'Authentication must leave capture width to settings');
        assert.equal('height' in message, false, 'Authentication must leave capture height to settings');
        viewerOptions.set(socket, { ...message });
        sendStatus(socket, true);
        sendFrame(socket);
      } else if (message.type === 'frame_ack') {
        assert.ok(message.frame_id > 0 && message.frame_id <= frameSequences.get(socket));
        frameAcknowledgements.push(message.frame_id);
      } else if (message.type === 'ping') {
        send(socket, { type: 'pong', id: message.id, metrics: { ack_ms: 30, quality: 50 } });
      } else if (message.type === 'visibility') {
        assert.equal('width' in message, false, 'Visibility must leave capture width to settings');
        assert.equal('height' in message, false, 'Visibility must leave capture height to settings');
        visibilityEvents.push(message.visible);
        Object.assign(viewerOptions.get(socket), { visible: message.visible, visibility_revision: message.revision });
        sendStatus(socket);
        if (message.visible) sendFrame(socket);
      } else if (message.type === 'resize') {
        resizeEvents.push(message);
        Object.assign(viewerOptions.get(socket), { width: message.width, height: message.height });
        sendFrame(socket);
      } else if (message.type === 'select_tab' || message.type === 'follow_agent') {
        inputs.push(message);
        Object.assign(viewerOptions.get(socket), { tab_id: message.tab_id, follow_agent: message.type === 'follow_agent' });
        sendStatus(socket, true);
        setTimeout(() => {
          if (!socket.destroyed) sendFrame(socket);
        }, 200);
      } else {
        inputs.push(message);
        if (message.input_id) send(socket, { type: 'input_ack', input_id: message.input_id });
      }
    }
  });
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-browser-ui-'));
const chrome = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore', windowsHide: true });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, description) { for (let i = 0; i < 150; i++) { if (await check()) return; await delay(100); } throw new Error(`Timed out: ${description}`); }
let ws, call;
try {
  const portFile = path.join(profile, 'DevToolsActivePort');
  await until(() => fs.existsSync(portFile), 'Chrome debugger');
  const port = Number(fs.readFileSync(portFile, 'utf8').split('\n')[0]);
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  ws = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
  await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }));
  let sequence = 0; const pending = new Map(), exceptions = [];
  call = (method, params = {}) => new Promise((resolve, reject) => { const id = ++sequence; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })); });
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id) { const item = pending.get(message.id); pending.delete(message.id); if (message.error) item?.reject(new Error(message.error.message)); else item?.resolve(message.result); }
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails.text);
  });
  const js = async expression => { const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails)); return result.result?.value; };
  const browserShortcut = async (activate = false) => {
    await js('document.querySelector(".chat-actions-trigger").click()');
    const available = await js('[...document.querySelectorAll(".chat-actions-menu button")].some(button => button.textContent.includes("Open browser"))');
    if (activate && available) await js('[...document.querySelectorAll(".chat-actions-menu button")].find(button => button.textContent.includes("Open browser")).click()');
    else await js('document.querySelector(".chat-actions-trigger").click()');
    return available;
  };
  await call('Runtime.enable');
  await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false });
  await call('Page.navigate', { url: `${base}/c/browser-test` });
  await until(() => js('document.querySelector(".browser-screen:not([hidden]) canvas")?.width === 1280'), 'live browser canvas');
  await until(() => frameAcknowledgements.length > 0, 'canvas acknowledges its first paint');
  assert.deepEqual(await js('[...document.querySelector(".browser-screen canvas").getContext("2d").getImageData(0, 0, 1, 1).data]'), [255, 255, 255, 255]);
  image = Buffer.from(await js(`(async () => {
    const canvas = document.querySelector('.browser-screen canvas');
    const jpeg = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg'));
    if (typeof ImageDecoder === 'function') {
      window.__imageDecodes = 0;
      window.ImageDecoder = new Proxy(ImageDecoder, { construct(target, args) {
        window.__imageDecodes++;
        return new target(...args);
      } });
    }
    return [...new Uint8Array(await jpeg.arrayBuffer())];
  })()`));
  const beforeJpegFrame = frameAcknowledgements.length;
  for (const socket of viewers) sendFrame(socket);
  await until(() => frameAcknowledgements.length > beforeJpegFrame, 'native JPEG frame is drawn');
  assert.equal(await js(`typeof ImageDecoder !== 'function' || window.__imageDecodes > 0`), true, 'JPEG frames use native WebCodecs when available');
  assert.deepEqual(await js('[...document.querySelector(".browser-screen canvas").getContext("2d").getImageData(0, 0, 1, 1).data]'), [255, 255, 255, 255]);
  assert.equal(ticketRequests, 2, 'failed viewer tickets are retried automatically');
  assert.equal(await js('document.querySelector(".browser-panel [role=alert]")?.textContent || ""'), '', 'a recovered stream clears the connection error');
  assert.equal(await browserShortcut(), true, 'active browser session shows the conversation action');
  assert.equal(await js('document.querySelector(".workspace-browser-panel").hidden'), false);
  assert.equal(await js('document.querySelector(".browser-panel").closest("[role=dialog]")'), null, 'browser belongs to the workspace');
  assert.equal(await js(`document.querySelector('[aria-label="Browser address"]').value`), status.tabs[0].url, 'address displays the active page URL');
  assert.equal(await js(`document.querySelector('.browser-navigation').getBoundingClientRect().right <= document.querySelector('.browser-address').getBoundingClientRect().left`), true, 'navigation controls sit to the left of the address bar');
  for (const [label, type] of [['Go back', 'back'], ['Go forward', 'forward'], ['Reload page', 'reload']]) {
    await js(`document.querySelector('[aria-label="${label}"]').click()`);
    await until(() => inputs.some(input => input.type === type), `${type} navigation forwarded`);
    assert.equal(inputs.find(input => input.type === type).handoff_id, 'handoff');
  }
  const backgroundTarget = await call('Target.createTarget', { url: 'about:blank' });
  await until(() => visibilityEvents.at(-1) === false, 'background dashboard pauses image delivery');
  assert.equal(await js('document.querySelector(".browser-keyboard").disabled'), true, 'a hidden dashboard cannot send input');
  const pausedAcknowledgements = frameAcknowledgements.length;
  for (const socket of viewers) sendFrame(socket);
  await delay(150);
  assert.equal(frameAcknowledgements.length, pausedAcknowledgements, 'paused viewers receive no images');
  await call('Page.bringToFront');
  await until(() => visibilityEvents.at(-1) === true, 'foreground dashboard requests fresh capture');
  await until(() => js('document.querySelector(".browser-keyboard")?.disabled === false'), 'fresh image restores control after visibility resume');
  await call('Target.closeTarget', { targetId: backgroundTarget.targetId });
  for (const socket of viewers) send(socket, { ...status, type: 'status', tab_id: 'tab', controller: 'other', can_control: false });
  await until(() => js('document.querySelector(".browser-viewport").getAttribute("aria-description").includes("Another window")'), 'another controller has a distinct accessible description');
  assert.equal(await js('document.querySelector(".browser-keyboard").disabled'), true);
  assert.equal(await js('[...document.querySelectorAll(".browser-navigation button")].every(button => button.disabled)'), true, 'navigation requires control of the browser');
  for (const socket of viewers) sendStatus(socket);
  await until(() => js('document.querySelector(".browser-keyboard")?.disabled === false'), 'released controller becomes available');
  await js('[...document.querySelectorAll(".workspace-primary-tabs [role=tab]")].find(tab => tab.title === "Files Explorer").click()');
  await until(() => js('document.querySelector(".workspace-browser-panel").hidden'), 'switch to Files');
  assert.equal(await js('document.querySelector(".chat-prompt-input").disabled'), true, 'switching tabs preserves handoff');
  await until(() => js('document.querySelector(".workspace-tree-row")?.title === "readme.txt"'), 'workspace file available');
  await js('document.querySelector(".workspace-tree-row").click()');
  await until(() => js('document.querySelector(".workspace-file-panel")?.textContent.includes("Workspace file preview")'), 'file preview loaded');
  await js('window.filePreview = document.querySelector(".workspace-file-panel")');
  await js('[...document.querySelectorAll(".workspace-primary-tabs [role=tab]")].find(tab => tab.title === "Browser").click()');
  await until(() => js('document.querySelector(".browser-keyboard")?.disabled === false'), 'viewer reconnects on tab activation');
  assert.equal(await js('filePreview.isConnected && filePreview.hidden'), true, 'Browser hides and preserves the open file preview');
  assert.equal(await js('document.querySelector(".workspace-explorer").classList.contains("is-split")'), false, 'Browser fills the workspace with files open');
  assert.equal(await js('document.querySelector(".browser-keyboard").disabled'), false);
  await js('document.querySelector(".browser-keyboard").focus()');
  await call('Input.insertText', { text: 'Fixture \u03bb \u65e5\u672c\u8a9e' });
  await until(() => inputs.some(input => input.type === 'text'), 'Unicode input forwarded');
  assert.equal(inputs.find(input => input.type === 'text').text, 'Fixture \u03bb \u65e5\u672c\u8a9e');
  await delay(2500);
  assert.equal(connections, 2, 'status polling must preserve the viewer connection');
  for (const socket of viewers) socket.destroy();
  await until(() => js('document.querySelector(".browser-screen-stale:not([hidden])") !== null'), 'disconnect keeps the last image visible');
  assert.equal(await js('document.querySelector(".browser-keyboard").disabled'), true, 'reconnecting disables stale image input');
  await until(() => connections === 3, 'viewer reconnects after transport loss');
  await until(() => js('document.querySelector(".browser-screen:not([hidden]) canvas")?.width === 1280'), 'canvas restored after transport loss');
  await js(`document.querySelector('[aria-label="Close Browser"]').click()`);
  assert.equal(await js('document.querySelector(".workspace-file-panel") === filePreview && !filePreview.hidden'), true, 'closing Browser restores the same file preview');
  assert.deepEqual(sessionMutations, [], 'switching and closing tabs must not close the browser session');
  await js(`document.querySelector('[aria-label="New tab"]').click()`);
  await js('[...document.querySelectorAll(".workspace-plus-menu [role=menuitem]")].find(item => item.textContent.includes("Browser")).click()');
  await until(() => js('document.querySelector(".browser-keyboard")?.disabled === false'), 'restore Browser from New tab menu');
  await js(`document.querySelector('[aria-label="Close workspace explorer"]').click()`);
  assert.equal(await js('document.querySelector(".chat-prompt-input").disabled'), true, 'closing the browser panel must keep the pending handoff locked');
  await call('Page.reload');
  await until(() => js('document.querySelector(".browser-screen:not([hidden]) canvas")?.width === 1280'), 'pending handoff restored after reload');
  assert.equal(await js('document.querySelector(".chat-prompt-input").disabled'), true, 'history must restore the pending handoff lock');
  assert.equal(resumes.length, 0);
  await js(`document.querySelector('[aria-label="Close Browser"]').click()`);
  await js('[...document.querySelectorAll("button")].find(button => button.getAttribute("aria-label") === "Previous branch").click()');
  await until(() => checkpointLoads.includes('older-checkpoint'), 'older checkpoint loaded');
  await until(() => js('document.querySelector(".message-branch-count")?.textContent.includes("1/2")'), 'older checkpoint displayed');
  assert.equal(await js('document.querySelector(".chat-prompt-input").disabled'), true, 'viewing an older checkpoint must preserve the live handoff lock');
  assert.equal(await browserShortcut(true), true);
  await until(() => js('[...document.querySelectorAll("button")].some(button => button.textContent.includes("Return control to agent"))'), 'handoff remains available after checkpoint selection');
  await until(() => js('document.querySelector(".browser-keyboard")?.disabled === false'), 'viewer reconnects after header shortcut');
  await js(`document.querySelector('[aria-label="Expand workspace"]').click()`);
  await until(() => js('document.querySelector(".workspace-browser-panel").getBoundingClientRect().width > 900'), 'browser uses the full workspace width');
  assert.equal(await js('document.querySelector(".workspace-explorer").classList.contains("is-split")'), false, 'browser fullscreen has no file sidebar');
  for (const [width, height, deviceScaleFactor] of [[800, 700, 1], [2400, 1600, 2], [1280, 1000, 1]]) {
    await call('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor, mobile: false });
    if (width >= 1280) {
      await until(() => js(`!!document.querySelector('[aria-label="Expand workspace"], [aria-label="Restore split view"]')`), 'desktop workspace controls');
      await js(`document.querySelector('[aria-label="Expand workspace"]')?.click()`);
      await until(() => js(`document.querySelector('.chat-shell').classList.contains('workspace-fullscreen')`), 'expand desktop viewer after a compact layout');
    }
    await until(() => js('!document.querySelector(".app-layout").getAnimations().some(animation => animation.playState === "running")'), 'sidebar layout transition settled');
    await js('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
    const fit = await js(`(() => {
      const viewport = document.querySelector('.browser-viewport').getBoundingClientRect();
      const canvas = document.querySelector('.browser-screen canvas').getBoundingClientRect();
      return { fits: canvas.left >= viewport.left && canvas.right <= viewport.right && canvas.top >= viewport.top && canvas.bottom <= viewport.bottom,
        width: canvas.width, height: canvas.height };
    })()`);
    assert.equal(fit.fits, true, 'The whole page must fit in the available viewer area');
    assert.ok(Math.abs(fit.width / fit.height - 1280 / 800) < 0.01, 'Scaling preserves the page aspect ratio');
    if (width === 2400) assert.ok(fit.width > 1280, 'Large viewers upscale locally beyond the old 1280px limit');
    await delay(400);
    assert.equal(resizeEvents.length, 0, 'Resizing the dashboard must not negotiate capture dimensions');
  }
  await js(`document.querySelector('[aria-label="Restore split view"]').click()`);
  if (process.env.BROWSER_UI_SCREENSHOT_DIR) {
    fs.mkdirSync(process.env.BROWSER_UI_SCREENSHOT_DIR, { recursive: true });
    const capture = await call('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(process.env.BROWSER_UI_SCREENSHOT_DIR, 'browser-desktop.png'), Buffer.from(capture.data, 'base64'));
  }
  await call('Emulation.setDeviceMetricsOverride', { width: 420, height: 820, deviceScaleFactor: 1, mobile: false });
  await delay(250);
  assert.ok(await js('document.querySelector(".browser-screen canvas").getBoundingClientRect().width > 100 && document.querySelector(".browser-screen canvas").getBoundingClientRect().width <= innerWidth'));
  assert.equal(await js('document.querySelector(".browser-panel").scrollWidth <= document.querySelector(".browser-panel").clientWidth'), true, 'mobile browser has no horizontal overflow');
  if (process.env.BROWSER_UI_SCREENSHOT_DIR) {
    const capture = await call('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(process.env.BROWSER_UI_SCREENSHOT_DIR, 'browser-mobile.png'), Buffer.from(capture.data, 'base64'));
  }
  await js('[...document.querySelectorAll("button")].find(button => button.textContent.includes("Return control to agent")).click()');
  await until(() => resumes.length === 1, 'handoff resume sent');
  assert.deepEqual(resumes[0].resume_payload, { action: 'browser_handoff', handoff_id: 'handoff', decision: 'return' });
  assert.equal(resumes[0].message, '');
  await until(() => js('document.querySelector(".browser-keyboard")?.disabled'), 'viewer becomes read only');
  await until(() => js('document.querySelector(".chat-prompt-input")?.disabled === false'), 'returning control unlocks the composer');
  status.tabs.push({ id: 'second-tab', title: 'Other page', url: 'http://fixture.test/other' });
  for (const socket of viewers) send(socket, { type: 'status', tabs: status.tabs, tab_id: 'tab',
    mode: 'agent', can_control: false, follow_agent: true });
  await until(() => js('document.querySelectorAll(".browser-page-tab").length === 2'), 'multiple browser tabs');
  await js('document.querySelectorAll(".browser-page-tab")[1].click()');
  await until(() => js('!document.querySelector(".browser-screen:not([hidden]) canvas")'), 'switching tabs clears the previous canvas');
  await until(() => js('document.querySelector(".browser-screen:not([hidden]) canvas")?.width === 1280'), 'pinned tab canvas');
  assert.equal(await js(`document.querySelector('[aria-label="Follow agent"]').getAttribute('aria-pressed')`), 'false');
  for (const socket of viewers) socket.destroy();
  await until(() => js('document.querySelector(".browser-screen-stale:not([hidden])") !== null'), 'pinned image remains during reconnect');
  await until(() => js('document.querySelector(".browser-screen:not(.browser-screen-stale):not([hidden])") !== null'), 'pinned tab reconnects with a fresh image');
  assert.equal(await js('document.querySelector(".browser-page-tab[aria-selected=true]").title'), status.tabs[1].url, 'reconnect preserves the pinned tab');
  await js(`document.querySelector('[aria-label="Follow agent"]').click()`);
  await until(() => js(`document.querySelector('[aria-label="Follow agent"]').getAttribute('aria-pressed') === 'true'`), 'resume following agent');
  await until(() => js('document.querySelector(".browser-screen:not([hidden]) canvas")?.width === 1280'), 'agent tab canvas');
  assert.equal(await js('document.querySelector(".browser-page-tab[aria-selected=true]").title'), status.tabs[0].url);
  holdBrowserStatus = true;
  await js('window.dispatchEvent(new CustomEvent("k41:thread-stop-running", { detail: { threadId: "browser-test" } }))');
  await until(() => typeof releaseBrowserStatus === 'function', 'capture a status poll before the next handoff');
  await js(`document.querySelector('[aria-label="Close workspace explorer"]').click()`);
  await js('document.querySelector(".chat-prompt-input").focus()');
  await call('Input.insertText', { text: 'Start another browser task' });
  await js('[...document.querySelectorAll("button")].find(button => button.getAttribute("aria-label") === "Send message").click()');
  await until(async () => status.handoff?.handoff_id === 'live-handoff'
    && await js('![...document.querySelectorAll("button")].some(button => button.getAttribute("aria-label") === "Stop generating")'), 'live handoff stream finishes');
  releaseBrowserStatus();
  await delay(100);
  assert.equal(await js('document.querySelector(".chat-prompt-input").disabled'), true, 'an outdated status poll must not clear a newly streamed handoff');
  await until(() => js('[...document.querySelectorAll("button")].some(button => button.textContent.includes("Return control to agent") && !button.disabled)'), 'live handoff pauses the stream');
  assert.equal(await js('document.querySelector(".workspace-browser-panel").hidden'), false, 'live handoff activates the Browser tab');
  assert.equal(await js('document.querySelector(".chat-shell").classList.contains("workspace-open")'), true, 'live handoff opens the workspace');
  await js(`document.querySelector('[aria-label="Close Browser"]').click()`);
  assert.equal(await js('document.querySelector(".chat-prompt-input").disabled'), true, 'a streamed handoff must lock new messages after streaming ends');
  assert.equal(await browserShortcut(true), true);
  await js('[...document.querySelectorAll("button")].find(button => button.textContent.includes("Cancel browser task")).click()');
  await until(() => resumes.length === 2, 'live handoff cancelled');
  assert.deepEqual(resumes[1].resume_payload, { action: 'browser_handoff', handoff_id: 'live-handoff', decision: 'cancel' });
  await until(() => js('document.querySelector(".chat-prompt-input")?.disabled === false'), 'cancelling unlocks the composer');
  await until(async () => !await browserShortcut(), 'closing the browser hides its conversation action');
  await js('[...document.querySelectorAll(".browser-session-header button")].find(button => button.textContent.includes("Open browser")).click()');
  await until(() => browserShortcut(), 'opening a browser restores its conversation action');
  await until(() => js('[...document.querySelectorAll(".browser-session-header button")].some(button => button.textContent.includes("Close browser") && !button.disabled)'), 'browser session opened');
  await js('[...document.querySelectorAll(".browser-session-header button")].find(button => button.textContent.includes("Close browser")).click()');
  await until(async () => !await browserShortcut(), 'explicit browser close hides its conversation action');
  await js(`document.querySelector('[aria-label="Login profiles"]').click()`);
  await until(() => js('!document.querySelector(".browser-panel select").disabled'), 'closed browser allows profile selection');
  assert.equal(await js(`!!document.querySelector('[aria-label="New login profile name"]')`), false, 'conversation does not manage shared profiles');
  assert.equal(await js(`document.querySelector('.browser-panel a[href="/settings/browser#login-profiles"]')?.textContent`), 'Manage login profiles');
  profiles.push({ profile_id: 'a'.repeat(32), name: 'Temporary profile', open_thread_id: null });
  await js(`document.querySelector('[aria-label="Login profiles"]').click()`);
  await js(`document.querySelector('[aria-label="Login profiles"]').click()`);
  await until(() => js('[...document.querySelectorAll(".browser-panel option")].some(option => option.textContent === "Temporary profile")'), 'shared profile loaded');
  holdProfileSelection = true;
  await js('const select = document.querySelector(".browser-panel select"); select.value = [...select.options].find(option => option.textContent === "Temporary profile").value; select.dispatchEvent(new Event("change", { bubbles: true }));');
  await until(() => Boolean(releaseProfileSelection), 'profile save is in flight');
  const pendingProfile = await js('document.querySelector(".browser-panel select").value');
  const loadsBeforeRefresh = browserStatusLoads;
  await js('window.dispatchEvent(new CustomEvent("k41:thread-stop-running", { detail: { threadId: "browser-test" } }))');
  await until(() => browserStatusLoads > loadsBeforeRefresh, 'old browser status refreshed during profile save');
  await delay(200);
  assert.equal(await js('document.querySelector(".browser-panel select").value'), pendingProfile, 'refreshing unchanged status preserves the pending profile choice');
  holdProfileSelection = false;
  releaseProfileSelection();
  await until(() => js('!document.querySelector(".browser-panel select").disabled'), 'profile selected');
  const selectedProfile = await js('document.querySelector(".browser-panel select").value');
  assert.ok(selectedProfile, 'the selected profile is shown');
  const loadsBeforeClosing = profileLoads;
  await js(`document.querySelector('[aria-label="Login profiles"]').click()`);
  await until(() => js('!document.querySelector(".browser-panel select")'), 'profile settings closed');
  await new Promise(resolve => setTimeout(resolve, 200));
  assert.equal(profileLoads, loadsBeforeClosing, 'closing the settings does not reload the profiles');
  await js(`document.querySelector('[aria-label="Login profiles"]').click()`);
  await until(() => profileLoads === loadsBeforeClosing + 1, 'opening the settings reloads the profiles');
  await until(() => js('!!document.querySelector(".browser-panel select")'), 'profile settings reopened');
  await new Promise(resolve => setTimeout(resolve, 200));
  assert.equal(await js('document.querySelector(".browser-panel select").value'), selectedProfile, 'reloading the profiles keeps the selection');
  rejectProfileSelection = true;
  await js('{ const select = document.querySelector(".browser-panel select"); select.value = ""; select.dispatchEvent(new Event("change", { bubbles: true })); }');
  await until(() => js('document.querySelector(".browser-panel [role=alert]")?.textContent.includes("Profile selection rejected.") && !document.querySelector(".browser-panel select").disabled'), 'rejected profile selection reported');
  assert.equal(await js('document.querySelector(".browser-panel select").value'), selectedProfile, 'a rejected selection restores the saved profile');
  rejectProfileSelection = false;
  assert.equal(await js('[...document.querySelectorAll(".browser-panel button")].some(button => button.textContent === "Delete profile")'), false, 'profile deletion belongs in settings');
  await js('{ const select = document.querySelector(".browser-panel select"); select.value = ""; select.dispatchEvent(new Event("change", { bubbles: true })); }');
  await until(() => js('!document.querySelector(".browser-panel select").disabled && document.querySelector(".browser-panel select").value === ""'), 'conversation can return to inherited profile selection');
  for (const decision of ['return', 'cancel']) {
    const handoff = { handoff_id: `timeout-${decision}`, reason: 'Complete a manual sign in' };
    Object.assign(status, { open: true, generation: `timeout-session-${decision}`, mode: 'human',
      handoff, handoff_expired: false });
    history.pending_browser_handoffs = [handoff];
    await call('Page.reload');
    await until(() => js('document.querySelector(".browser-keyboard")?.disabled === false'), 'manual control before timeout');
    Object.assign(status, { open: false, generation: null, mode: 'agent',
      handoff: { ...handoff, expired: true }, handoff_expired: true });
    history.pending_browser_handoffs = [status.handoff];
    for (const socket of viewers) send(socket, { type: 'session_closed' });
    await until(() => js('document.querySelector(".browser-handoff")?.textContent.includes("Browser handoff timed out")'), 'timeout notice replaces manual instructions');
    assert.equal(await js('document.querySelector(".browser-keyboard")?.disabled'), true, 'expired sessions reject input');
    assert.equal(await js('document.querySelector(".chat-prompt-input").disabled'), true, 'timed out workflow still needs a resume decision');
    await call('Page.reload');
    await until(() => js('document.querySelector(".browser-handoff")?.textContent.includes("Continue after timeout")'), 'timeout recovery survives conversation reload');
    assert.equal(await js('document.querySelector(".chat-prompt-input").disabled'), true);
    const previousResumes = resumes.length, previousMutations = sessionMutations.length;
    const label = decision === 'return' ? 'Continue after timeout' : 'Cancel browser task';
    await js(`[...document.querySelectorAll('.browser-handoff button')].find(button => button.textContent.includes('${label}')).click()`);
    await until(() => resumes.length === previousResumes + 1, 'timeout decision resumes the workflow');
    assert.deepEqual(resumes.at(-1).resume_payload, { action: 'browser_handoff', handoff_id: handoff.handoff_id, decision });
    await until(() => js('document.querySelector(".chat-prompt-input")?.disabled === false && !document.querySelector(".browser-handoff")'), 'timeout decision clears the handoff and unlocks chat');
    assert.equal(sessionMutations.length, previousMutations, 'settling a timeout does not reopen or close another browser');
  }
  await js(`document.querySelector('[aria-label="Login profiles"][aria-expanded="false"]')?.click()`);
  await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false });
  const loadsBeforeThreadChange = profileLoads;
  await js('[...document.querySelectorAll("a")].find(link => link.getAttribute("href") === "/chat").click()');
  await until(() => js('location.pathname === "/chat" && !document.querySelector(".browser-panel")'), 'new chat does not inherit the previous Browser tab');
  assert.equal(profileLoads, loadsBeforeThreadChange, 'new conversations do not load hidden profile settings');
  assert.equal(await browserShortcut(), false, 'new conversations have no browser shortcut');
  await js(`document.querySelector('[aria-label="New tab"]').click()`);
  await js('[...document.querySelectorAll(".workspace-plus-menu [role=menuitem]")].find(item => item.textContent.includes("Browser")).click()');
  await until(() => js('!!document.querySelector(".browser-panel")'), 'new conversations can open Browser manually');
  await js(`document.querySelector('[aria-label="Login profiles"]').click()`);
  await until(() => profileLoads > loadsBeforeThreadChange, 'explicit profile settings refresh availability');
  assert.equal(await js('document.querySelector(".browser-panel select").disabled'), true);
  assert.equal(await js(`!!document.querySelector('[aria-label="New login profile name"]')`), false);
  assert.equal(await js(`document.querySelector('.browser-panel a[href="/settings/browser#login-profiles"]')?.textContent`), 'Manage login profiles', 'new chats link to account profile management');
  assert.deepEqual(invalidThreadRequests, [], 'profile operations must not fetch an empty thread endpoint');
  assert.equal(await js('document.querySelector(".browser-panel [role=alert]")?.textContent || ""'), '');
  assert.deepEqual(exceptions, []);
  console.log('Browser dashboard: ticket retry, transport recovery, tab pinning, follow agent, workspace tabs, live view, Unicode input, checkpoint and timeout handoff recovery, fullscreen, mobile layout, handback and profile operations passed.');
} finally {
  if (ws?.readyState === WebSocket.OPEN) await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws?.close();
  if (chrome.exitCode === null) chrome.kill();
  for (const socket of viewers) socket.destroy();
  await new Promise(resolve => server.close(resolve));
  const resolved = path.resolve(profile), temporaryRoot = path.resolve(os.tmpdir()) + path.sep;
  assert.ok(resolved.startsWith(temporaryRoot) && path.basename(resolved).startsWith('k41-browser-ui-'));
  for (let retry = 0; retry < 10; retry++) { try { fs.rmSync(resolved, { recursive: true, force: true }); break; } catch { await delay(200); } }
}
