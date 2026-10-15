import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compileFunction, runInNewContext } from 'node:vm';
import test from 'node:test';
import * as solid from 'solid-js';
import * as store from 'solid-js/store';
import ts from 'typescript';

function loadModule(path, dependencies = {}, globals = {}) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  const context = {
    exports, console, structuredClone, JSON, Error, ...globals,
    require: name => {
      if (name === 'solid-js') return solid;
      if (name === 'solid-js/store') return store;
      assert.ok(name in dependencies, `Unexpected dependency: ${name}`);
      return dependencies[name];
    },
  };
  // Keep plain object prototypes in Solid's realm while isolating module bindings.
  compileFunction(outputText, Object.keys(context))(...Object.values(context));
  return exports;
}

function mount(t, callback) {
  let dispose;
  const value = solid.createRoot(cleanup => {
    dispose = cleanup;
    return callback();
  });
  t.after(() => dispose());
  return { value, dispose };
}

const tick = () => new Promise(resolve => setImmediate(resolve));

test('stream updates keep message and tool rows mounted and track only changed fields', t => {
  const utils = loadModule('../src/lib/utils.ts');
  const { createTranscriptItems } = loadModule('../src/lib/chatStreamStore.ts', { '@/lib/utils': utils });
  mount(t, () => {
    const [items, setItems] = createTranscriptItems([
      { id: 1, type: 'message', role: 'assistant', text: '' },
      { id: 2, type: 'tool', name: 'read', args: {}, result: null },
    ]);
    let created = 0;
    let destroyed = 0;
    let messageUpdates = 0;
    let toolUpdates = 0;
    const rows = solid.mapArray(items, item => {
      created += 1;
      solid.onCleanup(() => destroyed += 1);
      solid.createComputed(() => {
        if (item.type === 'message') { item.text; messageUpdates += 1; }
        else { item.result; toolUpdates += 1; }
      });
      return item;
    });
    const first = rows()[0];
    for (let i = 0; i < 20; i += 1) {
      setItems(current => current.map(item => item.id === 1 ? { ...item, text: item.text + 'x' } : item));
      rows();
    }
    assert.equal(rows()[0], first);
    assert.equal(created, 2);
    assert.equal(destroyed, 0);
    assert.equal(messageUpdates, 21);
    assert.equal(toolUpdates, 1);
    setItems(current => current.map(item => item.id === 2 ? { ...item, result: 'done' } : item));
    rows();
    assert.equal(toolUpdates, 2);
    assert.equal(created, 2);
    setItems(current => current.filter(item => item.id !== 1));
    rows();
    assert.equal(destroyed, 1);
  });
});

test('replacing and reordering transcript items retains identities by item ID', t => {
  const utils = loadModule('../src/lib/utils.ts');
  const { createTranscriptItems } = loadModule('../src/lib/chatStreamStore.ts', { '@/lib/utils': utils });
  mount(t, () => {
    const [items, setItems] = createTranscriptItems([{ id: 1, text: 'a' }, { id: 2, text: 'b' }]);
    const first = items()[0];
    const second = items()[1];
    setItems([{ id: 2, text: 'updated' }, { id: 1, text: 'a' }]);
    assert.equal(items()[0], second);
    assert.equal(items()[1], first);
    assert.equal(items()[0].text, 'updated');
    setItems([]);
    assert.equal(items().length, 0);
  });
});

test('persisted transcripts do not share mutable data with the previous local store', t => {
  const utils = loadModule('../src/lib/utils.ts');
  const { createTranscriptItems } = loadModule('../src/lib/chatStreamStore.ts', { '@/lib/utils': utils });
  mount(t, () => {
    const [local, setLocal] = createTranscriptItems([{ id: 1, type: 'message', text: 'initial', attachments: [{ name: 'original' }] }]);
    const [persisted, setPersisted] = createTranscriptItems(local());
    setPersisted(current => current.map(item => ({ ...item, text: 'streaming', attachments: [{ name: 'updated' }] })));
    assert.equal(local()[0].text, 'initial');
    assert.equal(local()[0].attachments[0].name, 'original');
    setLocal([]);
    assert.equal(persisted().length, 1);
    assert.equal(persisted()[0].text, 'streaming');
  });
});

