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
  const frames = new Map();
  const schedule = (queue, callback) => {
    const id = nextId++;
    queue.set(id, callback);
    return id;
  };
  const window = {
    requestAnimationFrame: (callback) => schedule(frames, callback),
    cancelAnimationFrame: (id) => frames.delete(id),
  };
  const exports = {};
  let resizeCallback;
  let mutationCallback;
  const observed = new Set();
  runInNewContext(outputText, {
    exports, window,
    ResizeObserver: class {
      constructor(callback) { resizeCallback = callback; }
      observe(target) { observed.add(target); }
      unobserve(target) { observed.delete(target); }
      disconnect() { observed.clear(); resizeCallback = undefined; }
    },
    MutationObserver: class {
      constructor(callback) { mutationCallback = callback; }
      observe() {}
      disconnect() { mutationCallback = undefined; }
    },
    require: (name) => {
      assert.equal(name, 'solid-js');
      return solid;
    },
  });
  const row = { nodeType: 1 };
  const transcript = {
    children: [row],
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
    scroll, transcript, row, frames, dispose,
    flushFrame: () => flush(frames),
    resize: (target = transcript) => {
      if (observed.has(target)) resizeCallback?.();
    },
    mutate: (addedNodes, removedNodes = []) => mutationCallback?.([{ addedNodes, removedNodes }]),
  };
}

test('opening a tool cancels a queued bottom scroll', (t) => {
  const f = createFixture(t);
  f.scroll.scrollToBottom();
  f.scroll.pauseAutoScroll();
  f.flushFrame();
  assert.equal(f.transcript.scrollTop, 100);
  assert.equal(f.scroll.autoScroll(), false);
});

test('opening a tool cancels an anchor before its scheduled frame', (t) => {
  const f = createFixture(t);
  f.scroll.setTurnAnchorItemId(42);
  f.scroll.scrollToTurnAnchor(42);
  f.scroll.pauseAutoScroll();
  f.flushFrame();
  assert.equal(f.transcript.scrollTop, 100);
  assert.equal(f.scroll.turnAnchorItemId(), null);
  assert.equal(f.scroll.turnAnchorSpacerHeight(), 0);
});

test('a queued bottom scroll rechecks whether following is still enabled', (t) => {
  const f = createFixture(t);
  f.scroll.scrollToBottom();
  f.scroll.setAutoScroll(false);
  f.flushFrame();
  assert.equal(f.transcript.scrollTop, 100);
});

test('an old anchor cannot scroll after the active turn changes', (t) => {
  const f = createFixture(t);
  f.scroll.setTurnAnchorItemId(42);
  f.scroll.scrollToTurnAnchor(42);
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
  assert.equal(f.transcript.scrollTop, 250);
});

test('a delayed programmatic scroll event cannot cancel following after a tool grows', (t) => {
  const f = createFixture(t);
  f.transcript.scrollTop = 1500;
  f.transcript.scrollHeight += 300;
  f.scroll.scrollToBottom();
  f.scroll.handleTranscriptScroll();
  assert.equal(f.scroll.autoScroll(), true);
  f.flushFrame();
  assert.equal(f.transcript.scrollTop, 2300);
});

test('an anchor above the bottom keeps following after the reply exceeds the viewport', (t) => {
  const f = createFixture(t);
  f.scroll.setTurnAnchorItemId(42);
  f.transcript.scrollTop = 250;
  f.scroll.handleTranscriptScroll();
  assert.equal(f.scroll.autoScroll(), true);
  assert.equal(f.scroll.turnAnchorItemId(), 42);
});

test('the bottom button removes the spacer and restores the bottom in the same frame', (t) => {
  const f = createFixture(t);
  f.scroll.setTurnAnchorItemId(42);
  f.scroll.setTurnAnchorSpacerHeight(450);
  f.scroll.handleScrollToBottomClick();
  assert.equal(f.scroll.turnAnchorSpacerHeight(), 450);
  f.flushFrame();
  assert.equal(f.scroll.turnAnchorSpacerHeight(), 0);
  assert.equal(f.transcript.scrollTop, 2000);
});

test('streamed updates cannot replace a queued explicit bottom request with an anchor scroll', (t) => {
  const f = createFixture(t);
  f.scroll.setTurnAnchorItemId(42);
  f.scroll.scrollToBottom(true);
  f.scroll.scrollToBottom();
  f.flushFrame();
  assert.equal(f.scroll.turnAnchorItemId(), null);
  assert.equal(f.transcript.scrollTop, 2000);
});

