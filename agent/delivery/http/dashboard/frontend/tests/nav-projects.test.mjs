import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compileFunction } from 'node:vm';
import test from 'node:test';
import * as solid from 'solid-js';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/navProjectsStore.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const dependencies = {
  'solid-js': solid,
  '@/lib/api': { apiFetch: () => { throw new Error('Unexpected default API request'); } },
  '@/lib/chatThreads': { threadWorkspaceKey: thread => thread.workspace_key || thread.workspace?.scope.key || 'no-workspace' },
  '@/lib/endpoints': { API_PATHS: { navProjects: '/dashboard-api/nav-projects', navProjectThreads: '/dashboard-api/nav-projects/threads' } },
  '@/lib/uiConstants': { HISTORY_PAGE_SIZE: 20, NAV_PROJECT_THREAD_LIMIT: 4 },
  '@/lib/workspace': { isTempWorkspace: workspace => workspace?.metadata?.temp === true || workspace?.metadata?.source?.trim().toLowerCase() === 'temp' },
};
const exports = {};
compileFunction(outputText, ['exports', 'require', 'URLSearchParams'])(exports, name => {
  assert.ok(name in dependencies, `Unexpected dependency: ${name}`);
  return dependencies[name];
}, URLSearchParams);
const { createNavProjectsStore, navProjectThreads, newProjectWorkspace } = exports;
const tick = () => new Promise(resolve => setImmediate(resolve));
const project = key => ({ key, label: key, threads: [{ thread_id: `${key}-thread`, workspace_key: key }] });
const payload = (keys, hasMore = false) => ({ projects: keys.map(project), has_more: hasMore, next_cursor: hasMore ? keys.at(-1) : null });

test('a project shows at most four chats including the active and running rows', () => {
  const rows = Array.from({ length: 8 }, (_, index) => ({ thread_id: `thread-${index}` }));
  assert.deepEqual(navProjectThreads(rows, '', new Set()), rows.slice(0, 4));
  assert.deepEqual(navProjectThreads(rows, 'thread-7', new Set(['thread-5'])), [rows[0], rows[1], rows[5], rows[7]]);
  assert.equal(navProjectThreads(rows, 'thread-7', new Set(rows.map(row => row.thread_id))).length, 4);
});

test('empty catalog projects keep their representative workspace without inventing threads', async () => {
  const execution = { backend: 'local', locator: '/empty', metadata: {} };
  const empty = { key: 'local:/empty', id: 7, label: 'Empty project', is_pinned: true,
    workspace: { scope: { key: 'local:/empty', kind: 'local' }, execution }, threads: [] };
  const store = createNavProjectsStore(async () => ({ projects: [empty], has_more: false, next_cursor: null }));
  await store.load(true);
  assert.equal(store.threads().length, 0);
  assert.equal(store.groups().length, 1);
  assert.equal(store.groups()[0].id, 7);
  assert.equal(newProjectWorkspace(store.groups()[0]), execution);
  store.updateThreads([]);
  assert.equal(store.groups()[0].label, 'Empty project');
});

test('compound cursors preserve the loaded boundary across pinned and unpinned pages', async () => {
  const groups = [
    { ...project('z-pinned'), is_pinned: true },
    ...Array.from({ length: 22 }, (_, index) => ({ ...project(`a-${String(index).padStart(2, '0')}`), is_pinned: false })),
  ];
  const store = createNavProjectsStore(async url => {
    const after = new URL(url, 'http://localhost').searchParams.get('after');
    const start = after ? groups.findIndex(group => JSON.stringify([group.is_pinned, group.key]) === after) + 1 : 0;
    const page = groups.slice(start, start + 20);
    const hasMore = start + page.length < groups.length;
    return { projects: page, has_more: hasMore,
      next_cursor: hasMore ? JSON.stringify([page.at(-1).is_pinned, page.at(-1).key]) : null };
  });
  await store.load(true);
  await store.load();
  await store.load(true);
  assert.equal(store.groups().length, 23);
  assert.equal(store.groups()[0].key, 'z-pinned');
  assert.equal(store.groups().at(-1).key, 'a-21');
});

