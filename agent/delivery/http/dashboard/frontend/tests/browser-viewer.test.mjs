import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/browserViewer.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
const exports = {};
runInNewContext(outputText, { exports });
const { browserPointer, browserModifiers, browserImageUrl } = exports;

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

function connectionFixture(requestTicket) {
  const timers = new Map(), sockets = [], messages = [], errors = [];
  let sequence = 0, now = 0, disconnected = 0;
  class FakeSocket {
    constructor(address) { this.address = String(address); this.sent = []; sockets.push(this); }
    send(raw) { this.sent.push(JSON.parse(raw)); }
    close() { this.closed = true; this.onclose?.(); }
    message(value) { this.onmessage?.({ data: JSON.stringify(value) }); }
  }
  const module = {};
  runInNewContext(outputText, {
    exports: module, AbortController, URL, WebSocket: FakeSocket, Math: Object.assign(Object.create(Math), { random: () => 0 }),
    window: {
      location: { href: 'https://dashboard.test/chat', protocol: 'https:' },
      setTimeout(callback, delay) { const id = ++sequence; timers.set(id, { callback, at: now + delay, delay }); return id; },
      clearTimeout(id) { timers.delete(id); },
    },
  });
  const dispose = module.connectBrowserViewer({
    requestTicket, onSocket: () => {}, onMessage: value => messages.push(value),
    onDisconnect: () => disconnected++, onError: error => errors.push(error),
  });
  return {
    sockets, messages, errors, timers, dispose, get disconnected() { return disconnected; },
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
  socket.message({ type: 'frame', data: 'image', metadata: {} });
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
  fixture.sockets.at(-1).message({ type: 'frame', data: 'image', metadata: {} });
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
  fixture.sockets[1].message({ type: 'frame', data: 'image', metadata: {} });
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
