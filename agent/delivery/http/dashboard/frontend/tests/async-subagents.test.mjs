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
test('an ask-user timeout settles the request without a human answer', () => {
  const result = input.parseAskUserToolResult({ type: 'ask_user_timeout', content: 'Continue with the default.' });
  assert.equal(result.valid, true);
  assert.equal(result.timedOut, true);
  assert.deepEqual(Array.from(result.answers), []);
});
const asyncHelpers = load('../src/lib/asyncSubagents.ts', {
  '@/lib/userInputRequest': input,
});
const { mergeAsyncTask, asyncInputRequest, asyncTaskPath, missingAsyncTasks } = asyncHelpers;
const streamHelpers = load('../src/lib/chatStreamHandler.ts', {
  '@/components/Transcript': {
    PLAN_MODE_TOOL_NAME: 'request_plan_review',
    createTranscriptTool: ({ toolCallId, name, args = null, result = null }) => ({
      type: 'tool', tool_call_id: toolCallId, name, args, result, resultReceived: false,
    }),
  },
  '@/lib/eventConstants': load('../src/lib/eventConstants.ts'),
  '@/lib/generatedImages': { GENERATE_IMAGE_TOOL_NAME: 'generate_image' },
  '@/lib/userInputRequest': { ASK_USER_TOOL_NAME: 'ask_user' },
  '@/types': { parseContextBreakdown: () => undefined },
});
const protocol = load('../src/lib/protocolStreamHandler.ts', {
  '@/lib/chatStreamHandler': streamHelpers,
  '@/lib/toolCallDisplay': load('../src/lib/toolCallDisplay.ts'),
});

test('an old SSE event or snapshot cannot replace a newer task state', () => {
  const completed = { version: 5, status: 'completed', result: 'Done' };
  assert.equal(mergeAsyncTask(completed, { version: 4, status: 'running' }), completed);
  assert.equal(mergeAsyncTask(completed, { version: 6, status: 'pending' }).status, 'pending');
});