test('message chunks update only their rows and remain correct after snapshot reordering and removal', t => {
  const utils = loadModule('../src/lib/utils.ts');
  const { createTranscriptItems } = loadModule('../src/lib/chatStreamStore.ts', { '@/lib/utils': utils });
  mount(t, () => {
    const initial = [
      { id: 1, type: 'message', role: 'assistant', text: 'first' },
      { id: 2, type: 'message', role: 'assistant', text: 'second' },
      { id: 3, type: 'tool', name: 'read', args: {}, result: null },
    ];
    const [items, setItems, appendChunks] = createTranscriptItems(initial);
    const first = items()[0];
    const second = items()[1];
    let mounted = 0;
    let destroyed = 0;
    let observed = 0;
    let toolUpdates = 0;
    const rows = solid.mapArray(items, item => {
      mounted += 1;
      solid.onCleanup(() => destroyed += 1);
      return item;
    });
    solid.createComputed(() => { first.text; second.text; observed += 1; });
    solid.createComputed(() => { items()[2]?.result; toolUpdates += 1; });
    rows();
    appendChunks(new Map([[1, '-a'], [2, '-b'], [3, 'ignored'], [99, 'missing']]));
    assert.equal(first.text, 'first-a');
    assert.equal(second.text, 'second-b');
    assert.equal(observed, 2, 'multiple chunks should notify shared consumers once');
    assert.equal(toolUpdates, 1);
    assert.equal(initial[0].text, 'first', 'streaming must not mutate the source snapshot');
    assert.equal(mounted, 3);

    setItems([initial[2], { ...initial[1], text: 'replaced' }, { ...initial[0], text: 'reset' }]);
    rows();
    appendChunks(new Map([[1, '-after-reorder'], [2, '-after-reorder']]));
    assert.equal(items()[1], second);
    assert.equal(items()[2], first);
    assert.equal(first.text, 'reset-after-reorder');
    assert.equal(second.text, 'replaced-after-reorder');
    assert.equal(mounted, 3);

    setItems(current => current.filter(item => item.id !== 1));
    rows();
    appendChunks(new Map([[1, 'removed'], [2, '-after-removal']]));
    assert.equal(items().length, 2);
    assert.equal(second.text, 'replaced-after-reorder-after-removal');
    assert.equal(destroyed, 1);
    setItems([]);
    appendChunks(new Map([[2, 'stale']]));
    assert.equal(items().length, 0);
  });
});

test('buffered chunks stay isolated by thread and authoritative replacements discard pending appends', t => {
  const utils = loadModule('../src/lib/utils.ts');
  const streams = loadModule('../src/lib/chatStreamStore.ts', { '@/lib/utils': utils });
  const timers = new Map();
  let nextTimer = 0;
  const { useChatStreams } = loadModule('../src/lib/useChatStreams.ts', {
    '@/components/Transcript': {},
    '@/lib/chatStreamStore': streams,
    '@/lib/generatedImages': {},
  }, {
    window: {
      setTimeout: callback => { const id = ++nextTimer; timers.set(id, callback); return id; },
      clearTimeout: id => timers.delete(id),
    },
  });
  mount(t, () => {
    let scrolls = 0;
    let unmounting = false;
    const chat = useChatStreams({
      scroll: { scrollToBottom: () => scrolls += 1 },
      getCurrentThreadId: () => 'first',
      getIsUnmounting: () => unmounting,
    });
    const localId = chat.appendItem({ type: 'message', role: 'assistant', text: '' }, 'none');
    chat.updateMessage(localId, 'buffered');
    chat.updateMessage(localId, ' text');
    assert.equal(chat.items()[0].text, '');
    assert.equal(timers.size, 1);
    chat.flushPendingMessageChunks();
    assert.equal(chat.items()[0].text, 'buffered text');
    assert.equal(timers.size, 0);

    chat.updateMessage(localId, 'discarded');
    chat.replaceMessage(localId, 'authoritative');
    chat.flushPendingMessageChunks();
    assert.equal(chat.items()[0].text, 'authoritative');
    chat.updateMessage(localId, 'removed');
    chat.removeItem(localId);
    chat.flushPendingMessageChunks();
    assert.equal(chat.items().length, 0);

    const first = streams.getOrCreateStreamSignals('first', [{ id: 10, type: 'message', text: 'A' }]);
    const second = streams.getOrCreateStreamSignals('second', [{ id: 20, type: 'message', text: 'B' }]);
    chat.setCurrentStreamThreadId('first');
    scrolls = 0;
    chat.updateMessage(10, '-one');
    chat.updateMessage(20, '-two', 'second');
    chat.flushPendingMessageChunks();
    assert.equal(first.items[0]()[0].text, 'A-one');
    assert.equal(second.items[0]()[0].text, 'B-two');
    assert.equal(scrolls, 1, 'background thread updates must not scroll the visible thread');
    unmounting = true;
    chat.updateMessage(10, '-after-navigation', 'first');
    chat.flushPendingMessageChunks();
    assert.equal(first.items[0]()[0].text, 'A-one-after-navigation');
    assert.equal(scrolls, 1);
    assert.equal(timers.size, 0);
  });
});

