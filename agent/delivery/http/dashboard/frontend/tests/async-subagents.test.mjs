import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compileFunction } from 'node:vm';
import test from 'node:test';
import * as solid from 'solid-js';
import * as solidStore from 'solid-js/store';
import ts from 'typescript';

function load(path, dependencies = {}, globals = {}) {
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  compileFunction(outputText, ['exports', 'require', ...Object.keys(globals)])(exports, (name) => {
    assert.ok(name in dependencies, name);
    return dependencies[name];
  }, ...Object.values(globals));
  return exports;
}

const input = load('../src/lib/userInputRequest.ts');
const asyncHelpers = load('../src/lib/asyncSubagents.ts', {
  '@/lib/userInputRequest': input,
});
const { mergeAsyncTask, asyncInputRequest, asyncTaskPath, missingAsyncTasks } = asyncHelpers;

test('an old SSE event or snapshot cannot replace a newer task state', () => {
  const completed = { version: 5, status: 'completed', result: 'Done' };
  assert.equal(mergeAsyncTask(completed, { version: 4, status: 'running' }), completed);
  assert.equal(mergeAsyncTask(completed, { version: 6, status: 'pending' }).status, 'pending');
});

test('a snapshot refresh preserves a pending question and its in-progress answer', () => {
  const interrupts = [{ id: 'question-1', value: { type: 'user_input_request' } }];
  const current = { version: 2, status: 'waiting', interrupts };
  const refreshed = mergeAsyncTask(current, { version: 3, interrupts: JSON.parse(JSON.stringify(interrupts)) });
  assert.equal(refreshed.interrupts, interrupts);
  assert.notEqual(mergeAsyncTask(current, { version: 3, interrupts: [{ id: 'question-2' }] }).interrupts, interrupts);
});

test('permission cards preserve interrupt and request identities and require a decision', () => {
  const request = asyncInputRequest({ id: 'interrupt-1', value: {
    type: 'permission_request', tool_call_id: 'tool-1', request_id: 'request-1', action: 'write', resource: '/workspace/report',
  } });
  assert.equal(request.interrupt_id, 'interrupt-1');
  assert.equal(request.questions[0].id, 'permission:request-1');
  assert.equal(request.questions[0].required, true);
  assert.equal(request.questions[0].free_text.enabled, false);
  assert.deepEqual(Array.from(request.questions[0].options, (option) => option.id), ['allow_once', 'allow_thread', 'deny']);
});

test('task management URL encodes both parent and task identities', () => {
  assert.equal(asyncTaskPath('api:parent/a', 'task/b'), '/v2/api/chat/api%3Aparent%2Fa/async-tasks/task%2Fb');
});

test('a background launch stays visible after the parent finishes its turn', () => {
  const { isTranscriptActivityBoundary } = load('../src/lib/transcriptActivity.ts', {
    '@/lib/htmlPreview': { HTML_PREVIEW_TOOL_NAME: 'preview_html' },
    '@/lib/toolCallStatus': load('../src/lib/toolCallStatus.ts'),
  });
  assert.equal(isTranscriptActivityBoundary({ type: 'tool', name: 'start_async_task', resultReceived: true }), true);
});

const task = (id, patch = {}) => ({ task_id: id, delegation_id: id, parent_thread_id: 'parent',
  root_thread_id: 'parent', thread_id: `parent:sub:${id}`, execution_mode: 'async',
  agent_name: 'worker', tool_call_id: `launch-${id}`, status: 'waiting', version: 1, ...patch });
const tick = () => new Promise(resolve => setImmediate(resolve));

function tasksFixture(t) {
  const requests = [];
  const events = new EventTarget();
  const names = { SUBAGENT_TASK_UPDATED: 'task', SESSIONS_RECONNECTED: 'reconnected' };
  const { useAsyncTasks } = load('../src/lib/useAsyncSubagent.ts', {
    'solid-js': solid, 'solid-js/store': solidStore,
    '@/lib/api': { apiFetch: (path, init) => new Promise((resolve, reject) => {
      requests.push({ path, signal: init.signal, resolve, reject });
    }) },
    '@/lib/asyncSubagents': asyncHelpers, '@/lib/chatStreamHandler': {}, '@/lib/protocolStreamHandler': {},
    '@/lib/eventConstants': { CUSTOM_DOM_EVENTS: names },
  }, { window: events });
  let setThread;
  const state = solid.createRoot(dispose => {
    t.after(dispose);
    const [thread, update] = solid.createSignal('parent');
    setThread = update;
    return useAsyncTasks(thread);
  });
  return { state, requests, setThread, reconnect: () => events.dispatchEvent(new Event(names.SESSIONS_RECONNECTED)) };
}

test('compacted or paginated launches recover controls without duplicating existing cards', () => {
  const active = [task('older'), task('visible')];
  const items = [{ type: 'tool', subagent: active[1] }];
  assert.deepEqual(missingAsyncTasks(active, items).map(item => item.task_id), ['older']);
  assert.deepEqual(missingAsyncTasks(active, []).map(item => item.task_id), ['older', 'visible']);
  assert.equal(missingAsyncTasks([active[0]], [{ type: 'tool', subagent: { ...active[0], read_only: true } }]).length, 1);
});

test('durable active task recovery loads all pages rather than only the newest tasks', async t => {
  const f = tasksFixture(t);
  f.requests[0].resolve({ tasks: Array.from({ length: 100 }, (_, index) => task(String(index))), total: 101, active_count: 101 });
  await tick();
  assert.match(f.requests[1].path, /status=active&limit=100&offset=100$/);
  f.requests[1].resolve({ tasks: [task('last')], total: 101, active_count: 101 });
  await tick();
  assert.equal(f.state.count(), 101);
  assert.equal(f.state.tasks().length, 101);
  assert.equal(f.state.tasks().at(-1).task_id, 'last');
});

test('refreshing recovered tasks preserves card identity and the pending answer', async t => {
  const f = tasksFixture(t);
  const interrupts = [{ id: 'approval', value: { type: 'permission_request' } }];
  f.requests[0].resolve({ tasks: [task('waiting', { interrupts })], total: 1, active_count: 1 });
  await tick();
  const original = f.state.tasks()[0];
  const originalInterrupt = original.interrupts[0];
  f.reconnect();
  f.requests[1].resolve({ tasks: [task('waiting', { version: 2, interrupts: JSON.parse(JSON.stringify(interrupts)) })], total: 1, active_count: 1 });
  await tick();
  assert.equal(f.state.tasks()[0], original);
  assert.equal(f.state.tasks()[0].interrupts[0], originalInterrupt);
  assert.equal(original.version, 2);
});

test('changing threads aborts recovery and ignores late responses from the old thread', async t => {
  const f = tasksFixture(t);
  f.setThread('other');
  assert.equal(f.requests[0].signal.aborted, true);
  f.requests[0].resolve({ tasks: [task('stale')], total: 1, active_count: 1 });
  await tick();
  assert.equal(f.state.tasks().length, 0);
  f.requests[1].resolve({ tasks: [task('current', { parent_thread_id: 'other' })], total: 1, active_count: 1 });
  await tick();
  assert.equal(f.state.tasks()[0].task_id, 'current');
});
