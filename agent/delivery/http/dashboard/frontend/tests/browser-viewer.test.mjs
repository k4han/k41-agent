import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/browserViewer.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
const exports = {};
runInNewContext(outputText, { exports, Blob });
const { browserPointer, browserModifiers, browserImageUrl, decodeBrowserFrame } = exports;

function binaryFrame(id = 1, width = 1280, height = 800) {
  const data = new ArrayBuffer(16), header = new DataView(data);
  header.setUint32(0, id); header.setUint32(4, width); header.setUint32(8, height);
  new Uint8Array(data, 12).set([0xff, 0xd8, 0xff, 0xd9]);
  return data;
}

test('browser input maps scaled screenshots to viewport coordinates', () => {
  const point = browserPointer(420, 250, { left: 100, top: 50, width: 640, height: 400 });
  assert.equal(point.x, 640);
  assert.equal(point.y, 400);
  const enlarged = browserPointer(1600, 900, { left: 0, top: 0, width: 1920, height: 1200 });
  assert.equal(enlarged.x, 1600 * 1280 / 1920);
  assert.equal(enlarged.y, 600);
  assert.equal(browserPointer(-10, 900, { left: 0, top: 0, width: 320, height: 200 }).y, 800);
  assert.equal(browserPointer(-10, 900, { left: 0, top: 0, width: 320, height: 200 }).x, 0);
});

test('keyboard modifiers and browser artifact URLs stay bounded', () => {
  assert.equal(browserModifiers({ altKey: true, ctrlKey: true, metaKey: false, shiftKey: true }), 11);
  const path = '/dashboard-api/browser/artifacts/' + 'a'.repeat(32);
  assert.equal(browserImageUrl('Browser image: ' + path), path);
  assert.equal(browserImageUrl('Browser image: https://other.example/image'), null);
  assert.equal(browserImageUrl({ image_url: path }), null);
});

async function flushPromises() {
  for (let index = 0; index < 5; index++) await Promise.resolve();
}

function connectionFixture(requestTicket, onFrame = async () => true, hidden = false) {
  const timers = new Map(), sockets = [], messages = [], errors = [], frames = [], states = [], samples = [], listeners = new Map();
  let sequence = 0, now = 0, disconnected = 0, replyPings = true;
  const page = {
    visibilityState: hidden ? 'hidden' : 'visible',
    addEventListener(name, callback) { listeners.set(name, callback); },
    removeEventListener(name) { listeners.delete(name); },
  };
  class FakeSocket {
    static OPEN = 1;
    constructor(address) { this.address = String(address); this.sent = []; this.readyState = 1; sockets.push(this); }
    send(raw) {
      const payload = JSON.parse(raw); this.sent.push(payload);
      if (payload.type === 'ping' && replyPings) this.message({ type: 'pong', id: payload.id, metrics: { ack_ms: 100, quality: 55 } });
    }
    close() { this.closed = true; this.readyState = 3; this.onclose?.(); }
    message(value) { this.onmessage?.({ data: JSON.stringify(value) }); }
    frame(data = binaryFrame()) { this.onmessage?.({ data }); }
  }
  const module = {};
  runInNewContext(outputText, {
    exports: module, AbortController, URL, Blob, document: page, performance: { now: () => now },
    WebSocket: FakeSocket, Math: Object.assign(Object.create(Math), { random: () => 0 }),
    window: {
      location: { href: 'https://dashboard.test/chat', protocol: 'https:' },
      setTimeout(callback, delay) { const id = ++sequence; timers.set(id, { callback, at: now + delay, delay }); return id; },
      clearTimeout(id) { timers.delete(id); },
    },
  });
  const connection = module.connectBrowserViewer({
    requestTicket, onMessage: value => messages.push(value),
    onFrame: value => { frames.push(value); return onFrame(value); },
    onDisconnect: () => disconnected++, onError: error => errors.push(error),
    onStateChange: value => states.push(value), onMetrics: value => samples.push(value),
  });
  return {
    sockets, messages, errors, frames, timers, states, samples, connection, listeners,
    dispose: () => connection.dispose(), get disconnected() { return disconnected; },
    set replyPings(value) { replyPings = value; },
    open(index = sockets.length - 1) {
      const socket = sockets[index]; socket.onopen();
      socket.message({ type: 'status', tabs: [{ id: 'first' }, { id: 'second' }], tab_id: 'first', mode: 'agent', can_control: false, sequence: 1 });
      return socket;
    },
    visibility(value) { page.visibilityState = value; listeners.get('visibilitychange')?.(); },
    async advance(duration) {
      const target = now + duration;
      while (true) {
        const next = [...timers].sort((left, right) => left[1].at - right[1].at)[0];
        if (!next || next[1].at > target) break;
        const [id, timer] = next; timers.delete(id); now = timer.at;
        timer.callback(); await flushPromises();
      }
      now = target; await flushPromises();
    },
  };
}