test('catalog changes discard stale pages and never recreate a removed group from thread updates', async () => {
  const requests = [];
  const store = createNavProjectsStore(url => new Promise(resolve => requests.push({ url, resolve })));
  const first = store.load(true);
  requests[0].resolve(payload(['old'], true));
  await first;
  const page = store.load();
  store.refreshProjects();
  requests[1].resolve(payload(['stale']));
  await tick();
  requests[2].resolve(payload(['kept']));
  await page;
  assert.deepEqual(store.groups().map(group => group.key), ['kept']);
  store.updateThreads([...store.threads(), ...project('old').threads]);
  assert.deepEqual(store.groups().map(group => group.key), ['kept']);
});

test('removal while inline conversations load discards the response and releases the request', async () => {
  let removed = false;
  let resolvePage;
  const store = createNavProjectsStore(async () => payload(removed ? [] : ['old']),
    () => new Promise(resolve => { resolvePage = resolve; }));
  await store.load(true);
  const pending = store.loadProjectThreads('old');
  removed = true;
  await store.refreshProjects();
  resolvePage({ threads: project('old').threads, has_more: false, next_offset: 1 });
  await pending;
  assert.deepEqual(store.groups(), []);
  assert.deepEqual(store.threads(), []);
  assert.equal(store.projectState('old'), undefined);
});

test('new temporary chats omit the previous execution while real projects keep their workspace', () => {
  const execution = { backend: 'local', locator: '/scratch/old', metadata: { temp: true } };
  assert.equal(newProjectWorkspace({ ...project('temp'), threads: [{ workspace: { execution } }] }), null);
  assert.equal(newProjectWorkspace({ ...project('local:/scratch/old'), threads: [{ workspace: { execution } }] }), null);
  const real = { ...execution, metadata: {} };
  assert.equal(newProjectWorkspace({ ...project('real'), threads: [{ workspace: { execution: real } }] }), real);
  assert.equal(newProjectWorkspace(project('no-workspace')), null);
});

test('changes during a pending request coalesce into a refresh and reject the stale response', async () => {
  const requests = [];
  const store = createNavProjectsStore(url => new Promise(resolve => requests.push({ url, resolve })));
  const pending = store.load(true);
  store.updateThreads(project('new').threads);
  assert.equal(store.load(true), pending);
  assert.equal(store.load(true), pending);
  assert.equal(requests.length, 1);
  requests[0].resolve(payload(['stale']));
  await tick();
  assert.equal(requests.length, 2);
  assert.equal(store.threads()[0].workspace_key, 'new');
  requests[1].resolve(payload(['new']));
  await pending;
  assert.equal(store.threads()[0].workspace_key, 'new');
  assert.equal(store.loaded(), true);
  assert.equal(store.loading(), false);
});

test('refreshes preserve the loaded project range and use key cursors without offsets', async () => {
  const keys = Array.from({ length: 22 }, (_, index) => `project-${String(index).padStart(2, '0')}`);
  const calls = [];
  const store = createNavProjectsStore(async url => {
    const params = new URL(url, 'http://localhost').searchParams;
    calls.push(params);
    assert.equal(params.has('offset'), false);
    return params.has('after') ? payload(keys.slice(20)) : payload(keys.slice(0, 20), true);
  });
  await store.load(true);
  assert.equal(store.threads().length, 20);
  await store.load();
  assert.equal(calls[1].get('after'), keys[19]);
  assert.equal(store.threads().length, 22);
  await store.load(true);
  assert.equal(calls.length, 4);
  assert.equal(store.threads().length, 22);
  assert.equal(store.hasMore(), false);
});

