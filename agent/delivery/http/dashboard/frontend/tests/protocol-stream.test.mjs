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
    createTranscriptTool: ({ toolCallId, name, args = null, result = null }) => ({
      type: 'tool', tool_call_id: toolCallId, name, args, result,
    }),
  },
  '@/lib/eventConstants': load('../src/lib/eventConstants.ts', {}),
  '@/lib/generatedImages': { GENERATE_IMAGE_TOOL_NAME: 'generate_image' },
  '@/lib/userInputRequest': { ASK_USER_TOOL_NAME: 'ask_user' },
  '@/types': { parseContextBreakdown: () => undefined },
});
const protocol = load('../src/lib/protocolStreamHandler.ts', {
  '@/lib/chatStreamHandler': handler,
});

function fixture() {
  let seq = 0, nextId = 1;
  const items = [];
  const errors = [];
  const callbacks = {
    appendItem: (item) => { const id = nextId++; items.push({ ...item, id }); return id; },
    updateMessage: (id, text) => { items.find((item) => item.id === id).text += text; },
    replaceMessage: (id, text) => { items.find((item) => item.id === id).text = text; },
    removeItem: (id) => { const index = items.findIndex((item) => item.id === id); if (index >= 0) items.splice(index, 1); },
    finishThinking: () => {},
    updateToolResult: (id, name, result) => {
      Object.assign(items.find((item) => item.tool_call_id === id), { result, resultReceived: true });
    },
    updateToolActivity: (id, changes) => {
      const item = items.find((row) => row.tool_call_id === id);
      if (item) Object.assign(item, handler.mergeToolActivity(item, changes));
    },
    updatePlanReviewResult: () => {}, updateUserInputRequestResult: () => {},
    setRecursionLimitReached: () => {}, onThreadCreated: () => {}, onError: (error) => errors.push(error),
  };
  const refs = { id: callbacks.appendItem({ type: 'message', role: 'assistant', text: 'Thinking...' }) };
  const streamed = { received: false }, thread = { id: 'parent', message: '' };
  const sendEvent = (event) => protocol.handleProtocolEvent(event, refs, streamed, thread, callbacks);
  const send = (method, data, namespace = []) => {
    const event = { type: 'event', method, params: { namespace, data, timestamp: 0 },
      seq: ++seq, event_id: `run:${seq}` };
    sendEvent(event);
    return event;
  };
  return { items, errors, send, sendEvent, callbacks };
}

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
