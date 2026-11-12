import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compileFunction } from 'node:vm';
import test from 'node:test';
import * as solid from 'solid-js';
import ts from 'typescript';

function load(path, dependencies = {}, globals = {}) {
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  const context = { exports, AbortController, console, ...globals, require(name) {
    if (name === 'solid-js') return solid;
    assert.ok(name in dependencies, `Unexpected dependency: ${name}`);
    return dependencies[name];
  } };
  compileFunction(outputText, Object.keys(context))(...Object.values(context));
  return exports;
}

const refresh = load('../src/lib/workspaceRefresh.ts');
const events = load('../src/lib/eventConstants.ts');
const tick = () => new Promise(resolve => setImmediate(resolve));

function environment() {
  let now = 1_000_000, nextId = 0;
  const timers = new Map();
  const target = () => {
    const listeners = new Map();
    return { listeners,
      addEventListener(name, handler) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(handler); },
      removeEventListener(name, handler) { listeners.get(name)?.delete(handler); },
      emit(name, detail) { for (const handler of [...(listeners.get(name) || [])]) handler({ detail }); },
    };
  };
  const window = { ...target(),
    setTimeout(handler, delay) { const id = ++nextId; timers.set(id, { handler, due: now + delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  const document = { ...target(), visibilityState: 'visible' };
  return { window, document, timers, globals: { window, document },
    hide(hidden) { document.visibilityState = hidden ? 'hidden' : 'visible'; document.emit('visibilitychange'); },
    async advance(ms) {
      const until = now + ms;
      while (true) {
        const pending = [...timers].filter(([, value]) => value.due <= until).sort((a, b) => a[1].due - b[1].due)[0];
        if (!pending) break;
        now = pending[1].due;
        timers.delete(pending[0]);
        pending[1].handler();
        await tick();
      }
      now = until;
      await tick();
    },
  };
}

function mount(t, callback) {
  let dispose;
  const value = solid.createRoot(cleanup => { dispose = cleanup; return callback(); });
  t.after(dispose);
  return { value, dispose };
}

const browserStatus = (generation = null, handoff = null) => ({ open: Boolean(generation), generation, revision: 0,
  profile_id: null, mode: handoff ? 'human' : 'agent', tabs: [], handoff, handoff_expired: false, images: [] });

test('workspace identities exclude unrelated events and recognize shared workspaces', () => {
  const current = { thread_id: 'one', project_id: 1, project_workspace_id: 2, backend: 'local', locator: '/work' };
  assert.equal(refresh.matchesWorkspaceEvent({ threadId: 'other' }, current), false);
  assert.equal(refresh.matchesWorkspaceEvent({ thread_id: 'other', project_workspace_id: 2 }, current), true);
  assert.equal(refresh.matchesWorkspaceEvent({ thread_id: 'one', project_workspace_id: 9 }, current), false);
  assert.equal(refresh.matchesWorkspaceEvent({ project_id: 9 }, current), false);
  assert.equal(refresh.matchesWorkspaceEvent({ backend: 'local', locator: '/other' }, current), false);
  assert.equal(refresh.matchesWorkspaceEvent(undefined, current), true);
});

test('only reachable expanded directories refresh for relative and absolute workspace paths', () => {
  for (const prefix of ['', '/project/', 'D:\\project\\']) {
    const parent = `${prefix}src`, child = `${prefix}src/nested`;
    const directory = path => ({ path, kind: 'directory' });
    const entries = { [parent]: [directory(child)], [child]: [] };
    const expanded = { [parent]: true, [child]: true, unrelated: true };
    assert.deepEqual(refresh.visibleWorkspaceDirectories([directory(parent)], entries, expanded), [parent, child]);
    expanded[parent] = false;
    assert.deepEqual(refresh.visibleWorkspaceDirectories([directory(parent)], entries, expanded), []);
    assert.deepEqual(refresh.visibleWorkspaceDirectories([], entries, expanded), []);
  }
});

test('refresh queues share concurrent reads and retain one invalidation after a failed read', async () => {
  const pending = [];
  let reads = 0;
  const queue = refresh.createRefreshQueue(async () => { reads++; await new Promise((resolve, reject) => pending.push({ resolve, reject })); });
  const first = queue.refresh();
  assert.equal(queue.refresh(), first);
  queue.refresh(true);
  queue.refresh(true);
  assert.equal(reads, 1);
  pending.shift().reject(new Error('Connection lost'));
  await tick();
  assert.equal(reads, 2);
  pending.shift().resolve();
  await first;
  assert.equal(reads, 2);
  queue.dispose();
  await queue.refresh(true);
  assert.equal(reads, 2);
});

test('browser discovery uses slow background reads, fast recovery, and no HTTP polling with a live viewer', async t => {
  const f = environment();
  let next = browserStatus();
  const reads = [];
  const { useBrowserSession } = load('../src/lib/useBrowserSession.ts', {
    '@/lib/api': { apiFetch: async (...args) => { reads.push(args); return structuredClone(next); } },
    '@/lib/eventConstants': events, '@/lib/workspaceRefresh': refresh,
  }, f.globals);
  const { value, dispose } = mount(t, () => {
    const [active, setActive] = solid.createSignal(false);
    const [visible, setVisible] = solid.createSignal(true);
    return { setActive, setVisible, session: useBrowserSession({ threadId: () => 'one', active, visible,
      busy: () => false, onOpen() {}, onHandoffChange() {} }) };
  });
  await tick();
  assert.equal(reads.length, 1);
  await f.advance(119_999);
  assert.equal(reads.length, 1);
  next = browserStatus('first');
  await f.advance(1);
  assert.equal(reads.length, 2);
  value.setActive(true);
  await f.advance(300);
  const beforeRecovery = reads.length;
  await f.advance(5000);
  assert.equal(reads.length, beforeRecovery + 1);
  value.session.setConnected(true);
  await f.advance(120_000);
  assert.equal(reads.length, beforeRecovery + 1);
  value.setVisible(false);
  assert.equal(f.timers.size, 0);
  dispose();
  assert.equal(reads[0][1].signal.aborted, true);
});

test('browser handoffs work without a mounted viewer and coalesce scoped events', async t => {
  const f = environment();
  let next = browserStatus();
  let reads = 0, opened = 0;
  const handoffs = [];
  const { useBrowserSession } = load('../src/lib/useBrowserSession.ts', {
    '@/lib/api': { apiFetch: async () => { reads++; return structuredClone(next); } },
    '@/lib/eventConstants': events, '@/lib/workspaceRefresh': refresh,
  }, f.globals);
  const { dispose } = mount(t, () => useBrowserSession({ threadId: () => 'one', active: () => false,
    visible: () => false, busy: () => true, onOpen() { opened++; }, onHandoffChange(pending) { handoffs.push(pending); } }));
  await tick();
  f.window.emit(events.CUSTOM_DOM_EVENTS.BROWSER_SESSION_CHANGED, { thread_id: 'other' });
  await f.advance(300);
  assert.equal(reads, 1);
  next = browserStatus('first', { handoff_id: 'handoff', reason: 'Login' });
  f.window.emit(events.CUSTOM_DOM_EVENTS.BROWSER_SESSION_CHANGED, { thread_id: 'one' });
  f.window.emit('k41-browser-handoff', { threadId: 'one' });
  await f.advance(300);
  assert.equal(reads, 2);
  assert.equal(opened, 1);
  assert.deepEqual(handoffs, [true]);
  f.window.emit(events.CUSTOM_DOM_EVENTS.SESSIONS_RECONNECTED);
  await f.advance(300);
  assert.equal(reads, 3);
  assert.equal(opened, 1);
  f.hide(true);
  f.window.emit(events.CUSTOM_DOM_EVENTS.BROWSER_SESSION_CHANGED, { thread_id: 'one' });
  await f.advance(120_000);
  assert.equal(reads, 3);
  assert.equal(f.timers.size, 0);
  f.hide(false);
  await f.advance(300);
  assert.equal(reads, 4);
  dispose();
  assert.equal(f.timers.size, 0);
  assert.equal([...f.window.listeners.values()].flatMap(handlers => [...handlers]).length, 0);
});

test('a delayed browser read cannot replace a different conversation or newly streamed state', async t => {
  const f = environment();
  const pending = [];
  const { useBrowserSession } = load('../src/lib/useBrowserSession.ts', {
    '@/lib/api': { apiFetch: (path, { signal }) => new Promise(resolve => pending.push({ path, signal, resolve })) },
    '@/lib/eventConstants': events, '@/lib/workspaceRefresh': refresh,
  }, f.globals);
  const { value } = mount(t, () => {
    const [threadId, setThread] = solid.createSignal('one');
    return { setThread, session: useBrowserSession({ threadId, active: () => false, visible: () => false,
      busy: () => false, onOpen() {}, onHandoffChange() {} }) };
  });
  value.setThread('two');
  assert.equal(pending[0].signal.aborted, true);
  pending[0].resolve(browserStatus('old'));
  await tick();
  assert.equal(value.session.status(), null);
  value.session.applyStatus(browserStatus('streamed', { handoff_id: 'new', reason: 'Login' }), 'two');
  pending[1].resolve(browserStatus());
  await tick();
  assert.equal(value.session.status().generation, 'streamed');
});

test('computer discovery only reads status and pauses with the workspace', async t => {
  const f = environment();
  const reads = [];
  let failed = false;
  const { useComputerSession } = load('../src/lib/useComputerSession.ts', {
    '@/lib/computers': { computerApi: { status: async (...args) => {
      reads.push(args);
      if (failed) throw new Error('Machine is stopped');
      return { running: true };
    } } },
    '@/lib/eventConstants': events, '@/lib/workspaceRefresh': refresh,
  }, f.globals);
  const { value, dispose } = mount(t, () => {
    const [visible, setVisible] = solid.createSignal(true);
    return { setVisible, session: useComputerSession(() => 'instabox', () => 'machine', visible) };
  });
  await tick();
  assert.equal(reads.length, 1);
  f.window.emit(events.CUSTOM_DOM_EVENTS.COMPUTER_SESSION_CHANGED, { backend: 'instabox', locator: 'other' });
  await f.advance(300);
  assert.equal(reads.length, 1);
  f.window.emit(events.CUSTOM_DOM_EVENTS.COMPUTER_SESSION_CHANGED, { backend: 'instabox', locator: 'machine' });
  f.window.emit(events.CUSTOM_DOM_EVENTS.COMPUTER_SESSION_CHANGED, { backend: 'instabox', locator: 'machine' });
  await f.advance(300);
  assert.equal(reads.length, 2);
  value.setVisible(false);
  assert.equal(f.timers.size, 0);
  await f.advance(120_000);
  assert.equal(reads.length, 2);
  value.setVisible(true);
  await tick();
  assert.equal(reads.length, 3);
  await f.advance(120_000);
  assert.equal(reads.length, 4);
  failed = true;
  f.window.emit(events.CUSTOM_DOM_EVENTS.COMPUTER_SESSION_CHANGED, { backend: 'instabox', locator: 'machine', reset: true });
  assert.equal(value.session.state().running, false, 'invalidated sessions end the previous running episode even when status is unavailable');
  await f.advance(300);
  assert.equal(value.session.state().running, false);
  failed = false;
  f.window.emit(events.CUSTOM_DOM_EVENTS.COMPUTER_SESSION_CHANGED, { backend: 'instabox', locator: 'machine' });
  await f.advance(300);
  assert.equal(value.session.state().running, true);
  dispose();
  assert.equal(reads[0][2].aborted, true);
  assert.equal(f.timers.size, 0);
});