test('viewer retries ticket failures and authenticates a replacement socket', async () => {
  let requests = 0;
  const fixture = connectionFixture(async () => {
    if (++requests === 1) throw new Error('Temporary ticket failure');
    return { ticket: 'fresh-ticket' };
  });
  await flushPromises();
  assert.equal(fixture.errors.length, 1);
  await fixture.advance(1000);
  assert.equal(requests, 2);
  const socket = fixture.open();
  assert.equal(socket.address, 'wss://dashboard.test/dashboard-api/browser/viewer');
  assert.equal(socket.sent[0].ticket, 'fresh-ticket');
  assert.equal(socket.sent[0].visible, true);
  assert.equal(socket.binaryType, 'arraybuffer');
  socket.frame(); await flushPromises();
  fixture.dispose();
  assert.equal(fixture.timers.size, 0);
  assert.equal(fixture.listeners.size, 0);
});

test('viewer uses bounded backoff and deduplicates socket failures', async () => {
  const fixture = connectionFixture(async () => ({ ticket: 'ticket' }));
  await flushPromises();
  for (const expected of [1000, 2000, 4000, 8000, 15000, 15000]) {
    const socket = fixture.sockets.at(-1);
    socket.onerror(); socket.onclose();
    assert.equal(fixture.timers.size, 1);
    assert.equal([...fixture.timers.values()][0].delay, expected);
    await fixture.advance(expected);
  }
  const socket = fixture.open(); socket.frame(); await flushPromises();
  socket.close();
  assert.equal([...fixture.timers.values()][0].delay, 1000);
  fixture.dispose();
});

test('viewer times out stalled tickets and ignores their late results', async () => {
  let resolveTicket, signal;
  const fixture = connectionFixture(value => {
    signal = value;
    return new Promise(resolve => { resolveTicket = resolve; });
  });
  await fixture.advance(10000);
  assert.equal(signal.aborted, true);
  resolveTicket({ ticket: 'expired' }); await flushPromises();
  assert.equal(fixture.sockets.length, 0);
  assert.equal(fixture.timers.size, 1);
  fixture.dispose();
});

test('a visible viewer requires an image but static pages stay connected with heartbeats', async () => {
  const fixture = connectionFixture(async () => ({ ticket: 'ticket' }));
  await flushPromises(); const first = fixture.open();
  await fixture.advance(15000);
  assert.equal(first.closed, true);
  await fixture.advance(1000);
  const second = fixture.open(); second.frame(); await flushPromises();
  await fixture.advance(60000);
  assert.equal(second.closed, undefined);
  assert.equal(fixture.sockets.length, 2);
  assert.equal(fixture.samples.at(-1).ackMs, 100);
  fixture.dispose();
});

test('missing pongs reconnect a socket even after its first image', async () => {
  const fixture = connectionFixture(async () => ({ ticket: 'ticket' }));
  await flushPromises(); const socket = fixture.open();
  socket.frame(); await flushPromises(); fixture.replyPings = false;
  await fixture.advance(20000);
  assert.equal(socket.closed, true);
  assert.match(fixture.errors.at(-1).message, /stopped responding/);
  fixture.dispose();
});

