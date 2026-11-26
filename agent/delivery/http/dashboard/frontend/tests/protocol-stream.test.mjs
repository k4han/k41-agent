import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import * as solid from 'solid-js/dist/solid.js';
import ts from 'typescript';

function load(path, dependencies, globals = {}) {
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  runInNewContext(outputText, { exports, setTimeout, clearTimeout, ...globals,
    require: (name) => { assert.ok(name in dependencies, name); return dependencies[name]; },
  });
  return exports;
}

const handler = load('../src/lib/chatStreamHandler.ts', {
  '@/components/Transcript': {
    PLAN_MODE_TOOL_NAME: 'request_plan_review',
    createTranscriptTool: ({ toolCallId, name, args = null, result = null, dispatched }) => ({
      type: 'tool', tool_call_id: toolCallId, name, args, result,
      ...(dispatched ? { dispatched } : {}),
    }),
  },
  '@/lib/eventConstants': load('../src/lib/eventConstants.ts', {}),
  '@/lib/generatedImages': { GENERATE_IMAGE_TOOL_NAME: 'generate_image' },
  '@/lib/userInputRequest': { ASK_USER_TOOL_NAME: 'ask_user' },
  '@/types': { parseContextBreakdown: () => undefined },
});
const protocol = load('../src/lib/protocolStreamHandler.ts', {
  '@/lib/chatStreamHandler': handler,
  '@/lib/toolCallDisplay': load('../src/lib/toolCallDisplay.ts', {}),
});
const replay = load('../src/lib/chatStreamReplay.ts', {
  '@/lib/asyncSubagents': load('../src/lib/asyncSubagents.ts', { '@/lib/userInputRequest': {} }),
});

function fixture(previous = [], replaying = false) {
  let seq = 0, nextId = Math.max(0, ...previous.map(item => item.id)) + 1;
  const items = [...previous];
  const errors = [];
  const callbacks = {
    appendItem: (item) => { const id = nextId++; items.push({ ...item, id }); return id; },
    updateMessage: (id, text) => { items.find((item) => item.id === id).text += text; },
    replaceMessage: (id, text) => { items.find((item) => item.id === id).text = text; },
    removeItem: (id) => { const index = items.findIndex((item) => item.id === id); if (index >= 0) items.splice(index, 1); },
    finishThinking: () => {},
    updateToolResult: (id, name, result) => {
      const item = items.find((row) => row.type === 'tool' && row.tool_call_id === id);
      if (item) Object.assign(item, { result, resultReceived: true });
      else callbacks.appendItem({ type: 'tool', tool_call_id: id, name, result, resultReceived: true });
    },
    updateToolActivity: (id, changes) => {
      const item = items.find((row) => row.tool_call_id === id);
      if (item) Object.assign(item, handler.mergeToolActivity(item, changes));
    },
    updatePlanReviewResult: () => {}, updateUserInputRequestResult: () => {},
    setRecursionLimitReached: () => {}, onThreadCreated: () => {}, onError: (error) => errors.push(error),
  };
  const refs = { id: previous.length ? null : callbacks.appendItem({ type: 'message', role: 'assistant', text: 'Thinking...' }) };
  const streamed = { received: false }, thread = { id: 'parent', message: '' };
  const projection = replaying ? replay.createReplayCallbacks(callbacks, previous) : callbacks;
  const sendEvent = (event) => protocol.handleProtocolEvent(event, refs, streamed, thread, projection);
  const send = (method, data, namespace = []) => {
    const event = { type: 'event', method, params: { namespace, data, timestamp: 0 },
      seq: ++seq, event_id: `run:${seq}` };
    sendEvent(event);
    return event;
  };
  return { items, errors, send, sendEvent, callbacks };
}

test('a parent continuation retains both completed subagents while streaming its final answer', () => {
  const previous = [
    { id: 1, type: 'message', role: 'user', messageId: 'user', text: 'Use two agents' },
    ...['browser', 'researcher'].map((id, index) => ({ id: index + 2, type: 'tool', name: 'start_async_task',
      tool_call_id: id, result: 'Accepted', resultReceived: true,
      subagent: { delegation_id: id, execution_mode: 'async', version: 3, status: 'completed' } })),
    { id: 4, type: 'message', role: 'assistant', messageId: 'launch-answer', text: 'Waiting for both agents.' },
  ];
  const f = fixture(previous, true);
  f.send('lifecycle', { event: 'started', thread_id: 'parent' });
  f.send('messages', [{ event: 'message-start', id: 'combined-answer' }, { run_id: 'combined-answer' }]);
  f.send('custom:k41', { type: 'message', message_id: 'combined-answer', content: 'Combined ', projection: 'ui' });
  assert.deepEqual(f.items.slice(0, 4), previous);
  assert.equal(f.items.filter(item => item.subagent).length, 2);
  assert.equal(f.items.at(-1).text, 'Combined ');
  f.send('custom:k41', { type: 'message', message_id: 'combined-answer', content: 'answer', projection: 'ui' });
  assert.equal(f.items.at(-1).text, 'Combined answer');
});