function loadWorkspaceGroups() {
  const threads = loadModule('../src/lib/chatThreads.ts', {
    '@/components/Transcript': {},
    '@/lib/userInputRequest': {},
    '@/lib/generatedImages': {},
    '@/lib/workspaceConstants': { NO_WORKSPACE_KEY: 'none', NO_WORKSPACE_LABEL: 'No workspace' },
  });
  return loadModule('../src/lib/useThreadWorkspaceGroups.ts', {
    '@/lib/chatThreads': threads,
    '@/lib/utils': loadModule('../src/lib/utils.ts'),
  }).useThreadWorkspaceGroups;
}

test('history refreshes retain group and thread rows while updating labels, order and titles', t => {
  const useThreadWorkspaceGroups = loadWorkspaceGroups();
  mount(t, () => {
    const initial = [
      { thread_id: 'a', title: 'A', workspace_key: 'one', workspace_label: 'One' },
      { thread_id: 'b', title: 'B', workspace_key: 'one', workspace_label: 'One' },
      { thread_id: 'c', title: 'C', workspace_key: 'two', workspace_label: 'Two' },
    ];
    const [threads, setThreads] = solid.createSignal(initial);
    const groups = useThreadWorkspaceGroups(threads);
    const firstGroup = groups()[0];
    const firstThread = firstGroup.threads[0];
    const secondThread = firstGroup.threads[1];
    let groupMounts = 0;
    let groupDisposals = 0;
    let threadMounts = 0;
    let threadDisposals = 0;
    let renderedTitle;
    const rows = solid.mapArray(groups, group => {
      groupMounts += 1;
      solid.onCleanup(() => groupDisposals += 1);
      return solid.mapArray(() => group.threads, thread => {
        threadMounts += 1;
        solid.onCleanup(() => threadDisposals += 1);
        if (thread.thread_id === 'a') solid.createComputed(() => renderedTitle = thread.title);
        return thread;
      });
    });
    solid.createComputed(() => rows().forEach(groupRows => groupRows()));
    setThreads([
      { ...initial[2] },
      { ...initial[1], workspace_label: 'Renamed workspace' },
      { ...initial[0], title: 'Renamed thread', workspace_label: 'Renamed workspace' },
    ]);
    assert.equal(groups()[1], firstGroup);
    assert.equal(firstGroup.label, 'Renamed workspace');
    assert.equal(firstGroup.threads[0], secondThread);
    assert.equal(firstGroup.threads[1], firstThread);
    assert.equal(renderedTitle, 'Renamed thread');
    assert.equal(initial[0].title, 'A');
    assert.equal(groupMounts, 2);
    assert.equal(threadMounts, 3);
    assert.equal(groupDisposals, 0);
    assert.equal(threadDisposals, 0);

    setThreads([{ ...initial[0], title: 'Still here' }]);
    assert.equal(groups()[0], firstGroup);
    assert.equal(firstGroup.threads[0], firstThread);
    assert.equal(groupDisposals, 1);
    assert.equal(threadDisposals, 2);
    setThreads([]);
    assert.equal(groups().length, 0);
    assert.equal(groupDisposals, 2);
    assert.equal(threadDisposals, 3);
  });
});

test('history groups track workspace moves and repository metadata without sharing source objects', t => {
  const useThreadWorkspaceGroups = loadWorkspaceGroups();
  mount(t, () => {
    const source = { thread_id: 'a', title: 'A', workspace_key: 'one', workspace_label: 'One', workspace: { scope: { kind: 'local' } } };
    const [threads, setThreads] = solid.createSignal([source]);
    const groups = useThreadWorkspaceGroups(threads);
    const original = groups()[0];
    setThreads([{ ...source, workspace: { scope: { kind: 'github' } } }]);
    assert.equal(groups()[0], original);
    assert.equal(original.isRepo, true);
    assert.equal(source.workspace.scope.kind, 'local');
    setThreads([{ ...source, workspace_key: 'two', workspace_label: 'Two' }]);
    assert.equal(groups()[0].key, 'two');
    assert.equal(groups()[0].threads[0].thread_id, 'a');
    setThreads([source]);
    assert.notEqual(groups()[0], original, 'removed groups must be released before being reintroduced');
  });
});