test('viewer disposal aborts pending requests and removes all timers and listeners', async () => {
  let resolveTicket, signal;
  const fixture = connectionFixture(value => {
    signal = value; return new Promise(resolve => { resolveTicket = resolve; });
  });
  fixture.dispose(); resolveTicket({ ticket: 'unused' }); await flushPromises();
  assert.equal(signal.aborted, true);
  assert.equal(fixture.sockets.length, 0);
  assert.equal(fixture.timers.size, 0);
  assert.equal(fixture.listeners.size, 0);
});

test('binary frame headers preserve logical viewport dimensions and JPEG bytes', async () => {
  const frame = decodeBrowserFrame(binaryFrame(42, 1024, 768));
  assert.equal(frame.id, 42); assert.equal(frame.width, 1024); assert.equal(frame.height, 768);
  assert.equal(frame.image.type, 'image/jpeg');
  assert.deepEqual([...new Uint8Array(await frame.image.arrayBuffer())], [0xff, 0xd8, 0xff, 0xd9]);
  assert.throws(() => decodeBrowserFrame(new ArrayBuffer(12)), /Invalid browser frame/);
  assert.throws(() => decodeBrowserFrame(binaryFrame(0)), /Invalid browser frame header/);
  assert.throws(() => decodeBrowserFrame(binaryFrame(1, 0)), /Invalid browser frame header/);
  assert.throws(() => decodeBrowserFrame(binaryFrame(1, 100000)), /Invalid browser frame header/);
});

test('drawing uses one active image and one latest slot, acknowledging skipped images', async () => {
  const drawings = [];
  const fixture = connectionFixture(async () => ({ ticket: 'ticket' }), () => new Promise(resolve => drawings.push(resolve)));
  await flushPromises(); const socket = fixture.open();
  socket.frame(binaryFrame(1)); socket.frame(binaryFrame(2)); socket.frame(binaryFrame(3));
  assert.deepEqual(fixture.frames.map(frame => frame.id), [1]);
  assert.deepEqual(socket.sent.filter(item => item.type === 'frame_ack').map(item => [item.frame_id, item.drawn]), [[2, false]]);
  drawings.shift()(true); await flushPromises();
  assert.deepEqual(fixture.frames.map(frame => frame.id), [1, 3]);
  drawings.shift()(true); await flushPromises();
  assert.deepEqual(socket.sent.filter(item => item.type === 'frame_ack').map(item => item.frame_id), [2, 1, 3]);
  fixture.dispose();
});

test('late draws from disconnected sockets cannot acknowledge replacement frames', async () => {
  const drawings = [];
  const fixture = connectionFixture(async () => ({ ticket: 'ticket' }), () => new Promise(resolve => drawings.push(resolve)));
  await flushPromises(); const first = fixture.open(); first.frame(binaryFrame(7)); first.close();
  await fixture.advance(1000); const replacement = fixture.open();
  drawings.shift()(true); await flushPromises();
  assert.equal(first.sent.filter(item => item.type === 'frame_ack').length, 0);
  assert.equal(replacement.sent.filter(item => item.type === 'frame_ack').length, 0);
  fixture.dispose();
});

test('hidden tabs pause streaming and resume requests a fresh image without a new ticket', async () => {
  const drawings = [];
  const fixture = connectionFixture(async () => ({ ticket: 'ticket' }), () => new Promise(resolve => drawings.push(resolve)));
  await flushPromises(); const socket = fixture.open(); socket.frame(binaryFrame(1));
  fixture.visibility('hidden');
  assert.equal(socket.sent.find(item => item.type === 'visibility').visible, false);
  socket.frame(binaryFrame(2));
  assert.equal(fixture.frames.length, 1);
  assert.equal(socket.sent.find(item => item.frame_id === 2).drawn, false);
  fixture.replyPings = false; await fixture.advance(30000);
  assert.equal(socket.closed, undefined);
  drawings.shift()(true); await flushPromises();
  assert.equal(socket.sent.some(item => item.frame_id === 1), false);
  fixture.visibility('visible');
  assert.equal(socket.sent.filter(item => item.type === 'visibility').at(-1).visible, true);
  socket.message({ type: 'status', paused: false, visibility_revision: 0, sequence: 2 });
  socket.frame(binaryFrame(9));
  assert.equal(socket.sent.find(item => item.frame_id === 9).drawn, false);
  socket.message({ type: 'status', paused: false, visibility_revision: 2, sequence: 3 });
  socket.frame(binaryFrame(3)); drawings.shift()(true); await flushPromises();
  assert.equal(socket.sent.find(item => item.frame_id === 3).drawn, true);
  assert.equal(fixture.sockets.length, 1);
  fixture.dispose();
});