test('a queued input starts a separate reply and is reused by stream replay', () => {
  const render = f => {
    f.send('lifecycle', { event: 'started', thread_id: 'parent' });
    f.send('messages', [{ event: 'message-start', id: 'first-answer' }, { run_id: 'first-answer' }]);
    f.send('custom:k41', { type: 'message', message_id: 'first-answer', content: 'First answer', projection: 'ui' });
    const queued = f.send('custom:k41', {
      type: 'chat_queue_delivery', message_id: 'user-queued-1', text: 'Follow-up', inserted: false, projection: 'ui',
    });
    f.sendEvent(queued);
    f.send('messages', [{ event: 'message-start', id: 'second-answer' }, { run_id: 'second-answer' }]);
    f.send('custom:k41', { type: 'message', message_id: 'second-answer', content: 'Second answer', projection: 'ui' });
  };
  const live = fixture([{ id: 1, type: 'message', role: 'user', text: 'Start' }]);
  render(live);
  assert.deepEqual(live.items.map(item => [item.role, item.text]), [
    ['user', 'Start'], ['assistant', 'First answer'], ['user', 'Follow-up'], ['assistant', 'Second answer'],
  ]);
  const replayed = fixture(live.items, true);
  render(replayed);
  assert.deepEqual(replayed.items, live.items);
});

test('reconnecting the same run reuses tools and answers without shrinking checkpoint text', () => {
  const previous = [
    { id: 1, type: 'message', role: 'user', messageId: 'user', text: 'Delegate work' },
    { id: 2, type: 'thinking', messageId: 'lc_run--launch', text: 'Review the source.', durationMs: 1000 },
    { id: 3, type: 'message', role: 'assistant', messageId: 'lc_run--launch', text: 'Starting a worker.' },
    { id: 4, type: 'tool', name: 'start_async_task', tool_call_id: 'worker', resultReceived: true, result: 'Accepted',
      subagent: { delegation_id: 'worker', execution_mode: 'async', version: 3, status: 'completed' } },
    { id: 5, type: 'message', role: 'assistant', messageId: 'lc_run--answer', text: 'Partial answer' },
  ];
  const f = fixture(previous, true);
  f.send('messages', [{ event: 'message-start', id: 'launch' }, { run_id: 'launch' }]);
  f.send('custom:k41', { type: 'thinking', message_id: 'launch', content: 'Review', projection: 'ui' });
  assert.equal(f.items[1].text, 'Review the source.');
  f.send('custom:k41', { type: 'message', message_id: 'launch', content: 'Starting', projection: 'ui' });
  assert.equal(f.items[2].text, 'Starting a worker.');
  f.send('tools', { event: 'tool-started', tool_call_id: 'worker', tool_name: 'start_async_task', input: {} });
  f.send('custom:k41', { type: 'tool_result', tool_call_id: 'worker', name: 'start_async_task',
    content: 'Accepted', projection: 'ui', subagent: { delegation_id: 'worker', execution_mode: 'async', version: 1, status: 'pending' } });
  f.send('messages', [{ event: 'message-start', id: 'answer' }, { run_id: 'answer' }]);
  f.send('custom:k41', { type: 'message', message_id: 'answer', content: 'Partial', projection: 'ui' });
  assert.equal(f.items[4].text, 'Partial answer');
  f.send('custom:k41', { type: 'message', message_id: 'answer', content: ' answer and more', projection: 'ui' });
  assert.equal(f.items[4].text, 'Partial answer and more');
  assert.equal(f.items.length, 5);
  assert.equal(f.items[3].subagent.status, 'completed');
  assert.deepEqual(f.items.map(item => item.id), [1, 2, 3, 4, 5]);
});