test('a new turn supersedes the previous turn queued bottom scroll', (t) => {
  const f = createFixture(t);
  f.scroll.scrollToBottom(true);
  f.scroll.setTurnAnchorItemId(42);
  f.scroll.scrollToTurnAnchor(42);
  f.flushFrame();
  assert.equal(f.scroll.turnAnchorItemId(), 42);
  assert.equal(f.transcript.scrollTop, 250);
});

test('asynchronous tool layout keeps following without another streamed chunk', (t) => {
  const f = createFixture(t);
  f.transcript.scrollTop = 1500;
  f.transcript.scrollHeight += 300;
  f.resize(f.row);
  f.flushFrame();
  assert.equal(f.transcript.scrollTop, 2300);
  assert.equal(f.scroll.autoScroll(), true);
});

test('the scroll event caused by pausing does not resume following', (t) => {
  const f = createFixture(t);
  f.transcript.scrollTop = 1500;
  f.scroll.pauseAutoScroll();
  f.scroll.handleTranscriptScroll();
  assert.equal(f.scroll.autoScroll(), false);
  f.transcript.scrollHeight = 2500;
  f.scroll.scrollToBottom();
  f.flushFrame();
  assert.equal(f.transcript.scrollTop, 1500);
});

test('opening a tool releases the anchor without shrinking the scroll range', (t) => {
  const f = createFixture(t);
  f.scroll.setTurnAnchorItemId(42);
  f.scroll.setTurnAnchorSpacerHeight(350);
  f.scroll.pauseAutoScroll();
  assert.equal(f.scroll.autoScroll(), false);
  assert.equal(f.scroll.turnAnchorItemId(), null);
  assert.equal(f.scroll.turnAnchorSpacerHeight(), 350);
  f.scroll.scrollToBottom();
  f.flushFrame();
  assert.equal(f.transcript.scrollTop, 100);
});

test('scrolling down to the content bottom clears the spacer before entering blank space', (t) => {
  const f = createFixture(t);
  f.scroll.setTurnAnchorSpacerHeight(350);
  f.transcript.scrollTop = 1000;
  f.scroll.pauseAutoScroll();
  f.transcript.scrollTop = 1150;
  f.scroll.handleTranscriptScroll();
  assert.equal(f.scroll.turnAnchorSpacerHeight(), 0);
  assert.equal(f.scroll.autoScroll(), true);
});

test('scrolling up through a padded turn keeps following paused', (t) => {
  const f = createFixture(t);
  f.scroll.setTurnAnchorSpacerHeight(350);
  f.transcript.scrollTop = 1200;
  f.scroll.pauseAutoScroll();
  f.transcript.scrollTop = 1180;
  f.scroll.handleTranscriptScroll();
  assert.equal(f.scroll.autoScroll(), false);
  assert.equal(f.scroll.turnAnchorSpacerHeight(), 350);
});

test('a programmatic turn anchor scroll keeps following while its spacer is present', (t) => {
  const f = createFixture(t);
  f.scroll.setTurnAnchorItemId(42);
  f.scroll.setTurnAnchorSpacerHeight(350);
  f.transcript.scrollTop = 1150;
  f.scroll.handleTranscriptScroll();
  assert.equal(f.scroll.autoScroll(), true);
  assert.equal(f.scroll.turnAnchorItemId(), 42);
  assert.equal(f.scroll.turnAnchorSpacerHeight(), 350);
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
  f.flushFrame();
  assert.equal(f.transcript.scrollTop, 3000);
});

test('the bottom button resumes following after opening a tool', (t) => {
  const f = createFixture(t);
  f.scroll.setTurnAnchorItemId(42);
  f.scroll.pauseAutoScroll();
  f.scroll.handleScrollToBottomClick();
  f.flushFrame();
  assert.equal(f.scroll.autoScroll(), true);
  assert.equal(f.scroll.turnAnchorItemId(), null);
  assert.equal(f.transcript.scrollTop, 2000);
});

test('scrolling down at the bottom keeps following without a scroll event', (t) => {
  const f = createFixture(t);
  f.transcript.scrollTop = 1500;
  f.scroll.handleUserScroll(120);
  assert.equal(f.scroll.autoScroll(), true);
  f.transcript.scrollHeight += 400;
  f.scroll.scrollToBottom();
  f.flushFrame();
  assert.equal(f.transcript.scrollTop, 2400);
});