function sessionFixture() {
  const connections = [];
  const events = [];
  const timers = new Map();
  let nextTimer = 0;
  class EventSource {
    static CLOSED = 2;
    readyState = 1;
    listeners = new Map();
    closed = false;
    constructor(url) { this.url = url; connections.push(this); }
    addEventListener(name, listener) { this.listeners.set(name, listener); }
    emit(name, data) { this.listeners.get(name)?.({ data: JSON.stringify(data) }); }
    close() { this.closed = true; this.readyState = 2; }
  }
  const constants = loadModule('../src/lib/eventConstants.ts');
  const module = loadModule('../src/lib/sessionStore.ts', {
    '@/lib/endpoints': { SSE_URLS: { sessions: '/sessions' } },
    '@/lib/eventConstants': constants,
    '@/lib/uiConstants': { SSE_RECONNECT_DELAY_MS: 10 },
  }, {
    EventSource,
    CustomEvent: class { constructor(type, { detail }) { this.type = type; this.detail = detail; } },
    window: {
      dispatchEvent: event => events.push(event),
      setTimeout: fn => { const id = ++nextTimer; timers.set(id, fn); return id; },
      clearTimeout: id => timers.delete(id),
    },
  });
  return { ...module, connections, events, timers, ...constants };
}

test('session consumers share one connection and release it after the final cleanup', t => {
  const f = sessionFixture();
  const first = mount(t, () => f.useSessions());
  const second = mount(t, () => f.useSessions());
  assert.equal(f.connections.length, 1);
  const session = { session_id: 'a', thread_id: 'thread-a', agent_name: 'default' };
  f.connections[0].emit(f.SESSION_EVENTS.SESSION_STARTED, session);
  assert.equal(first.value.sessions()[0], second.value.sessions()[0]);
  assert.equal(f.events.filter(event => event.type === f.CUSTOM_DOM_EVENTS.SESSION_STARTED).length, 1);
  const row = first.value.sessions()[0];
  f.connections[0].emit(f.SESSION_EVENTS.SESSION_UPDATED, { ...session, agent_name: 'updated' });
  assert.equal(first.value.sessions()[0], row);
  assert.equal(row.agent_name, 'updated');
  first.dispose();
  assert.equal(f.connections[0].closed, false);
  second.dispose();
  assert.equal(f.connections[0].closed, true);
});

test('session reconnect timers are cancelled when consumers leave', t => {
  const f = sessionFixture();
  const consumer = mount(t, () => f.useSessions());
  f.connections[0].readyState = 2;
  f.connections[0].onerror();
  assert.equal(f.timers.size, 1);
  consumer.dispose();
  assert.equal(f.timers.size, 0);
  const next = mount(t, () => f.useSessions());
  assert.equal(f.connections.length, 2);
  f.connections[0].emit(f.SESSION_EVENTS.SNAPSHOT, { sessions: [{ session_id: 'stale' }] });
  assert.equal(next.value.sessions().length, 0);
});

test('cached home sessions cannot overwrite newer live session data', t => {
  const f = sessionFixture();
  mount(t, () => f.useSessions());
  f.connections[0].emit(f.SESSION_EVENTS.SNAPSHOT, { sessions: [] });
  const home = mount(t, () => f.useSessions([{ session_id: 'stale', thread_id: 'old' }]));
  assert.equal(home.value.sessions().length, 0);
});

test('Markdown consumers share a theme observer and clean it up together', t => {
  const observers = [];
  let isDark = false;
  const theme = loadModule('../src/lib/theme.ts', {}, {
    document: { documentElement: { classList: { contains: () => isDark } } },
    MutationObserver: class {
      disconnected = false;
      constructor(callback) { this.callback = callback; observers.push(this); }
      observe() {}
      disconnect() { this.disconnected = true; }
    },
  });
  const first = mount(t, () => theme.createDarkMode());
  const second = mount(t, () => theme.getSharedDarkMode());
  assert.equal(observers.length, 1);
  isDark = true;
  observers[0].callback();
  assert.equal(first.value(), true);
  assert.equal(second.value(), true);
  first.dispose();
  assert.equal(observers[0].disconnected, false);
  second.dispose();
  assert.equal(observers[0].disconnected, true);
});