test('a failed refresh retains visible projects and a retry clears the error', async () => {
  let fail = false;
  const store = createNavProjectsStore(async () => {
    if (fail) throw new Error('Database unavailable');
    return payload(['saved']);
  });
  await store.load(true);
  fail = true;
  await store.load(true);
  assert.equal(store.threads()[0].workspace_key, 'saved');
  assert.equal(store.error(), 'Database unavailable');
  fail = false;
  await store.load(true);
  assert.equal(store.error(), '');
});

test('inserting earlier projects does not hide the previously loaded boundary on refresh', async () => {
  let keys = Array.from({ length: 21 }, (_, index) => `project-${String(index).padStart(2, '0')}`);
  const store = createNavProjectsStore(async url => {
    const after = new URL(url, 'http://localhost').searchParams.get('after');
    const remaining = keys.filter(key => !after || key > after);
    return payload(remaining.slice(0, 20), remaining.length > 20);
  });
  await store.load(true);
  await store.load();
  keys = [...Array.from({ length: 20 }, (_, index) => `earlier-${index}`), ...keys].sort();
  await store.load(true);
  assert.equal(store.threads().length, 41);
  assert.equal(store.threads().at(-1).workspace_key, 'project-20');
});

test('switching the active thread during a request fetches the new selection before committing', async () => {
  const requests = [];
  const store = createNavProjectsStore(url => new Promise(resolve => requests.push({ url, resolve })));
  store.setActiveThreadId('first');
  const pending = store.load(true);
  store.setActiveThreadId('second');
  requests[0].resolve(payload(['stale']));
  await tick();
  assert.equal(new URL(requests[1].url, 'http://localhost').searchParams.get('active_thread_id'), 'second');
  assert.equal(store.threads().length, 0);
  requests[1].resolve(payload(['current']));
  await pending;
  assert.equal(store.threads()[0].workspace_key, 'current');
});

test('refresh boundaries follow database ordering for Unicode project keys', async () => {
  const keys = [...Array.from({ length: 19 }, (_, index) => `project-${String(index).padStart(2, '0')}`), 'project-\ufffd', 'project-\u{1F680}'];
  const store = createNavProjectsStore(async url => {
    const after = new URL(url, 'http://localhost').searchParams.get('after');
    const start = after ? keys.indexOf(after) + 1 : 0;
    return payload(keys.slice(start, start + 20), keys.length - start > 20);
  });
  await store.load(true);
  await store.load();
  await store.load(true);
  assert.equal(store.threads().length, 21);
  assert.equal(store.threads().at(-1).workspace_key, keys.at(-1));
});

test('optimistic updates and pagination cannot duplicate thread IDs', async () => {
  let page = 0;
  const store = createNavProjectsStore(async () => page++ === 0 ? payload(['one'], true) : payload(['two']));
  await store.load(true);
  store.updateThreads([...store.threads(), ...project('two').threads, ...project('two').threads]);
  await store.load();
  assert.deepEqual(store.threads().map(row => row.thread_id), ['one-thread', 'two-thread']);
});

