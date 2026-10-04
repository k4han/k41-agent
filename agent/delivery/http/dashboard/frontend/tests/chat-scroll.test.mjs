import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import * as solid from 'solid-js/dist/solid.js';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/useChatScroll.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});

function createFixture(t) {
  let nextId = 1;
  const timers = new Map();
  const frames = new Map();
  const schedule = (queue, callback) => {
    const id = nextId++;
    queue.set(id, callback);
    return id;
  };
  const window = {
    setTimeout: (callback) => schedule(timers, callback),
    clearTimeout: (id) => timers.delete(id),
    requestAnimationFrame: (callback) => schedule(frames, callback),
    cancelAnimationFrame: (id) => frames.delete(id),
  };
  const exports = {};
  runInNewContext(outputText, {
    exports, window,
    require: (name) => {
      assert.equal(name, 'solid-js');
      return solid;
    },
  });
  const transcript = {
    scrollTop: 100,
    scrollHeight: 2000,
    clientHeight: 500,
    getBoundingClientRect: () => ({ top: 0 }),
    querySelector: () => ({
      getBoundingClientRect: () => ({ top: 250 - transcript.scrollTop }),
    }),
  };
  let dispose;
  const scroll = solid.createRoot((cleanup) => {
    dispose = cleanup;
    return exports.useChatScroll(() => transcript);
  });
  t.after(() => dispose());
  const flush = (queue) => {
    const snapshot = [...queue.values()];
    queue.clear();
    snapshot.forEach((callback) => callback());
  };
  return {
    scroll, transcript, timers, frames, dispose,
    flushTimers: () => flush(timers),
    flushFrame: () => flush(frames),
  };
}

test('opening a tool cancels a queued bottom scroll', (t) => {
  const f = createFixture(t);
  f.scroll.scrollToBottom();
  f.scroll.pauseAutoScroll();
  f.flushTimers();
  assert.equal(f.transcript.scrollTop, 100);
  assert.equal(f.scroll.autoScroll(), false);
});

test('opening a tool cancels an anchor before either animation frame', (t) => {
  for (const betweenFrames of [false, true]) {
    const f = createFixture(t);
    f.scroll.setTurnAnchorItemId(42);
    f.scroll.scrollToTurnAnchor(42);
    if (betweenFrames) f.flushFrame();
    f.scroll.pauseAutoScroll();
    f.flushFrame();
    f.flushFrame();
    assert.equal(f.transcript.scrollTop, 100);
    assert.equal(f.scroll.turnAnchorItemId(), null);
    assert.equal(f.scroll.turnAnchorSpacerHeight(), 0);
  }
});

test('a queued bottom scroll rechecks whether following is still enabled', (t) => {
  const f = createFixture(t);
  f.scroll.scrollToBottom();
  f.scroll.setAutoScroll(false);
  f.flushTimers();
  assert.equal(f.transcript.scrollTop, 100);
});

test('an old anchor cannot scroll after the active turn changes', (t) => {
  const f = createFixture(t);
  f.scroll.setTurnAnchorItemId(42);
  f.scroll.scrollToTurnAnchor(42);
  f.flushFrame();
  f.scroll.setTurnAnchorItemId(43);
  f.flushFrame();
  assert.equal(f.transcript.scrollTop, 100);
});

test('a new turn still aligns with its anchor after adding the spacer', (t) => {
  const f = createFixture(t);
  f.transcript.scrollHeight = 300;
  f.scroll.setTurnAnchorItemId(42);
  f.scroll.scrollToTurnAnchor(42);
  f.flushFrame();
  assert.equal(f.scroll.turnAnchorSpacerHeight(), 450);
  f.flushFrame();
  assert.equal(f.transcript.scrollTop, 250);
});

test('the scroll event caused by pausing does not resume following', (t) => {
  const f = createFixture(t);
  f.transcript.scrollTop = 1500;
  f.scroll.pauseAutoScroll();
  f.scroll.handleTranscriptScroll();
  assert.equal(f.scroll.autoScroll(), false);
  f.transcript.scrollHeight = 2500;
  f.scroll.scrollToBottom();
  f.flushTimers();
  assert.equal(f.transcript.scrollTop, 1500);
});

test('scrolling manually to the bottom releases the anchor and follows new content', (t) => {
  const f = createFixture(t);
  f.scroll.setTurnAnchorItemId(42);
  f.scroll.setAutoScroll(false);
  f.transcript.scrollTop = 1500;
  f.scroll.handleTranscriptScroll();
  assert.equal(f.scroll.turnAnchorItemId(), null);
  assert.equal(f.scroll.autoScroll(), true);
  f.transcript.scrollHeight = 3000;
  f.scroll.scrollToBottom();
  f.flushTimers();
  assert.equal(f.transcript.scrollTop, 3000);
});

test('the bottom button resumes following after opening a tool', (t) => {
  const f = createFixture(t);
  f.scroll.setTurnAnchorItemId(42);
  f.scroll.pauseAutoScroll();
  f.scroll.handleScrollToBottomClick();
  f.flushTimers();
  assert.equal(f.scroll.autoScroll(), true);
  assert.equal(f.scroll.turnAnchorItemId(), null);
  assert.equal(f.transcript.scrollTop, 2000);
});

test('repeated stream updates keep only one scheduled bottom scroll', (t) => {
  const f = createFixture(t);
  f.scroll.scrollToBottom();
  f.scroll.scrollToBottom();
  f.scroll.scrollToBottom();
  assert.equal(f.timers.size, 1);
  f.flushTimers();
  assert.equal(f.transcript.scrollTop, 2000);
});

test('unmounting cancels scheduled scrolling', (t) => {
  const f = createFixture(t);
  f.scroll.scrollToBottom();
  f.dispose();
  assert.equal(f.timers.size, 0);
  f.flushTimers();
  assert.equal(f.transcript.scrollTop, 100);
});