function dataFixture() {
  const cache = new Map();
  const requests = [];
  const query = (fetcher, name) => {
    const read = path => {
      if (!cache.has(read.keyFor(path))) cache.set(read.keyFor(path), fetcher(path));
      return cache.get(read.keyFor(path));
    };
    read.keyFor = path => `${name}:${path}`;
    return read;
  };
  query.delete = key => cache.delete(key);
  const module = loadModule('../src/lib/dashboardData.ts', {
    '@solidjs/router': { query },
    '@/lib/dashboardCache': { trackDashboardCacheKey: () => {} },
    '@/lib/api': { apiFetch: path => new Promise((resolve, reject) => requests.push({ path, resolve, reject })) },
  });
  return { ...module, requests, query };
}

test('route preloading and initial resource loading share the same request', async t => {
  const f = dataFixture();
  f.preloadDashboardData('/config');
  const { value } = mount(t, () => f.useDashboardData('/config', { defer: true }));
  const loading = value.load();
  assert.equal(f.requests.length, 1);
  f.requests[0].resolve({ title: 'first' });
  await loading;
  await tick();
  assert.equal(value.data().title, 'first');
  const refresh = value.load();
  assert.equal(f.requests.length, 2);
  assert.equal(value.data().title, 'first');
  f.requests[1].resolve({ title: 'second' });
  await refresh;
  assert.equal(value.data().title, 'second');
});

test('failed resource refresh keeps existing content and allows a successful retry', async t => {
  const f = dataFixture();
  const { value } = mount(t, () => f.useDashboardData('/config'));
  f.requests[0].resolve({ title: 'first' });
  await tick();
  const refresh = value.load();
  f.requests[1].reject(new Error('offline'));
  await refresh;
  assert.equal(value.data().title, 'first');
  assert.equal(value.error(), 'offline');
  const retry = value.load();
  f.requests[2].resolve({ title: 'recovered' });
  await retry;
  assert.equal(value.error(), '');
  assert.equal(value.data().title, 'recovered');
});

test('a response from an older resource path cannot overwrite the current page', async t => {
  const f = dataFixture();
  let setPath;
  const { value } = mount(t, () => {
    const [path, update] = solid.createSignal('/first');
    setPath = update;
    return f.useDashboardData(path);
  });
  setPath('/second');
  f.requests[1].resolve({ title: 'second' });
  await tick();
  f.requests[0].reject(new Error('old failure'));
  await tick();
  assert.equal(value.data().title, 'second');
  assert.equal(value.error(), '');
});

test('resource cleanup ignores delayed failures and blocks further loads', async t => {
  const f = dataFixture();
  const { value, dispose } = mount(t, () => f.useDashboardData('/config'));
  dispose();
  f.requests[0].reject(new Error('late failure'));
  await tick();
  await value.load();
  assert.equal(value.error(), '');
  assert.equal(f.requests.length, 1);
});

test('catalog refresh performs a new request after its cache has been populated', async () => {
  const f = dataFixture();
  const catalog = loadModule('../src/lib/catalogStore.ts', {
    '@solidjs/router': { query: f.query },
    '@/lib/dashboardData': f,
  });
  const initial = catalog.fetchCatalog();
  f.requests[0].resolve({ backends: [{ name: 'local' }] });
  await initial;
  const refresh = catalog.refreshCatalog();
  await tick();
  assert.equal(f.requests.length, 2);
  assert.equal(catalog.getBackends().length, 1);
  f.requests[1].resolve({ backends: [{ name: 'local' }, { name: 'cloud' }] });
  await refresh;
  assert.equal(catalog.getBackends().length, 2);
});

test('a failed catalog request can be retried without reusing a rejected promise', async () => {
  const f = dataFixture();
  const catalog = loadModule('../src/lib/catalogStore.ts', {
    '@solidjs/router': { query: f.query },
    '@/lib/dashboardData': f,
  });
  const failed = catalog.fetchCatalog();
  f.requests[0].reject(new Error('offline'));
  await assert.rejects(failed, /offline/);
  const retry = catalog.fetchCatalog();
  assert.equal(f.requests.length, 2);
  f.requests[1].resolve({ backends: [] });
  await retry;
  assert.equal(catalog.getError(), null);
});