test('reconnecting preserves a pinned tab without negotiating capture size', async () => {
  const fixture = connectionFixture(async () => ({ ticket: 'ticket' }));
  await flushPromises(); const first = fixture.open(); first.frame(); await flushPromises();
  fixture.connection.selectTab('second');
  first.message({ type: 'status', tab_id: 'second', follow_agent: false, sequence: 2 });
  first.close(); await fixture.advance(1000); fixture.open();
  const auth = fixture.sockets[1].sent[0];
  assert.equal(auth.tab_id, 'second'); assert.equal(auth.follow_agent, false);
  assert.equal('width' in auth, false); assert.equal('height' in auth, false);
  fixture.dispose();
});

test('authentication and visibility messages leave capture dimensions to server settings', async () => {
  const fixture = connectionFixture(async () => ({ ticket: 'ticket' }));
  await flushPromises(); const socket = fixture.open();
  assert.equal('width' in socket.sent[0], false); assert.equal('height' in socket.sent[0], false);
  fixture.visibility('hidden'); fixture.visibility('visible');
  assert.deepEqual(socket.sent.filter(item => item.type === 'visibility'), [
    { type: 'visibility', visible: false, revision: 1 },
    { type: 'visibility', visible: true, revision: 2 },
  ]);
  fixture.dispose();
  assert.equal(fixture.timers.size, 0);
});

test('a viewer opened in the background waits for visibility before requiring an image', async () => {
  const fixture = connectionFixture(async () => ({ ticket: 'ticket' }), async () => true, true);
  await flushPromises(); const socket = fixture.open();
  assert.equal(socket.sent[0].visible, false);
  await fixture.advance(30000);
  assert.equal(socket.closed, undefined);
  fixture.visibility('visible');
  socket.message({ type: 'status', paused: false, visibility_revision: 1, sequence: 2 });
  socket.frame(); await flushPromises();
  assert.equal(fixture.frames.length, 1);
  fixture.dispose();
});

test('outdated status messages cannot overwrite newer control state', async () => {
  const fixture = connectionFixture(async () => ({ ticket: 'ticket' }));
  await flushPromises(); const socket = fixture.open();
  socket.message({ type: 'status', sequence: 3, mode: 'human', controller: 'other' });
  socket.message({ type: 'status', sequence: 2, mode: 'agent' });
  assert.equal(fixture.messages.at(-1).controller, 'other');
  fixture.dispose();
});

test('input telemetry measures acknowledgement and the next received image', async () => {
  const fixture = connectionFixture(async () => ({ ticket: 'ticket' }));
  await flushPromises(); const socket = fixture.open(); socket.frame(); await flushPromises();
  fixture.connection.sendInput({ type: 'text', text: 'hello' });
  const input = socket.sent.find(item => item.type === 'text');
  await fixture.advance(40); socket.message({ type: 'input_ack', input_id: input.input_id });
  await fixture.advance(30); socket.frame(binaryFrame(2)); await flushPromises();
  assert.equal(fixture.samples.at(-1).inputAckMs, 40);
  assert.equal(fixture.samples.at(-1).inputToFrameMs, 70);
  fixture.dispose();
});

test('decode failure and a stuck render reconnect without claiming a successful draw', async () => {
  const invalid = connectionFixture(async () => ({ ticket: 'ticket' }), async () => { throw new Error('Invalid JPEG'); });
  await flushPromises(); const socket = invalid.open(); socket.frame(); await flushPromises();
  assert.match(invalid.errors.at(-1).message, /Invalid JPEG/);
  assert.equal(socket.sent.some(item => item.type === 'frame_ack'), false);
  invalid.dispose();
  const stuck = connectionFixture(async () => ({ ticket: 'ticket' }), () => new Promise(() => {}));
  await flushPromises(); const hung = stuck.open(); hung.frame(); await stuck.advance(10000);
  assert.equal(hung.closed, true); assert.match(stuck.errors.at(-1).message, /rendering timed out/);
  stuck.dispose();
});

