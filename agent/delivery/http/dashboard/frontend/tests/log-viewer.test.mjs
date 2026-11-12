import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compileFunction } from 'node:vm';
import test from 'node:test';
import * as solid from 'solid-js/dist/solid.js';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/useLogViewer.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const tick = () => new Promise(resolve => setImmediate(resolve));
const page = (text, options = {}) => ({
  source: 'server', text, bytes: text.length, before: 'start', after: 'end', has_older: false, has_newer: false, ...options,
});

function fixture(t) {
  const mounts = [], cleanups = [], pending = [], frames = new Map(), timers = new Map(), nodes = new Map();
  let nextId = 0, viewer, position = 0;
  const exports = {};
  const viewport = {
    clientHeight: 100,
    get scrollHeight() { return 100 + (viewer?.chunks() ?? []).reduce((sum, chunk) => sum + chunk.page.text.length, 0); },
    get scrollTop() { return position; },
    set scrollTop(value) { position = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); },
    getBoundingClientRect: () => ({ top: 20 }),
    querySelectorAll() {
      return viewer.chunks().map(chunk => {
        if (!nodes.has(chunk.id)) nodes.set(chunk.id, {
          get isConnected() { return viewer.chunks().includes(chunk); },
          getBoundingClientRect() {
            let top = 20 - position;
            for (const item of viewer.chunks()) {
              if (item === chunk) break;
              top += item.page.text.length;
            }
            return { top, bottom: top + chunk.page.text.length };
          },
        });
        return nodes.get(chunk.id);
      });
    },
  };
  const globals = {
    exports, URLSearchParams, AbortController, TextEncoder, Error,
    requestAnimationFrame: callback => { const id = ++nextId; frames.set(id, callback); return id; },
    cancelAnimationFrame: id => frames.delete(id),
    setInterval: callback => { const id = ++nextId; timers.set(id, callback); return id; },
    clearInterval: id => timers.delete(id),
    require(name) {
      if (name === 'solid-js') return { ...solid, onMount: callback => mounts.push(callback), onCleanup: callback => cleanups.push(callback) };
      if (name === './endpoints') return { API_PATHS: { logs: '/logs', logContent: id => `/logs/${id}` } };
      if (name === './api') return { apiFetch(url, init) {
        if (url === '/logs') return Promise.resolve({ sources: [] });
        return new Promise((resolve, reject) => pending.push({ url, init, resolve, reject }));
      } };
      throw new Error(`Unexpected import: ${name}`);
    },
  };
  compileFunction(outputText, Object.keys(globals))(...Object.values(globals));
  viewer = exports.useLogViewer(() => viewport);
  mounts.forEach(callback => callback());
  const flush = () => { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(callback => callback()); };
  t.after(() => cleanups.forEach(callback => callback()));
  return {
    viewer, viewport, pending, frames, timers, exports, flush,
    poll: () => [...timers.values()].forEach(callback => callback()),
    dispose: () => cleanups.forEach(callback => callback()),
    async respond(value) { pending.shift().resolve(value); await tick(); flush(); },
  };
}

test('opens at the latest output, loads older output without moving the visible anchor', async t => {
  const f = fixture(t);
  assert.equal(f.pending[0].url, '/logs/server?');
  await f.respond(page('n'.repeat(1000), { has_older: true }));
  assert.equal(f.viewport.scrollTop, 1000);
  f.viewport.scrollTop = 0;
  f.viewer.onScroll();
  assert.equal(new URL(f.pending[0].url, 'http://local').searchParams.get('before'), 'start');
  await f.respond(page('o'.repeat(1000), { after: 'start' }));
  assert.equal(f.viewport.scrollTop, 1000);
  assert.equal(f.viewer.chunks().map(chunk => chunk.page.text).join(''), 'o'.repeat(1000) + 'n'.repeat(1000));
  assert.equal(f.viewer.hasOlder(), false);
});

test('polling follows the end and leaves a reader of history in place', async t => {
  const f = fixture(t);
  await f.respond(page('a'.repeat(1000)));
  f.poll();
  await f.respond(page('b'.repeat(100), { before: 'end', after: 'end2' }));
  assert.equal(f.viewport.scrollTop, 1100);
  f.viewport.scrollTop = 300;
  f.poll();
  await f.respond(page('new output', { after: 'end3' }));
  assert.equal(f.viewport.scrollTop, 300);
  assert.equal(f.viewer.chunks().length, 2);
  assert.equal(f.viewer.newLogs(), true);
  f.viewer.latest();
  await f.respond(page('latest'.repeat(200)));
  assert.equal(f.viewport.scrollTop, 1200);
  assert.equal(f.viewer.newLogs(), false);
});

test('scrolling during a poll does not pull the reader down', async t => {
  const f = fixture(t);
  await f.respond(page('a'.repeat(1000)));
  f.poll();
  f.viewport.scrollTop = 100;
  await f.respond(page('new output'));
  assert.equal(f.viewport.scrollTop, 100);
  assert.equal(f.viewer.chunks().length, 1);
  assert.equal(f.viewer.newLogs(), true);
});