test('checkpoint refreshes preserve subagent row IDs and allocate IDs only for new content', () => {
  const previous = [
    { id: 10, type: 'tool', name: 'start_async_task', tool_call_id: 'worker', result: 'Accepted' },
    { id: 11, type: 'message', role: 'assistant', messageId: 'answer', text: 'Partial' },
  ];
  let nextId = 20;
  const snapshot = replay.preserveTranscriptIds([
    { type: 'tool', name: 'start_async_task', tool_call_id: 'worker', result: 'Accepted' },
    { type: 'message', role: 'assistant', messageId: 'lc_run--answer', text: 'Full answer' },
    { type: 'message', role: 'assistant', messageId: 'new-answer', text: 'New content' },
  ], previous, () => nextId++);
  assert.deepEqual(Array.from(snapshot, item => item.id), [10, 11, 20]);
});

test('wakeups add a fixed boundary before output and deduplicate stream and checkpoint replays', () => {
  const f = fixture();
  const wakeup = { type: 'async_wakeup', wakeup_id: 'wake-1', agent_names: ['Browser', 'Researcher'], projection: 'ui' };
  f.send('custom:k41', wakeup);
  f.send('custom:k41', wakeup);
  f.send('custom:k41', { type: 'message', message_id: 'wake-answer', content: 'Combined answer', projection: 'ui' });
  assert.deepEqual(f.items.map(item => item.type), ['async_wakeup', 'message']);
  assert.deepEqual(Array.from(f.items[0].agentNames), ['Browser', 'Researcher']);
  const previous = [...f.items];
  const repeated = fixture(previous, true);
  repeated.send('custom:k41', wakeup);
  assert.equal(repeated.items.filter(item => item.type === 'async_wakeup').length, 1);
  const saved = replay.preserveTranscriptIds([
    { type: 'async_wakeup', wakeup_id: 'wake-1', agentNames: ['Browser', 'Researcher'] },
    { type: 'message', role: 'assistant', messageId: 'lc_run--wake-answer', text: 'Combined answer' },
  ], previous, () => 99);
  assert.deepEqual(Array.from(saved, item => item.id), previous.map(item => item.id));
  repeated.send('custom:k41', { ...wakeup, wakeup_id: 'wake-2', agent_names: ['Browser'] });
  assert.equal(repeated.items.filter(item => item.type === 'async_wakeup').length, 2);
});

test('joining and reconnecting a running background stream renders each projected delta once', (t) => {
  const sources = [];
  class FakeEventSource {
    listeners = new Map();
    constructor() { sources.push(this); }
    addEventListener(name, callback) { this.listeners.set(name, callback); }
    close() {}
    emit(name, payload) { this.listeners.get(name)?.({ data: JSON.stringify(payload) }); }
  }
  const { useBackgroundStream } = load('../src/lib/useBackgroundStream.ts', {
    'solid-js': solid,
    '@/lib/endpoints': { API_PATHS: { backgroundTaskStream: (id) => id } },
    '@/lib/protocolStreamHandler': protocol,
    '@/lib/eventConstants': { CUSTOM_DOM_EVENTS: {} },
  }, { EventSource: FakeEventSource });
  const f = fixture();
  solid.createRoot((dispose) => {
    t.after(dispose);
    const background = useBackgroundStream({
      applyThreadPayload: () => { f.items.length = 0; },
      streamCallbacks: f.callbacks,
    });
    background.openBackgroundStream('parent');
    const source = sources[0];
    let seq = 30;
    const emit = (method, data) => source.emit('agent', {
      type: 'event', method, params: { namespace: [], data, timestamp: 0 },
      seq: ++seq, event_id: `background:${seq}`,
    });
    for (const text of ['Hello', 'Reconnected']) {
      source.emit('snapshot', { thread_id: 'parent', messages: [] });
      emit('messages', [{ event: 'content-block-delta', index: 0,
        delta: { type: 'text-delta', text } }, { run_id: 'message' }]);
      emit('custom:k41', { type: 'message', message_id: 'message', content: text, projection: 'ui' });
      emit('custom:k41', { type: 'final', message_id: 'message', content: text, projection: 'ui' });
      assert.deepEqual(f.items.map((item) => item.text), [text]);
    }
  });
});

