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

function connectionFixture(requestTicket, onFrame = async () => {}) {
  const timers = new Map(), sockets = [], messages = [], errors = [], frames = [];
  let sequence = 0, now = 0, disconnected = 0;
  class FakeSocket {
    static OPEN = 1;
    constructor(address) { this.address = String(address); this.sent = []; this.readyState = 1; sockets.push(this); }
    send(raw) { this.sent.push(JSON.parse(raw)); }
    close() { this.closed = true; this.onclose?.(); }
    message(value) { this.onmessage?.({ data: JSON.stringify(value) }); }
    frame(data = binaryFrame()) { this.onmessage?.({ data }); }
  }
  const module = {};
  runInNewContext(outputText, {
    exports: module, AbortController, URL, Blob, WebSocket: FakeSocket, Math: Object.assign(Object.create(Math), { random: () => 0 }),
    window: {
      location: { href: 'https://dashboard.test/chat', protocol: 'https:' },
      setTimeout(callback, delay) { const id = ++sequence; timers.set(id, { callback, at: now + delay, delay }); return id; },
      clearTimeout(id) { timers.delete(id); },
    },
  });
  const dispose = module.connectBrowserViewer({
    requestTicket, onSocket: () => {}, onMessage: value => messages.push(value),
    onFrame: value => { frames.push(value); return onFrame(value); },
    onDisconnect: () => disconnected++, onError: error => errors.push(error),
  });
  return {
    sockets, messages, errors, frames, timers, dispose, get disconnected() { return disconnected; },
    async nextTimer() {
      const [id, timer] = [...timers].sort((left, right) => left[1].at - right[1].at)[0];
      timers.delete(id); now = timer.at; timer.callback(); await flushPromises(); return timer.delay;
    },
  };
}

test('viewer retries failed ticket requests and authenticates the replacement socket', async () => {
  let requests = 0;
  const fixture = connectionFixture(async () => {
    if (++requests === 1) throw new Error('Temporary ticket failure');
    return { ticket: 'fresh-ticket' };
  });
  await flushPromises();
  assert.equal(fixture.errors.length, 1);
  assert.equal(await fixture.nextTimer(), 1000);
  assert.equal(requests, 2);
  const socket = fixture.sockets[0];
  assert.equal(socket.address, 'wss://dashboard.test/dashboard-api/browser/viewer');
  socket.onopen();
  assert.equal(socket.sent[0].ticket, 'fresh-ticket');
  assert.equal(socket.binaryType, 'arraybuffer');
  socket.frame();
  assert.equal(fixture.timers.size, 0);
  fixture.dispose();
  assert.equal(fixture.timers.size, 0);
});

test('viewer uses bounded backoff, deduplicates socket failures and resets after a frame', async () => {
  const fixture = connectionFixture(async () => ({ ticket: 'ticket' }));
  await flushPromises();
  for (const expected of [1000, 2000, 4000, 8000, 15000, 15000]) {
    const socket = fixture.sockets.at(-1);
    socket.onerror(); socket.onclose();
    assert.equal(fixture.timers.size, 1);
    assert.equal(await fixture.nextTimer(), expected);
  }
  fixture.sockets.at(-1).frame();
  fixture.sockets.at(-1).close();
  assert.equal(await fixture.nextTimer(), 1000);
  fixture.dispose();
});

test('viewer times out stalled tickets and ignores their late results', async () => {
  let resolveTicket, signal;
  const fixture = connectionFixture(value => {
    signal = value;
    return new Promise(resolve => { resolveTicket = resolve; });
  });
  assert.equal(await fixture.nextTimer(), 10000);
  assert.equal(signal.aborted, true);
  resolveTicket({ ticket: 'expired' });
  await flushPromises();
  assert.equal(fixture.sockets.length, 0);
  assert.equal(fixture.timers.size, 1);
  fixture.dispose();
  assert.equal(fixture.timers.size, 0);
});

test('viewer requires a first frame but keeps static pages connected afterwards', async () => {
  const fixture = connectionFixture(async () => ({ ticket: 'ticket' }));
  await flushPromises();
  fixture.sockets[0].onopen();
  fixture.sockets[0].message({ type: 'status' });
  assert.equal(await fixture.nextTimer(), 10000);
  assert.equal(fixture.sockets[0].closed, true);
  assert.equal(await fixture.nextTimer(), 1000);
  fixture.sockets[1].frame();
  assert.equal(fixture.timers.size, 0);
  assert.equal(fixture.sockets[1].closed, undefined);
  fixture.dispose();
});

test('viewer disposal aborts pending requests without creating sockets or retries', async () => {
  let resolveTicket, signal;
  const fixture = connectionFixture(value => {
    signal = value;
    return new Promise(resolve => { resolveTicket = resolve; });
  });
  fixture.dispose();
  resolveTicket({ ticket: 'unused' });
  await flushPromises();
  assert.equal(signal.aborted, true);
  assert.equal(fixture.sockets.length, 0);
  assert.equal(fixture.timers.size, 0);
});

test('binary frame headers preserve viewport dimensions and JPEG bytes', async () => {
  const frame = decodeBrowserFrame(binaryFrame(42, 1024, 768));
  assert.equal(frame.id, 42);
  assert.equal(frame.width, 1024);
  assert.equal(frame.height, 768);
  assert.equal(frame.image.type, 'image/jpeg');
  assert.deepEqual([...new Uint8Array(await frame.image.arrayBuffer())], [0xff, 0xd8, 0xff, 0xd9]);
  assert.throws(() => decodeBrowserFrame(new ArrayBuffer(12)), /Invalid browser frame/);
  assert.throws(() => decodeBrowserFrame(binaryFrame(0)), /Invalid browser frame header/);
  assert.throws(() => decodeBrowserFrame(binaryFrame(1, 0)), /Invalid browser frame header/);
});

test('viewer acknowledges only after drawing and ignores late draws from an old connection', async () => {
  const drawings = [];
  const fixture = connectionFixture(async () => ({ ticket: 'ticket' }), () => new Promise(resolve => drawings.push(resolve)));
  await flushPromises();
  const first = fixture.sockets[0];
  first.frame(binaryFrame(7));
  assert.equal(first.sent.length, 0);
  drawings.shift()();
  await flushPromises();
  assert.deepEqual(first.sent[0], { type: 'frame_ack', frame_id: 7 });
  first.frame(binaryFrame(8));
  first.close();
  await fixture.nextTimer();
  const replacement = fixture.sockets[1];
  drawings.shift()();
  await flushPromises();
  assert.equal(first.sent.length, 1);
  assert.equal(replacement.sent.length, 0);
  replacement.frame(binaryFrame(9));
  fixture.dispose();
  drawings.shift()();
  await flushPromises();
  assert.equal(replacement.sent.length, 0);
});

test('a decode failure reconnects without acknowledging the failed frame', async () => {
  const fixture = connectionFixture(async () => ({ ticket: 'ticket' }), async () => { throw new Error('Invalid JPEG'); });
  await flushPromises();
  fixture.sockets[0].frame();
  await flushPromises();
  assert.equal(fixture.errors[0].message, 'Invalid JPEG');
  assert.equal(fixture.sockets[0].sent.length, 0);
  assert.equal(fixture.sockets[0].closed, true);
  assert.equal(await fixture.nextTimer(), 1000);
  fixture.dispose();
});

function rendererFixture() {
  const paints = new Map(), decodes = [], draws = [];
  let sequence = 0;
  const canvas = { width: 0, height: 0, getContext: () => ({ drawImage: bitmap => draws.push(bitmap) }) };
  const module = {};
  runInNewContext(outputText, {
    exports: module,
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
