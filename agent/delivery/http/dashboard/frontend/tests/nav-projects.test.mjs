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
  '@/lib/endpoints': { API_PATHS: { navProjects: '/dashboard-api/nav-projects' } },
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