test('native text is rendered only through its normalized application update', () => {
  const f = fixture();
  f.send('messages', [{ event: 'message-start', id: 'message' }, { run_id: 'message' }]);
  f.send('messages', [{ event: 'content-block-delta', index: 0,
    delta: { type: 'text-delta', text: 'Native answer' } }, { run_id: 'message' }]);
  assert.deepEqual(f.items.map((item) => item.text), ['Thinking...']);
  f.send('custom:k41', { type: 'message', content: 'Native answer', projection: 'ui' });
  assert.deepEqual(f.items.map((item) => item.text), ['Native answer']);
});

test('v3 UI projection renders tokens once and keeps narration before tools', () => {
  const f = fixture();
  f.send('lifecycle', { event: 'started' });
  f.send('messages', [{ event: 'message-start', id: 'message' }, { run_id: 'message' }]);
  f.send('messages', [{ event: 'content-block-delta', index: 0, delta: { type: 'text-delta', text: 'Hello' } }, { run_id: 'message' }]);
  const event = f.send('custom:k41', { type: 'message', content: 'Hello', projection: 'ui' });
  f.sendEvent(event);
  f.send('custom:k41', { type: 'final', content: 'Hello', projection: 'ui' });
  f.send('tools', { event: 'tool-started', tool_call_id: 'tool', tool_name: 'read', input: {} });
  f.send('custom:k41', { type: 'tool_call', id: 'tool', name: 'read', args: {}, projection: 'ui' });
  assert.deepEqual(f.items.map((item) => item.type), ['message', 'tool']);
  assert.equal(f.items[0].text, 'Hello');
});

test('concurrent delegated streams keep separate collapsed activity records', () => {
  const f = fixture();
  f.send('lifecycle', { event: 'started' });
  for (const id of ['a', 'b']) {
    f.send('tools', { event: 'tool-started', tool_call_id: id, tool_name: 'delegate_task', input: {} });
    f.send('lifecycle', { event: 'started', delegation_id: id, tool_call_id: id, agent_name: id,
      thread_id: `parent:sub:${id}` }, [`${id}:${id}`]);
  }
  f.send('custom:k41', { type: 'message', content: 'Alpha', projection: 'ui' }, ['a:a']);
  f.send('custom:k41', { type: 'message', content: 'Beta', projection: 'ui' }, ['b:b']);
  f.send('lifecycle', { event: 'completed' });
  const tools = f.items.filter((item) => item.type === 'tool');
  assert.equal(tools.length, 2);
  assert.equal(tools[0].subagent.items[0].text, 'Alpha');
  assert.equal(tools[1].subagent.items[0].text, 'Beta');
  assert.equal(tools[0].subagent.status, 'completed');
});

for (const nested of [false, true]) {
  test(`${nested ? 'nested' : 'root'} delegation retains live items when completion patches omit them or send an empty list`, (t) => {
    const f = fixture();
    t.after(() => f.send('lifecycle', { event: 'completed' }));
    f.send('lifecycle', { event: 'started' });
    f.send('tools', { event: 'tool-started', tool_call_id: 'a', tool_name: 'delegate_task', input: {} });
    f.send('lifecycle', { event: 'started', delegation_id: 'a', tool_call_id: 'a', agent_name: 'child',
      thread_id: 'parent:sub:a' }, ['child:a']);
    const parentNamespace = nested ? ['child:a'] : [];
    const namespace = nested ? ['child:a', 'grandchild:b'] : ['child:a'];
    const id = nested ? 'b' : 'a';
    const descriptor = { delegation_id: id, tool_call_id: id,
      agent_name: nested ? 'grandchild' : 'child', parent_thread_id: nested ? 'parent:sub:a' : 'parent',
      thread_id: nested ? 'parent:sub:a:sub:b' : 'parent:sub:a' };
    if (nested) {
      f.send('tools', { event: 'tool-started', tool_call_id: id, tool_name: 'delegate_task', input: {} }, parentNamespace);
      f.send('lifecycle', { ...descriptor, event: 'started' }, namespace);
    }
    f.send('custom:k41', { type: 'message', content: 'Live answer', projection: 'ui' }, namespace);
    f.send('lifecycle', { ...descriptor, event: 'completed' }, namespace);
    const commitParent = () => {
      if (nested) f.send('lifecycle', { event: 'completed', delegation_id: 'a' }, ['child:a']);
    };
    commitParent();
    const target = () => nested
      ? f.items.find((item) => item.tool_call_id === 'a').subagent.items.find((item) => item.tool_call_id === id)
      : f.items.find((item) => item.tool_call_id === id);
    for (const patch of [{}, { items: [] }]) {
      f.send('custom:k41', { type: 'tool_result', tool_call_id: id, name: 'delegate_task',
        content: 'Summary', projection: 'ui',
        subagent: { ...descriptor, status: 'completed', checkpoint_id: 'checkpoint', ...patch } }, parentNamespace);
      commitParent();
      assert.equal(target().subagent.items[0].text, 'Live answer');
      assert.equal(target().subagent.checkpoint_id, 'checkpoint');
      assert.equal(target().subagent.status, 'completed');
    }
    f.send('custom:k41', { type: 'tool_result', tool_call_id: id, name: 'delegate_task',
      content: 'Summary', projection: 'ui', subagent: { ...descriptor, status: 'completed',
        items: [{ type: 'message', role: 'assistant', text: 'Full snapshot' }] } }, parentNamespace);
    commitParent();
    assert.equal(target().subagent.items.length, 1);
    assert.equal(target().subagent.items[0].text, 'Full snapshot');
  });
}