test('scrolling down at the bottom resumes following after inspecting a tool', (t) => {
  const f = createFixture(t);
  f.transcript.scrollTop = 1500;
  f.scroll.pauseAutoScroll();
  f.scroll.handleUserScroll(120);
  assert.equal(f.scroll.autoScroll(), true);
  f.transcript.scrollHeight += 400;
  f.resize(f.row);
  f.flushFrame();
  assert.equal(f.transcript.scrollTop, 2400);
});

test('scrolling up from the bottom still pauses following before new content arrives', (t) => {
  const f = createFixture(t);
  f.transcript.scrollTop = 1500;
  f.scroll.handleUserScroll(-120);
  f.transcript.scrollHeight += 400;
  f.resize(f.row);
  f.flushFrame();
  assert.equal(f.scroll.autoScroll(), false);
  assert.equal(f.transcript.scrollTop, 1500);
});

test('scrolling down while reading earlier content cancels queued following', (t) => {
  const f = createFixture(t);
  f.scroll.scrollToBottom();
  f.scroll.handleUserScroll(120);
  f.flushFrame();
  assert.equal(f.scroll.autoScroll(), false);
  assert.equal(f.transcript.scrollTop, 100);
});

test('scrolling down at the content bottom releases the padded turn anchor', (t) => {
  const f = createFixture(t);
  f.scroll.setTurnAnchorItemId(42);
  f.scroll.setTurnAnchorSpacerHeight(350);
  f.transcript.scrollTop = 1150;
  f.scroll.handleUserScroll(120);
  f.flushFrame();
  assert.equal(f.scroll.autoScroll(), true);
  assert.equal(f.scroll.turnAnchorItemId(), null);
  assert.equal(f.scroll.turnAnchorSpacerHeight(), 0);
});

test('horizontal scrolling does not change whether the transcript is following', (t) => {
  const f = createFixture(t);
  f.scroll.setTurnAnchorItemId(42);
  f.scroll.handleUserScroll(0);
  assert.equal(f.scroll.autoScroll(), true);
  assert.equal(f.scroll.turnAnchorItemId(), 42);
  f.scroll.pauseAutoScroll();
  f.scroll.handleUserScroll(0);
  assert.equal(f.scroll.autoScroll(), false);
});

test('repeated stream updates keep only one scheduled bottom scroll', (t) => {
  const f = createFixture(t);
  f.scroll.scrollToBottom();
  const pendingFrames = f.frames.size;
  f.scroll.scrollToBottom();
  f.scroll.scrollToBottom();
  assert.equal(f.frames.size, pendingFrames);
  f.flushFrame();
  assert.equal(f.transcript.scrollTop, 2000);
});

test('unmounting cancels scheduled scrolling', (t) => {
  const f = createFixture(t);
  f.scroll.scrollToBottom();
  f.dispose();
  assert.equal(f.frames.size, 0);
  f.flushFrame();
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

test('expanding a tool refreshes the bottom button without another streamed chunk', (t) => {
  const f = createFixture(t);
  f.transcript.scrollTop = 1500;
  f.scroll.pauseAutoScroll();
  f.flushFrame();
  assert.equal(f.scroll.showScrollToBottom(), false);
  f.transcript.scrollHeight += 300;
  f.resize(f.row);
  f.flushFrame();
  assert.equal(f.scroll.showScrollToBottom(), true);
  assert.equal(f.transcript.scrollTop, 1500);
  assert.equal(f.scroll.autoScroll(), false);
});

test('new tool rows refresh scrolling and stop reporting size changes after removal', (t) => {
  const f = createFixture(t);
  const tool = { nodeType: 1 };
  f.transcript.scrollTop = 1500;
  f.scroll.pauseAutoScroll();
  f.mutate([tool]);
  f.flushFrame();
  f.transcript.scrollHeight += 400;
  f.resize(tool);
  f.flushFrame();
  assert.equal(f.scroll.showScrollToBottom(), true);
  f.transcript.scrollHeight -= 400;
  f.mutate([], [tool]);
  f.flushFrame();
  assert.equal(f.scroll.showScrollToBottom(), false);
  f.resize(tool);
  assert.equal(f.frames.size, 0);
});

test('clicking the bottom button hides it immediately and resumes following', (t) => {
  const f = createFixture(t);
  f.scroll.handleTranscriptScroll();
  assert.equal(f.scroll.showScrollToBottom(), true);
  f.scroll.handleScrollToBottomClick();
  assert.equal(f.scroll.showScrollToBottom(), false);
  f.flushFrame();
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
  f.mutate([{ nodeType: 1 }]);
  assert.equal(f.frames.size, 0);
});