test('project conversations load inline across pages and remain expanded after refresh', async () => {
  const key = 'local:C:/work & notes';
  const rows = Array.from({ length: 25 }, (_, index) => ({ thread_id: `chat-${index}`, workspace_key: key }));
  const requests = [];
  const store = createNavProjectsStore(async () => ({
    projects: [{ key, label: key, threads: [...rows.slice(0, 3), rows[24]] }, project('other')],
    has_more: false, next_cursor: null,
  }), async url => {
    const params = new URL(url, 'http://localhost').searchParams;
    assert.equal(params.get('project_key'), key);
    const offset = Number(params.get('offset'));
    const limit = Number(params.get('limit'));
    requests.push({ offset, limit });
    const threads = rows.slice(offset, offset + limit);
    return { threads, has_more: offset + threads.length < rows.length, next_offset: offset + threads.length };
  });
  await store.load(true);
  await store.loadProjectThreads(key);
  assert.equal(store.projectState(key).hasMore, true);
  assert.equal(store.threads().filter(row => row.workspace_key === key).length, 21, 'older active preview remains visible');
  await store.loadProjectThreads(key);
  assert.deepEqual(store.threads().filter(row => row.workspace_key === key), rows);
  assert.equal(store.projectState(key).hasMore, false);
  assert.equal(store.threads().at(-1).workspace_key, 'other');
  assert.deepEqual(requests, [{ offset: 0, limit: 20 }, { offset: 20, limit: 20 }]);
  await store.loadProjectThreads(key);
  assert.equal(requests.length, 2, 'exhausted projects do not fetch again');
  rows[5] = { ...rows[5], title: 'Updated conversation' };
  await store.load(true);
  assert.equal(store.threads().find(row => row.thread_id === 'chat-5').title, 'Updated conversation');
  assert.equal(store.projectState(key).expanded, true);
  assert.equal(store.threads().filter(row => row.workspace_key === key).length, 25);
});

test('duplicate project clicks share a request and failed pagination can retry without losing rows', async () => {
  const requests = [];
  const store = createNavProjectsStore(async () => payload(['one']), url => new Promise((resolve, reject) => requests.push({ url, resolve, reject })));
  await store.load(true);
  const pending = store.loadProjectThreads('one');
  assert.equal(store.loadProjectThreads('one'), pending);
  assert.equal(store.projectState('one').loading, true);
  requests[0].reject(new Error('Connection failed'));
  await pending;
  assert.equal(store.projectState('one').error, 'Connection failed');
  assert.equal(store.threads().length, 1);
  const retry = store.loadProjectThreads('one');
  requests[1].resolve({ threads: project('one').threads, has_more: true, next_offset: 1 });
  await retry;
  const more = store.loadProjectThreads('one');
  requests[2].reject(new Error('Connection failed again'));
  await more;
  assert.equal(store.projectState('one').expanded, true);
  assert.equal(store.threads().length, 1);
  assert.equal(store.projectState('one').nextOffset, 1);
  assert.equal(store.projectState('one').loading, false);
});

test('changes during inline loading discard stale conversation rows', async () => {
  const requests = [];
  const store = createNavProjectsStore(async () => payload(['one']), url => new Promise(resolve => requests.push({ url, resolve })));
  await store.load(true);
  const pending = store.loadProjectThreads('one');
  store.updateThreads([{ ...store.threads()[0], title: 'Renamed' }]);
  requests[0].resolve({ threads: [{ ...project('one').threads[0], title: 'Old title' }], has_more: false, next_offset: 1 });
  await tick();
  assert.equal(requests.length, 2);
  assert.equal(store.threads()[0].title, 'Renamed');
  requests[1].resolve({ threads: [{ ...project('one').threads[0], title: 'Renamed' }], has_more: false, next_offset: 1 });
  await pending;
  assert.equal(store.threads()[0].title, 'Renamed');
});

test('a concurrent refresh cannot leave a failed project page stuck loading', async () => {
  const requests = [];
  let first = true;
  const firstPage = { threads: project('one').threads, has_more: true, next_offset: 1 };
  const store = createNavProjectsStore(async () => payload(['one']), () => {
    if (first) {
      first = false;
      return Promise.resolve(firstPage);
    }
    return new Promise((resolve, reject) => requests.push({ resolve, reject }));
  });
  await store.load(true);
  await store.loadProjectThreads('one');
  const more = store.loadProjectThreads('one');
  const refresh = store.load(true);
  await tick();
  requests[0].resolve({ threads: [], has_more: false, next_offset: 1 });
  await tick();
  requests[2].reject(new Error('Connection failed'));
  await more;
  requests[1].resolve(firstPage);
  await refresh;
  assert.equal(store.projectState('one').loading, false);
  assert.equal(store.projectState('one').error, 'Connection failed');
  assert.equal(store.projectState('one').hasMore, true);
});