test('live following survives a burst spanning multiple pages and scroll callbacks', async t => {
  const f = fixture(t);
  await f.respond(page('initial'.repeat(200)));
  f.poll();
  await f.respond(page('a'.repeat(65536), { before: 'end', after: 'burst1', has_newer: true }));
  assert.equal(f.viewport.scrollTop, f.viewport.scrollHeight - f.viewport.clientHeight);

  // Browsers dispatch a scroll event after the viewer moves to the bottom.
  f.viewer.onScroll();
  assert.equal(new URL(f.pending[0].url, 'http://local').searchParams.get('after'), 'burst1');
  await f.respond(page('b'.repeat(65536), { before: 'burst1', after: 'burst2', has_newer: true }));
  assert.equal(f.viewport.scrollTop, f.viewport.scrollHeight - f.viewport.clientHeight);

  f.poll();
  await f.respond(page('c'.repeat(1000), { before: 'burst2', after: 'burst3' }));
  assert.equal(f.viewport.scrollTop, f.viewport.scrollHeight - f.viewport.clientHeight);
  f.poll();
  await f.respond(page('continued output\n', { before: 'burst3', after: 'end2' }));
  assert.equal(f.viewport.scrollTop, f.viewport.scrollHeight - f.viewport.clientHeight);
  assert.equal(f.viewer.newLogs(), false);
});

test('scrolling up during a multi-page burst stops live following', async t => {
  const f = fixture(t);
  await f.respond(page('initial'.repeat(200)));
  f.poll();
  await f.respond(page('a'.repeat(65536), { after: 'burst1', has_newer: true }));
  f.viewer.onScroll();
  f.viewport.scrollTop = 300;
  f.viewer.onScroll();
  await f.respond(page('b'.repeat(65536), { before: 'burst1', after: 'burst2' }));
  assert.equal(f.viewport.scrollTop, 300);
  f.poll();
  await f.respond(page('new output', { after: 'burst3' }));
  assert.equal(f.viewport.scrollTop, 300);
  assert.equal(f.viewer.newLogs(), true);
});

test('loading newer history at the bottom preserves the reading position', async t => {
  const f = fixture(t);
  await f.respond(page('n'.repeat(65536), { has_older: true }));
  for (let index = 0; index < 32; index++) {
    f.viewer.older();
    await f.respond(page('o'.repeat(65536), { before: `older${index}`, has_older: true }));
  }
  assert.equal(f.viewer.hasNewer(), true);
  f.viewport.scrollTop = f.viewport.scrollHeight;
  f.viewer.onScroll();
  const position = f.viewport.scrollTop;
  const firstSize = f.viewer.chunks()[0].size;
  await f.respond(page('later history'.repeat(1000), { has_newer: true }));
  assert.equal(f.viewport.scrollTop, position - firstSize);
});

test('changing sources aborts requests and ignores late responses', async t => {
  const f = fixture(t);
  const old = f.pending.shift();
  f.viewer.selectSource('tray');
  assert.equal(old.init.signal.aborted, true);
  old.resolve(page('wrong source'));
  await tick();
  assert.equal(f.viewer.chunks().length, 0);
  await f.respond(page('tray output', { source: 'tray' }));
  assert.equal(f.viewer.source(), 'tray');
  assert.equal(f.viewer.chunks()[0].page.text, 'tray output');
});

test('errors can be retried and unmount aborts requests and polling', async t => {
  const f = fixture(t);
  f.pending.shift().reject(new Error('Read failed'));
  await tick();
  assert.equal(f.viewer.error(), 'Read failed');
  f.poll();
  assert.equal(f.pending.length, 0);
  f.viewer.retry();
  await f.respond(page('recovered'));
  assert.equal(f.viewer.error(), '');
  f.poll();
  const request = f.pending.shift();
  f.dispose();
  assert.equal(request.init.signal.aborted, true);
  assert.equal(f.timers.size, 0);
  request.resolve(page('late output'));
  await tick();
  assert.equal(f.viewer.chunks()[0].page.text, 'recovered');
});

test('window trimming retains cursors for loading either discarded edge again', t => {
  const f = fixture(t);
  const chunks = [1, 2, 3].map(id => ({ id, page: page(String(id), { before: `b${id}`, after: `a${id}` }), size: 4 }));
  const older = f.exports.trimLogWindow(chunks, 'before', 8);
  assert.deepEqual(older.chunks.map(chunk => chunk.id), [1, 2]);
  assert.equal(older.chunks.at(-1).page.after, 'a2');
  const newer = f.exports.trimLogWindow(chunks, 'after', 8);
  assert.deepEqual(newer.chunks.map(chunk => chunk.id), [2, 3]);
  assert.equal(newer.chunks[0].page.before, 'b2');
  assert.equal(older.trimmed, true);
  assert.equal(newer.trimmed, true);
});

test('empty logs are polled until output arrives', async t => {
  const f = fixture(t);
  await f.respond(page('', { before: null, after: null }));
  assert.equal(f.viewer.loaded(), true);
  f.poll();
  assert.equal(f.pending[0].url, '/logs/server?');
  await f.respond(page('first output'));
  assert.equal(f.viewer.chunks()[0].page.text, 'first output');
});