test('a delayed launch result cannot replace a newer task already linked from SSE', () => {
  const item = { type: 'tool', subagent: task('linked', { version: 3, status: 'completed' }) };
  const merged = streamHelpers.mergeToolActivity(item, { subagent: task('linked', { version: 1, status: 'pending' }) });
  assert.equal(merged.subagent, item.subagent);
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
    '@/lib/uiConstants': { SSE_RECONNECT_DELAY_MS: 3000 },
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

test('a launch awaiting its result reserves the inline card position', () => {
  const active = [task('first'), task('second')];
  const items = active.map(value => ({ type: 'tool', name: 'start_async_task', tool_call_id: value.tool_call_id }));
  assert.deepEqual(missingAsyncTasks(active, items), []);
  const linked = asyncHelpers.attachAsyncTaskActivities(items, active, 'parent');
  assert.deepEqual(linked.map(item => item.subagent.task_id), ['first', 'second']);
  assert.deepEqual(linked.map(item => item.tool_call_id), items.map(item => item.tool_call_id));
  assert.equal(asyncHelpers.attachAsyncTaskActivities(linked, active, 'parent'), linked);
  assert.equal(asyncHelpers.attachAsyncTaskActivities(items, active, 'other'), items);
});

test('replayed child activity merges with saved history without losing earlier runs or repeating rows', () => {
  const saved = [
    { type: 'message', role: 'user', text: 'Initial task', messageId: 'input' },
    { type: 'message', role: 'assistant', text: 'Earlier answer', messageId: 'earlier' },
    { type: 'message', role: 'user', text: 'Follow up', messageId: 'follow-up' },
    { type: 'thinking', text: 'Inspect source', messageId: 'lc_run--current' },
    { type: 'tool', name: 'read_file', tool_call_id: 'read', result: 'source', resultReceived: true },
  ];
  const replay = [
    { id: 1, type: 'thinking', text: 'Inspect', messageId: 'current' },
    { id: 2, type: 'tool', name: 'read_file', tool_call_id: 'read', result: null, resultReceived: false },
    { id: 3, type: 'message', role: 'assistant', text: 'Latest answer', messageId: 'latest' },
  ];
  const merged = asyncHelpers.mergeSubagentTranscript(saved, replay);
  assert.deepEqual(merged.map(item => item.text || item.tool_call_id), [
    'Initial task', 'Earlier answer', 'Follow up', 'Inspect source', 'read', 'Latest answer',
  ]);
  assert.equal(merged[4].result, 'source');
  assert.equal(merged[4].resultReceived, true);
  assert.equal(new Set(merged.map(item => item.key)).size, merged.length);
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

function liveFixture(t) {
  const streams = [], timers = new Map();
  const events = new EventTarget();
  let value = task('live', { run_id: 'job', status: 'running' });
  const { useAsyncSubagent } = load('../src/lib/useAsyncSubagent.ts', {
    'solid-js': solid, 'solid-js/store': solidStore,
    '@/lib/api': {
      apiFetch: async () => value,
      fetchWithCsrf: async (_path, init) => {
        let writer;
        const body = new ReadableStream({ start(controller) { writer = controller; } });
        let closed = false;
        const close = () => { if (!closed) { closed = true; writer.close(); } };
        const entry = { signal: init.signal, close, send: event => writer.enqueue(new TextEncoder().encode(JSON.stringify(event) + '\n')) };
        init.signal.addEventListener('abort', close, { once: true });
        streams.push(entry);
        return { ok: true, body };
      },
    },
    '@/lib/asyncSubagents': asyncHelpers, '@/lib/chatStreamHandler': streamHelpers, '@/lib/protocolStreamHandler': protocol,
    '@/lib/eventConstants': { CUSTOM_DOM_EVENTS: { SUBAGENT_TASK_UPDATED: 'task', SESSIONS_RECONNECTED: 'reconnected' } },
    '@/lib/uiConstants': { SSE_RECONNECT_DELAY_MS: 3000 },
  }, { window: events, setTimeout: callback => { const id = Symbol(); timers.set(id, callback); return id; }, clearTimeout: id => timers.delete(id) });
  let setOpened, setActivity;
  const state = solid.createRoot(dispose => {
    t.after(dispose);
    const [opened, updateOpened] = solid.createSignal(true);
    const [activity, updateActivity] = solid.createSignal(value);
    setOpened = updateOpened;
    setActivity = patch => { value = { ...value, ...patch }; updateActivity(value); };
    return useAsyncSubagent(activity, opened);
  });
  const send = (stream, seq, content, runId = 'stream-run') => streams[stream].send({
    type: 'event', method: 'custom:k41', seq, event_id: `${runId}:${seq}`,
    params: { namespace: [], data: { type: 'message', message_id: runId, projection: 'ui', content } },
  });
  return { state, streams, timers, setOpened, setActivity, send };
}

test('live child rows keep their identity, text and order across deltas, collapse and replay', async t => {
  const f = liveFixture(t);
  f.send(0, 1, 'First');
  await tick();
  const first = f.state.liveItems()[0];
  f.send(0, 2, ' answer');
  await tick();
  assert.equal(f.state.liveItems()[0], first);
  assert.equal(first.text, 'First answer');
  f.setOpened(false);
  assert.equal(f.streams[0].signal.aborted, true);
  assert.equal(f.state.liveItems()[0], first);
  f.setOpened(true);
  f.send(1, 1, 'First');
  f.send(1, 2, ' answer');
  f.send(1, 3, '!');
  await tick();
  assert.equal(f.state.liveItems().length, 1);
  assert.equal(f.state.liveItems()[0], first);
  assert.equal(first.text, 'First answer!');
  f.setActivity({ version: 2, status: 'completed' });
  assert.equal(f.state.liveItems()[0], first);
  assert.equal(f.state.activity().status, 'completed');
});

test('a child stream not yet admitted retries and a replacement run follows the earlier answer', async t => {
  const f = liveFixture(t);
  f.streams[0].close();
  await tick();
  assert.equal(f.timers.size, 1);
  const [id, retry] = f.timers.entries().next().value;
  f.timers.delete(id);
  retry();
  f.send(1, 1, 'First answer');
  await tick();
  f.send(1, 1, 'Follow-up answer', 'replacement');
  await tick();
  assert.deepEqual(Array.from(f.state.liveItems(), item => item.text), ['First answer', 'Follow-up answer']);
});