function rendererFixture() {
  const paints = new Map(), decodes = [], draws = [];
  let sequence = 0;
  const canvas = { width: 0, height: 0, getContext: () => ({ drawImage: bitmap => draws.push(bitmap) }) };
  const module = {};
  runInNewContext(outputText, {
    exports: module,
    performance,
    createImageBitmap: () => new Promise(resolve => decodes.push(resolve)),
    window: {
      requestAnimationFrame(callback) { const id = ++sequence; paints.set(id, callback); return id; },
      cancelAnimationFrame(id) { paints.delete(id); },
    },
  });
  return {
    renderer: module.createBrowserFrameRenderer(() => canvas), canvas, decodes, paints, draws,
    paint() { const callbacks = [...paints.values()]; paints.clear(); for (const callback of callbacks) callback(); },
  };
}

function bitmap() {
  return { width: 640, height: 400, closed: 0, close() { this.closed++; } };
}

test('canvas draws on the animation frame and releases decoded bitmap memory', async () => {
  const fixture = rendererFixture(), image = bitmap();
  const drawing = fixture.renderer.draw({ image: {} });
  fixture.decodes.shift()(image);
  await flushPromises();
  assert.equal(fixture.draws.length, 0);
  fixture.paint();
  assert.equal(await drawing, true);
  assert.equal(fixture.canvas.width, 640);
  assert.equal(fixture.canvas.height, 400);
  assert.equal(fixture.draws[0], image);
  assert.equal(image.closed, 1);
});

test('tab resets discard stale decodes and cancel queued paints without leaking bitmaps', async () => {
  const fixture = rendererFixture(), oldImage = bitmap(), newImage = bitmap();
  const oldDraw = fixture.renderer.draw({ image: {} });
  fixture.renderer.reset();
  const newDraw = fixture.renderer.draw({ image: {} });
  fixture.decodes[1](newImage);
  await flushPromises();
  fixture.paint();
  assert.equal(await newDraw, true);
  fixture.decodes[0](oldImage);
  assert.equal(await oldDraw, false);
  assert.deepEqual(fixture.draws, [newImage]);
  assert.equal(oldImage.closed, 1);

  const cancelledImage = bitmap(), cancelled = fixture.renderer.draw({ image: {} });
  fixture.decodes.at(-1)(cancelledImage);
  await flushPromises();
  fixture.renderer.reset();
  assert.equal(await cancelled, false);
  assert.equal(fixture.paints.size, 0);
  assert.equal(cancelledImage.closed, 1);
});

test('mouse movement keeps only the latest position and flushes before discrete inputs', () => {
  const timers = new Map(), events = [], module = {};
  let sequence = 0;
  runInNewContext(outputText, { exports: module, window: {
    setTimeout(callback, delay) { const id = ++sequence; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
  } });
  const input = module.createBrowserInputSender(event => events.push(event));
  input.send({ type: 'mousePressed', x: 0 });
  for (let x = 1; x <= 100; x++) input.send({ type: 'mouseMoved', x });
  assert.equal(timers.size, 1);
  assert.equal(events.length, 1);
  const timer = [...timers.values()][0];
  assert.equal(timer.delay, 40);
  timer.callback();
  assert.deepEqual(events.at(-1), { type: 'mouseMoved', x: 100 });
  input.send({ type: 'mouseMoved', x: 110 });
  input.send({ type: 'mouseReleased', x: 120 });
  assert.deepEqual(events.slice(-2), [{ type: 'mouseMoved', x: 110 }, { type: 'mouseReleased', x: 120 }]);
  assert.equal(timers.size, 0);
  input.send({ type: 'mouseMoved', x: 999 });
  input.reset();
  assert.equal(timers.size, 0);
  assert.equal(events.length, 4);
});