function loadSettingsHook(dashboard) {
  const source = readFileSync(new URL('../src/pages/settings/shared.tsx', import.meta.url), 'utf8');
  const ast = ts.createSourceFile('shared.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const hook = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name.text === 'useSettingsData');
  const { outputText } = ts.transpileModule(hook.getText(ast), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  runInNewContext(outputText, {
    exports, structuredClone, JSON, ...solid, ...store,
    useDashboardData: dashboard,
    useToast: () => ({ showToast: () => {} }),
    settingsFromPayload: data => data.settings,
    typedValue: (info, value) => value,
    sameValue: loadModule('../src/lib/utils.ts').sameJsonValue,
    cloneValue: loadModule('../src/lib/utils.ts').cloneValue,
    sameJsonValue: loadModule('../src/lib/utils.ts').sameJsonValue,
  });
  return exports.useSettingsData;
}

test('settings payloads initialize drafts before reactive consumers observe them', async t => {
  const f = dataFixture();
  const useSettingsData = loadSettingsHook(f.useDashboardData);
  const snapshots = [];
  const { value } = mount(t, () => {
    const settings = useSettingsData('/config');
    solid.createComputed(() => {
      const payload = settings.data();
      if (payload) snapshots.push({
        first: settings.drafts().first,
        second: settings.drafts().second,
        pending: settings.pendingChanges().length,
      });
    });
    return settings;
  });
  const loading = value.load();
  f.requests[0].resolve({ settings: { first: { value: 'one' }, second: { value: 'two' } } });
  await loading;
  await tick();
  assert.ok(snapshots.length > 0);
  for (const snapshot of snapshots) {
    assert.deepEqual(snapshot, { first: 'one', second: 'two', pending: 0 });
  }
});

test('settings refresh updates clean drafts while preserving edits and removing obsolete keys', async t => {
  const f = dataFixture();
  const useSettingsData = loadSettingsHook(f.useDashboardData);
  const { value } = mount(t, () => useSettingsData('/config'));
  const loading = value.load();
  f.requests[0].resolve({ settings: {
    clean: { value: 'old' }, edited: { value: 'original' }, removed: { value: 'obsolete' },
  } });
  await loading;
  await tick();
  value.setDraft('edited', 'local edit');
  const refresh = value.load();
  f.requests[1].resolve({ settings: {
    clean: { value: 'new' }, edited: { value: 'server edit' }, added: { value: 'added' },
  } });
  await refresh;
  await tick();
  assert.equal(value.drafts().clean, 'new');
  assert.equal(value.drafts().edited, 'local edit');
  assert.equal(value.drafts().added, 'added');
  assert.equal(value.drafts().removed, undefined);
  assert.deepEqual(Array.from(value.pendingChanges(), change => change.key), ['edited']);
});

test('nested setting drafts preserve the server baseline, remove old keys, and restore correctly', t => {
  const serverValue = { old: true, model: { effort: 'low' } };
  const payload = { settings: { profile: { value: serverValue } } };
  const useSettingsData = loadSettingsHook(() => ({ data: () => payload, error: () => '', load: async () => {} }));
  const { value } = mount(t, () => useSettingsData('/config'));
  value.setDraft('profile', { model: { effort: 'high' } });
  assert.deepEqual(serverValue, { old: true, model: { effort: 'low' } });
  assert.equal(value.drafts().profile.old, undefined);
  assert.equal(value.pendingChanges().length, 1);
  value.restoreDraft('profile');
  assert.equal(value.drafts().profile.model.effort, 'low');
  assert.equal(value.pendingChanges().length, 0);
  value.setDraft('profile', { model: { effort: 'max' } });
  value.discardAll();
  assert.equal(value.drafts().profile.model.effort, 'low');
});

test('writes invalidate dashboard queries without clearing unrelated router caches', () => {
  const keys = new Set(['dashboard-config', 'dashboard-agents', 'unrelated']);
  const cache = loadModule('../src/lib/dashboardCache.ts', {
    '@solidjs/router': { query: { delete: key => keys.delete(key) } },
  });
  cache.trackDashboardCacheKey('dashboard-config');
  cache.trackDashboardCacheKey('dashboard-agents');
  cache.invalidateDashboardCache();
  assert.deepEqual([...keys], ['unrelated']);
  cache.invalidateDashboardCache();
  assert.deepEqual([...keys], ['unrelated']);
});