test('native tool argument and output deltas accumulate without extra rows', () => {
  const f = fixture();
  f.send('messages', [{ event: 'message-start', id: 'message' }, { run_id: 'message' }]);
  f.send('messages', [{ event: 'content-block-start', index: 0,
    content: { type: 'tool_call', id: 'tool', name: 'read', args: '' } }, { run_id: 'message' }]);
  for (const args of ['{"path":', '"file"}']) f.send('messages', [
    { event: 'content-block-delta', index: 0, delta: { type: 'tool-call-delta', args } }, { run_id: 'message' },
  ]);
  f.send('tools', { event: 'tool-started', tool_call_id: 'tool', tool_name: 'read', input: { path: 'file' } });
  f.send('tools', { event: 'tool-output-delta', tool_call_id: 'tool', delta: 'one' });
  f.send('tools', { event: 'tool-output-delta', tool_call_id: 'tool', delta: 'two' });
  const tools = f.items.filter((item) => item.type === 'tool');
  assert.equal(tools.length, 1);
  assert.equal(tools[0].args.path, 'file');
  assert.equal(tools[0].outputDelta, 'onetwo');
});

test('a dispatched tool keeps its target, arguments and marker across stream events', () => {
  const f = fixture();
  const call = { id: 'fetch', name: 'execute_tool' };
  const args = { url: 'https://example.test' };
  const request = { tool_name: 'web_fetch', arguments: args };
  const metadata = { run_id: 'message' };
  f.send('messages', [{ event: 'content-block-start', index: 0,
    content: { ...call, type: 'tool_call_chunk', args: '' } }, metadata]);
  f.send('messages', [{ event: 'content-block-delta', index: 0,
    delta: { type: 'tool-call-delta', args: '{"tool_name":"web_fetch",' } }, metadata]);
  assert.equal(f.items.filter(item => item.type === 'tool').length, 0);
  f.send('messages', [{ event: 'content-block-delta', index: 0,
    delta: { type: 'tool-call-delta', args: `"arguments":${JSON.stringify(args)}}` } }, metadata]);
  const row = f.items.find(item => item.type === 'tool');
  assert.equal(row.name, 'web_fetch');
  assert.equal(row.args.url, args.url);
  f.send('messages', [{ event: 'content-block-finish', index: 0,
    content: { ...call, type: 'tool_call', args: request } }, metadata]);
  f.send('custom:k41', { type: 'tool_call', ...call, args: request, projection: 'ui' });
  f.send('tools', { event: 'tool-started', tool_call_id: call.id, tool_name: 'web_fetch', input: args });
  f.send('custom:k41', { type: 'tool_result', tool_call_id: call.id, name: 'web_fetch',
    content: 'Fetched', projection: 'ui' });
  assert.deepEqual(f.items.map(item => [item.name, item.dispatched, item.result]), [['web_fetch', true, 'Fetched']]);
  assert.deepEqual(row.args, args);

  f.send('custom:k41', { type: 'tool_call', id: 'direct', name: 'web_fetch', args, projection: 'ui' });
  assert.equal(f.items.at(-1).dispatched, undefined);
});

test('a discovery tool error preserves the reason from the native stream', () => {
  const f = fixture();
  f.send('tools', { event: 'tool-started', tool_call_id: 'discover', tool_name: 'search_tools', input: { query: 'web' } });
  f.send('tools', { event: 'tool-error', tool_call_id: 'discover', message: 'Discovery service timed out' });
  const row = f.items.find(item => item.tool_call_id === 'discover');
  assert.equal(row.executionStatus, 'failed');
  assert.equal(row.result, 'Discovery service timed out');
  assert.equal(row.resultReceived, true);
});

