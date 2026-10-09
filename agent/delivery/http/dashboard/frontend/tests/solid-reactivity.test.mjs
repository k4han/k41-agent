import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
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
  runInNewContext(outputText, {
    exports, console, structuredClone, JSON, Error, ...globals,
    require: name => {
      if (name === 'solid-js') return solid;
      if (name === 'solid-js/store') return store;
      assert.ok(name in dependencies, `Unexpected dependency: ${name}`);
      return dependencies[name];
    },
  });
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
