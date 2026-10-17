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
  let resizeCallback;
  runInNewContext(outputText, {
    exports, window,
    ResizeObserver: class {
      constructor(callback) { resizeCallback = callback; }
      observe() {}
      disconnect() { resizeCallback = undefined; }
    },
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
  scroll.observeTranscript(transcript);
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
    resize: () => resizeCallback?.(),
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

test('interacting with a short conversation does not show the bottom button', (t) => {
  const f = createFixture(t);
  f.transcript.scrollHeight = 500;
  f.transcript.scrollTop = 0;
  f.scroll.pauseAutoScroll();
  f.flushFrame();
  assert.equal(f.scroll.autoScroll(), false);
  assert.equal(f.scroll.showScrollToBottom(), false);
});

test('the bottom button only appears more than 200px from the bottom', (t) => {
  const f = createFixture(t);
  for (const distance of [0, 60, 150, 200, 201, 400, 100, 0]) {
    f.transcript.scrollTop = 1500 - distance;
    f.scroll.handleTranscriptScroll();
    assert.equal(f.scroll.showScrollToBottom(), distance > 200, `distance: ${distance}`);
  }
});

test('blank turn anchor space does not count toward the button threshold', (t) => {
  const f = createFixture(t);
  f.transcript.scrollHeight = 1000;
  f.transcript.scrollTop = 100;
  f.scroll.setTurnAnchorSpacerHeight(350);
  f.scroll.handleTranscriptScroll();
  assert.equal(f.scroll.showScrollToBottom(), false);
});

test('streaming while paused updates visibility without moving the transcript', (t) => {
  const f = createFixture(t);
  f.transcript.scrollTop = 1500;
  f.scroll.pauseAutoScroll();
  f.flushFrame();
  assert.equal(f.scroll.showScrollToBottom(), false);
  f.transcript.scrollHeight = 2400;
  f.scroll.scrollToBottom();
  f.flushFrame();
  assert.equal(f.scroll.showScrollToBottom(), true);
  assert.equal(f.transcript.scrollTop, 1500);
});

test('clearing a conversation removes stale button visibility', (t) => {
  const f = createFixture(t);
  f.scroll.handleTranscriptScroll();
  assert.equal(f.scroll.showScrollToBottom(), true);
  f.transcript.scrollHeight = 500;
  f.transcript.scrollTop = 0;
  f.scroll.clearTurnAnchor();
  f.flushFrame();
  assert.equal(f.scroll.showScrollToBottom(), false);
});

test('resizing the viewport refreshes the bottom button', (t) => {
  const f = createFixture(t);
  f.transcript.scrollTop = 1200;
  f.scroll.handleTranscriptScroll();
  assert.equal(f.scroll.showScrollToBottom(), true);
  f.transcript.clientHeight = 700;
  f.resize();
  f.flushFrame();
  assert.equal(f.scroll.showScrollToBottom(), false);
});

test('clicking the bottom button hides it immediately and resumes following', (t) => {
  const f = createFixture(t);
  f.scroll.handleTranscriptScroll();
  assert.equal(f.scroll.showScrollToBottom(), true);
  f.scroll.handleScrollToBottomClick();
  assert.equal(f.scroll.showScrollToBottom(), false);
  f.flushTimers();
  f.flushFrame();
  assert.equal(f.scroll.showScrollToBottom(), false);
  assert.equal(f.scroll.autoScroll(), true);
});

test('unmounting cancels visibility updates and disconnects resize observation', (t) => {
  const f = createFixture(t);
  f.scroll.pauseAutoScroll();
  assert.ok(f.frames.size > 0);
  f.dispose();
  f.resize();
  assert.equal(f.frames.size, 0);
});