test('a malformed dispatch gets a transcript row with the raw arguments at block finish', () => {
  const f = fixture();
  const call = { id: 'invalid-dispatch', name: 'execute_tool' };
  const metadata = { run_id: 'message' };
  const args = '{"tool_name":"web_fetch","arguments":';
  f.send('messages', [{ event: 'content-block-start', index: 0,
    content: { ...call, type: 'tool_call_chunk', args: '' } }, metadata]);
  f.send('messages', [{ event: 'content-block-delta', index: 0,
    delta: { type: 'tool-call-delta', args } }, metadata]);
  assert.equal(f.items.filter(item => item.type === 'tool').length, 0);
  f.send('messages', [{ event: 'content-block-finish', index: 0,
    content: { ...call, type: 'invalid_tool_call', args: null, error: 'Malformed arguments' } }, metadata]);
  f.send('custom:k41', { type: 'tool_result', tool_call_id: call.id, name: call.name,
    status: 'error', content: 'Invalid tool input', projection: 'ui' });
  const [row] = f.items.filter(item => item.type === 'tool');
  assert.equal(f.items.filter(item => item.type === 'tool').length, 1);
  assert.equal(row.name, call.name);
  assert.equal(row.args, args);
  assert.equal(row.executionStatus, 'failed');
  assert.equal(row.result, 'Invalid tool input');
});

test('a rejected dispatched question shows its error in the live transcript', () => {
  const f = fixture();
  const error = '[error] permission_denied: Tool is unavailable to this agent.';
  f.send('custom:k41', { type: 'tool_call', id: 'question', name: 'execute_tool',
    args: { tool_name: 'ask_user', arguments: {} }, projection: 'ui' });
  f.send('custom:k41', { type: 'tool_result', tool_call_id: 'question', name: 'execute_tool',
    status: 'error', content: error, projection: 'ui' });
  const [item] = f.items.filter(row => row.tool_call_id === 'question');
  assert.equal(item.type, 'tool');
  assert.equal(item.name, 'ask_user');
  assert.equal(item.executionStatus, 'failed');
  assert.equal(item.result, error);
});

test('interleaved model invocations retain their message identity', () => {
  const f = fixture();
  f.send('lifecycle', { event: 'started' });
  for (const id of ['a', 'b']) f.send('messages', [{ event: 'message-start', id: `lc_run--${id}` }, { run_id: id }]);
  for (const [id, content] of [['a', 'Alpha'], ['b', 'Beta'], ['a', ' tail']]) {
    f.send('custom:k41', { type: 'message', message_id: id, content, projection: 'ui' });
  }
  f.send('custom:k41', { type: 'final', message_id: 'lc_run--a', content: 'Alpha tail', projection: 'ui' });
  f.send('custom:k41', { type: 'final', message_id: 'lc_run--b', content: 'Beta', projection: 'ui' });
  assert.deepEqual(f.items.map((item) => item.text), ['Alpha tail', 'Beta']);
});

test('nested delegation keeps descendants under their originating tool', () => {
  const f = fixture();
  f.send('lifecycle', { event: 'started' });
  f.send('tools', { event: 'tool-started', tool_call_id: 'a', tool_name: 'delegate_task', input: {} });
  f.send('lifecycle', { event: 'started', delegation_id: 'a', tool_call_id: 'a', agent_name: 'child',
    thread_id: 'parent:sub:a' }, ['child:a']);
  f.send('tools', { event: 'tool-started', tool_call_id: 'b', tool_name: 'delegate_task', input: {} }, ['child:a']);
  f.send('lifecycle', { event: 'started', delegation_id: 'b', tool_call_id: 'b', agent_name: 'grandchild',
    thread_id: 'parent:sub:a:sub:b' }, ['child:a', 'grandchild:b']);
  f.send('custom:k41', { type: 'message', content: 'Nested answer', projection: 'ui' }, ['child:a', 'grandchild:b']);
  f.send('lifecycle', { event: 'completed' });
  const parent = f.items.find((item) => item.tool_call_id === 'a');
  const child = parent.subagent.items.find((item) => item.tool_call_id === 'b');
  assert.equal(child.subagent.items[0].text, 'Nested answer');
  assert.equal(child.subagent.parent_thread_id, 'parent:sub:a');
});
