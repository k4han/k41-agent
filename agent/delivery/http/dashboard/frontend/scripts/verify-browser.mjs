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
const image = Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(pixels)), chunk('IEND', Buffer.alloc(0))]).toString('base64');
const status = { open: true, generation: 'session', profile_id: null, mode: 'human',
  tabs: [{ id: 'tab', title: 'Sign in', url: 'http://fixture.test/login' }],
  handoff: { handoff_id: 'handoff', reason: 'Sign in directly in the browser' }, handoff_expired: false, images: [] };
const history = { thread_id: 'browser-test', platform: 'api', user_id: 'dashboard', agent_name: 'default',
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
  '/dashboard-api/nav-projects': { projects: [], has_more: false },
  '/dashboard-api/chat-history/browser-test': history,
  '/dashboard-api/browser/threads/browser-test': status,
  '/dashboard-api/browser/profiles': { profiles: [] },
  '/dashboard-api/browser/threads/browser-test/viewer-ticket': { ticket: 'ticket' },
};
const inputs = [], resumes = [], viewers = new Set();
const profiles = [], checkpointLoads = [], invalidThreadRequests = [];
let connections = 0;
let holdBrowserStatus = false;
let releaseBrowserStatus;
function send(socket, value) {
  const data = Buffer.from(JSON.stringify(value));
  const header = Buffer.alloc(data.length < 126 ? 2 : 4);
  header[0] = 0x81;
  if (data.length < 126) header[1] = data.length;
  else { header[1] = 126; header.writeUInt16BE(data.length, 2); }
  socket.write(Buffer.concat([header, data]));
}
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/api/chat/events' || url.pathname === '/v1/api/chat/events') {
    let raw = ''; for await (const data of req) raw += data;
    const request = JSON.parse(raw);
    if (!request.resume_payload) {
      assert.equal(request.message, 'Start another browser task');
      const handoff = { type: 'browser_handoff_request', handoff_id: 'live-handoff',
        thread_id: history.thread_id, tool_call_id: 'live-call', reason: 'Complete another sign in' };
      status.handoff = handoff; status.mode = 'human';
      history.pending_browser_handoffs = [handoff];
      res.writeHead(200, { 'Content-Type': 'application/x-ndjson' });
      res.end(JSON.stringify(handoff) + '\n'); return;
    }
    resumes.push(request);
    status.handoff = null; status.mode = 'agent';
    if (request.resume_payload.decision === 'cancel') { status.open = false; status.generation = null; }
    history.pending_browser_handoffs = [];
    for (const socket of viewers) send(socket, { type: 'status', tabs: status.tabs, mode: 'agent', can_control: false });
    history.messages.push({ id: 'answer', role: 'assistant', content: 'Browser task resumed' });
    res.writeHead(200, { 'Content-Type': 'application/x-ndjson' });
    res.end(JSON.stringify({ type: 'final', content: 'Browser task resumed' }) + '\n'); return;
  }
  if (url.pathname.startsWith('/dashboard-api/') || url.pathname === '/health') {
    res.setHeader('Content-Type', 'application/json'); res.setHeader('X-CSRF-Token', 'browser-csrf');
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
      } else res.end(JSON.stringify({ profiles }));
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
      status.profile_id = JSON.parse(raw).profile_id;
    }
    if (holdBrowserStatus && url.pathname === '/dashboard-api/browser/threads/browser-test') {
      holdBrowserStatus = false;
      const previous = JSON.stringify(status);
      releaseBrowserStatus = () => res.end(previous);
      return;
    }
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
        send(socket, { type: 'status', tabs: status.tabs, mode: status.mode, can_control: status.mode === 'human' });
        send(socket, { type: 'frame', data: image, metadata: { deviceWidth: 1280, deviceHeight: 800 } });
      } else inputs.push(message);
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
  await call('Runtime.enable');
  await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false });
  await call('Page.navigate', { url: `${base}/c/browser-test` });
  await until(() => js('document.querySelector(".browser-screen img")?.naturalWidth === 1280'), 'live browser image');
  assert.equal(await js('document.querySelector(".browser-keyboard").disabled'), false);
  await js('document.querySelector(".browser-keyboard").focus()');
  await call('Input.insertText', { text: 'Fixture \u03bb \u65e5\u672c\u8a9e' });
  await until(() => inputs.some(input => input.type === 'text'), 'Unicode input forwarded');
  assert.equal(inputs.find(input => input.type === 'text').text, 'Fixture \u03bb \u65e5\u672c\u8a9e');
  await delay(2500);
  assert.equal(connections, 1, 'status polling must preserve the viewer connection');
  await js('document.querySelector(".dialog-close-btn").click()');
  assert.equal(await js('document.querySelector(".chat-prompt-input").disabled'), true, 'closing the browser panel must keep the pending handoff locked');
  await call('Page.reload');
  await until(() => js('document.querySelector(".browser-screen img")?.naturalWidth === 1280'), 'pending handoff restored after reload');
  assert.equal(await js('document.querySelector(".chat-prompt-input").disabled'), true, 'history must restore the pending handoff lock');
  assert.equal(resumes.length, 0);
  await js('document.querySelector(".dialog-close-btn").click()');
  await js('[...document.querySelectorAll("button")].find(button => button.getAttribute("aria-label") === "Previous branch").click()');
  await until(() => checkpointLoads.includes('older-checkpoint'), 'older checkpoint loaded');
  await until(() => js('document.querySelector(".message-branch-count")?.textContent.includes("1/2")'), 'older checkpoint displayed');
  assert.equal(await js('document.querySelector(".chat-prompt-input").disabled'), true, 'viewing an older checkpoint must preserve the live handoff lock');
  await js('[...document.querySelectorAll("button")].find(button => button.getAttribute("aria-label") === "Open browser").click()');
  await until(() => js('[...document.querySelectorAll("button")].some(button => button.textContent.includes("Return control to agent"))'), 'handoff remains available after checkpoint selection');
  await call('Emulation.setDeviceMetricsOverride', { width: 420, height: 820, deviceScaleFactor: 1, mobile: false });
  assert.ok(await js('document.querySelector(".browser-screen img").getBoundingClientRect().width <= innerWidth'));
  await js('[...document.querySelectorAll("button")].find(button => button.textContent.includes("Return control to agent")).click()');
  await until(() => resumes.length === 1, 'handoff resume sent');
  assert.deepEqual(resumes[0].resume_payload, { action: 'browser_handoff', handoff_id: 'handoff', decision: 'return' });
  assert.equal(resumes[0].message, '');
  await until(() => js('document.querySelector(".browser-keyboard")?.disabled'), 'viewer becomes read only');
  await until(() => js('document.querySelector(".chat-prompt-input")?.disabled === false'), 'returning control unlocks the composer');
  holdBrowserStatus = true;
  await until(() => typeof releaseBrowserStatus === 'function', 'capture a status poll before the next handoff');
  await js('document.querySelector(".dialog-close-btn").click()');
  await js('document.querySelector(".chat-prompt-input").focus()');
  await call('Input.insertText', { text: 'Start another browser task' });
  await js('[...document.querySelectorAll("button")].find(button => button.getAttribute("aria-label") === "Send message").click()');
  await until(async () => status.handoff?.handoff_id === 'live-handoff'
    && await js('![...document.querySelectorAll("button")].some(button => button.getAttribute("aria-label") === "Stop generating")'), 'live handoff stream finishes');
  releaseBrowserStatus();
  await delay(100);
  assert.equal(await js('document.querySelector(".chat-prompt-input").disabled'), true, 'an outdated status poll must not clear a newly streamed handoff');
  await until(() => js('[...document.querySelectorAll("button")].some(button => button.textContent.includes("Return control to agent") && !button.disabled)'), 'live handoff pauses the stream');
  await js('document.querySelector(".dialog-close-btn").click()');
  assert.equal(await js('document.querySelector(".chat-prompt-input").disabled'), true, 'a streamed handoff must lock new messages after streaming ends');
  await js('[...document.querySelectorAll("button")].find(button => button.getAttribute("aria-label") === "Open browser").click()');
  await js('[...document.querySelectorAll("button")].find(button => button.textContent.includes("Cancel browser task")).click()');
  await until(() => resumes.length === 2, 'live handoff cancelled');
  assert.deepEqual(resumes[1].resume_payload, { action: 'browser_handoff', handoff_id: 'live-handoff', decision: 'cancel' });
  await until(() => js('document.querySelector(".chat-prompt-input")?.disabled === false'), 'cancelling unlocks the composer');
  await until(() => js('!document.querySelector(".browser-panel select").disabled'), 'closed browser allows profile selection');
  await js('document.querySelector(".browser-panel input").focus()');
  await call('Input.insertText', { text: 'Temporary profile' });
  await js('[...document.querySelectorAll("button")].find(button => button.textContent === "Create profile").click()');
  await until(() => js('[...document.querySelectorAll(".browser-panel option")].some(option => option.textContent === "Temporary profile")'), 'profile created');
  await js('const select = document.querySelector(".browser-panel select"); select.value = [...select.options].find(option => option.textContent === "Temporary profile").value; select.dispatchEvent(new Event("change", { bubbles: true }));');
  await until(() => js('[...document.querySelectorAll("button")].some(button => button.textContent === "Delete profile" && !button.disabled)'), 'profile selected');
  await js('[...document.querySelectorAll("button")].find(button => button.textContent === "Delete profile").click()');
  await until(() => js('![...document.querySelectorAll(".browser-panel option")].some(option => option.textContent === "Temporary profile")'), 'deleted profile removed locally');
  assert.equal(await js('document.querySelector(".browser-panel select").value'), '', 'deleting the selected profile clears its binding in the panel');
  await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false });
  await js('[...document.querySelectorAll("a")].find(link => link.getAttribute("href") === "/chat").click()');
  await until(() => js('location.pathname === "/chat" && document.querySelector(".browser-panel select")?.disabled'), 'new chat while browser panel stays open');
  await js('document.querySelector(".browser-panel input").focus()');
  await call('Input.insertText', { text: 'New chat profile' });
  await js('[...document.querySelectorAll("button")].find(button => button.textContent === "Create profile").click()');
  await until(() => js('[...document.querySelectorAll(".browser-panel option")].some(option => option.textContent === "New chat profile")'), 'profile created without a conversation');
  assert.deepEqual(invalidThreadRequests, [], 'profile operations must not fetch an empty thread endpoint');
  assert.equal(await js('document.querySelector(".browser-panel [role=alert]")?.textContent || ""'), '');
  assert.deepEqual(exceptions, []);
  console.log('Browser dashboard: live view, Unicode input, reconnect, checkpoint handoff recovery, mobile layout, handback and profile operations passed.');
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
