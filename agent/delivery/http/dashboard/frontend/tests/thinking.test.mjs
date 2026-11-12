import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

function loadModule(path, dependencies = {}) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  runInNewContext(outputText, {
    exports,
    require: (name) => {
      assert.ok(name in dependencies, `Unexpected dependency: ${name}`);
      return dependencies[name];
    },
  });
  return exports;
}

const transcriptStub = {
  PLAN_MODE_TOOL_NAME: 'plan_mode',
  createTranscriptTool: ({ toolCallId, name, args = null, result = null }) => ({
    type: 'tool', tool_call_id: toolCallId || null, name: name || 'unknown', args, result,
  }),
  findTranscriptToolTarget: () => undefined,
  findTranscriptPlanReviewTarget: () => undefined,
  findTranscriptUserInputRequestTarget: () => undefined,
};
const durations = loadModule('../src/lib/thinkingDurations.ts');
const handler = loadModule('../src/lib/chatStreamHandler.ts', {
  '@/components/Transcript': transcriptStub,
  '@/lib/eventConstants': loadModule('../src/lib/eventConstants.ts'),
  '@/lib/generatedImages': { GENERATE_IMAGE_TOOL_NAME: 'generate_image' },
  '@/lib/userInputRequest': { ASK_USER_TOOL_NAME: 'ask_user' },
  '@/types': loadModule('../src/types.ts'),
});
const activity = loadModule('../src/lib/transcriptActivity.ts', {
  '@/lib/htmlPreview': { HTML_PREVIEW_TOOL_NAME: 'html_preview' },
  '@/lib/toolCallStatus': loadModule('../src/lib/toolCallStatus.ts'),
});
const threads = loadModule('../src/lib/chatThreads.ts', {
  '@/components/Transcript': transcriptStub,
  '@/lib/userInputRequest': { ASK_USER_TOOL_NAME: 'ask_user' },
  '@/lib/generatedImages': { GENERATE_IMAGE_TOOL_NAME: 'generate_image' },
  '@/lib/thinkingDurations': durations,
  '@/lib/workspaceConstants': { NO_WORKSPACE_KEY: 'no-workspace', NO_WORKSPACE_LABEL: 'No workspace' },
  '@/lib/workspace': { isNoWorkspace: () => false },
});

function createStream(placeholderText = 'Thinking...') {
  const items = [];
  let nextId = 1;
  const finished = [];
  const callbacks = {
    appendItem: (item) => {
      const id = nextId++;
      items.push({ ...item, id });
      return id;
    },
    updateMessage: (id, chunk) => {
      const item = items.find((row) => row.id === id);
      item.text += chunk;
    },
    replaceMessage: (id, text) => {
      items.find((row) => row.id === id).text = text;
    },
    removeItem: (id) => {
      items.splice(items.findIndex((row) => row.id === id), 1);
    },
    finishThinking: (id, durationMs) => finished.push({ id, durationMs }),
    updateToolResult: () => {},
    updatePlanReviewResult: () => {},
    updateUserInputRequestResult: () => {},
    setRecursionLimitReached: () => {},
    onThreadCreated: () => {},
  };
  const placeholderId = callbacks.appendItem({ type: 'message', role: 'assistant', text: placeholderText });
  const assistantIdRef = { id: placeholderId };
  const streamedRef = { received: false };
  const threadRef = { id: 'thread-a', message: '' };
  const send = (event) => handler.applyApplicationEvent(event, assistantIdRef, streamedRef, threadRef, callbacks);
  return { items, finished, send, assistantIdRef, threadRef, callbacks };
}

test('thinking replaces the status placeholder and settles when the answer starts', () => {
  const stream = createStream();
  stream.send({ type: 'thinking', content: 'Check the ' });
  stream.send({ type: 'thinking', content: 'cache.' });

  assert.deepEqual(stream.items.map(({ type, role, text }) => ({ type, role, text })), [
    { type: 'thinking', role: undefined, text: 'Check the cache.' },
  ]);
  assert.equal(stream.finished.length, 0);

  stream.send({ type: 'message', content: 'Warm.' });
  stream.send({ type: 'final', content: 'Warm.' });

  assert.deepEqual(stream.items.map((item) => item.type), ['thinking', 'message']);
  assert.equal(stream.items[1].text, 'Warm.');
  assert.equal(stream.finished.length, 1);
  assert.equal(stream.finished[0].id, stream.items[0].id);
  assert.ok(stream.finished[0].durationMs >= 0);
});

test('reasoning between tool calls opens a new block each time', () => {
  const stream = createStream();
  stream.send({ type: 'thinking', content: 'Read first.' });
  stream.send({ type: 'tool_call', id: 'call-1', name: 'read', args: {} });
  stream.send({ type: 'thinking', content: 'Now edit.' });
  stream.send({ type: 'tool_call', id: 'call-2', name: 'edit', args: {} });

  assert.deepEqual(stream.items.map((item) => item.type), ['thinking', 'tool', 'thinking', 'tool']);
  assert.deepEqual(stream.items.filter((item) => item.type === 'thinking').map((item) => item.text),
    ['Read first.', 'Now edit.']);
  assert.equal(stream.finished.length, 2);
});

test('the end of a stream settles a block that nothing followed', () => {
  const stream = createStream();
  stream.send({ type: 'thinking', content: 'Unfinished.' });
  handler.settleStreamThinking(stream.assistantIdRef, stream.threadRef, stream.callbacks);
  handler.settleStreamThinking(stream.assistantIdRef, stream.threadRef, stream.callbacks);

  assert.equal(stream.finished.length, 1);
  assert.equal(stream.assistantIdRef.thinkingId, null);
});

test('empty thinking deltas leave the placeholder in place', () => {
  const stream = createStream();
  stream.send({ type: 'thinking', content: '' });

  assert.deepEqual(stream.items.map((item) => item.type), ['message']);
});

test('thinking blocks separate tool groups and empty blocks stay hidden', () => {
  const view = activity.buildTranscriptActivityView([
    { id: 1, type: 'message', role: 'user', text: 'go' },
    { id: 2, type: 'thinking', text: 'plan' },
    { id: 3, type: 'tool', name: 'read', args: {}, result: 'ok' },
    { id: 4, type: 'thinking', text: '   ' },
    { id: 5, type: 'tool', name: 'grep', args: {}, result: 'ok' },
    { id: 6, type: 'thinking', text: 'edit next' },
    { id: 7, type: 'tool', name: 'edit', args: {}, result: 'ok' },
    { id: 8, type: 'message', role: 'assistant', text: 'done' },
  ], false);

  assert.deepEqual(Array.from(view.keys), ['item-1', 'item-2', 'activity-3', 'item-6', 'activity-7', 'item-8']);
  assert.deepEqual(Array.from(view.entries.get('activity-3').items, (item) => item.id), [3, 5]);
  assert.equal(activity.isTranscriptActivityBoundary({ type: 'thinking', text: 'x' }), true);
  assert.equal(activity.isEmptyThinking({ type: 'thinking', text: '' }), true);
});

test('a trailing thinking block does not show the waiting indicator for tools', () => {
  const items = [
    { type: 'tool', name: 'read', args: {}, result: 'ok' },
    { type: 'thinking', text: 'next step' },
  ];
  assert.equal(activity.isTranscriptWaitingForModel(items, true), false);
});

test('history places thinking before the answer and reuses streamed durations', () => {
  durations.rememberThinkingDuration('thread-h', ' Plan the edit. ', 4200);
  const items = threads.toThreadTranscript([
    { id: 'u1', role: 'user', content: 'go' },
    {
      id: 'a1', role: 'assistant', content: '', thinking: 'Plan the edit.',
      tool_calls: [{ id: 'call-1', name: 'edit', args: {} }],
    },
    { id: 'a2', role: 'assistant', content: 'Done.', thinking: 'Check it worked.' },
    { id: 'a3', role: 'assistant', content: 'Plain.' },
  ], 'thread-h');

  assert.deepEqual(Array.from(items, (item) => item.type), ['message', 'thinking', 'tool', 'thinking', 'message', 'message']);
  assert.equal(items[1].durationMs, 4200);
  assert.equal(items[3].durationMs, undefined);
  assert.equal(items[4].text, 'Done.');
});

test('finished thinking labels round to readable durations', () => {
  assert.equal(durations.formatThinkingLabel(undefined), 'Thought');
  assert.equal(durations.formatThinkingLabel(400), 'Thought');
  assert.equal(durations.formatThinkingLabel(8400), 'Thought for 8s');
  assert.equal(durations.formatThinkingLabel(125000), 'Thought for 2m 5s');
  assert.equal(durations.formatThinkingLabel(120000), 'Thought for 2m');
});

test('a tool-free turn streams thinking above a single answer bubble', () => {
  const stream = createStream();
  stream.send({ type: 'thinking', content: 'Greet back.' });
  stream.send({ type: 'message', content: 'Hello' });
  stream.send({ type: 'message', content: ' there!' });
  stream.send({ type: 'final', content: 'Hello there!' });
  handler.settleStreamThinking(stream.assistantIdRef, stream.threadRef, stream.callbacks);

  assert.deepEqual(stream.items.map(({ type, text }) => ({ type, text })), [
    { type: 'thinking', text: 'Greet back.' },
    { type: 'message', text: 'Hello there!' },
  ]);
  assert.equal(stream.finished.length, 1);

  const rows = [{ id: 100, type: 'message', role: 'user', text: 'hi' }, ...stream.items];
  const answerId = stream.items[1].id;
  // Copy and regenerate stay on the answer, never on the thinking block.
  assert.equal(activity.findTranscriptAnswerId(rows), answerId);
  assert.deepEqual(Array.from(activity.findTranscriptCompletedAnswerIds(rows)), [answerId]);
  const view = activity.buildTranscriptActivityView(rows, false);
  assert.deepEqual(Array.from(view.keys), ['item-100', `item-${stream.items[0].id}`, `item-${answerId}`]);
});

test('a non-streaming provider sends thinking and the answer only at the end', () => {
  const stream = createStream();
  stream.send({ type: 'thinking', content: 'Whole plan.' });
  stream.send({ type: 'final', content: 'Whole answer.' });

  assert.deepEqual(stream.items.map(({ type, text }) => ({ type, text })), [
    { type: 'thinking', text: 'Whole plan.' },
    { type: 'message', text: 'Whole answer.' },
  ]);
  assert.equal(stream.finished.length, 1);
});

test('reasoning after visible text moves later text below it without repeating the answer', () => {
  const stream = createStream();
  stream.send({ type: 'message', content: 'Part one.' });
  stream.send({ type: 'thinking', content: 'Reconsider.' });
  stream.send({ type: 'message', content: ' Part two.' });
  stream.send({ type: 'final', content: 'Part one. Part two.' });

  assert.deepEqual(stream.items.map(({ type, text }) => ({ type, text })), [
    { type: 'message', text: 'Part one.' },
    { type: 'thinking', text: 'Reconsider.' },
    { type: 'message', text: ' Part two.' },
  ]);
});

test('a tool-free turn reloads from history with its thinking and duration', () => {
  durations.rememberThinkingDuration('thread-n', 'Greet back.', 2600);
  const items = threads.toThreadTranscript([
    { id: 'u1', role: 'user', content: 'hi' },
    { id: 'a1', role: 'assistant', content: 'Hello there!', thinking: 'Greet back.' },
  ], 'thread-n');

  assert.deepEqual(Array.from(items, ({ type, text }) => `${type}:${text}`),
    ['message:hi', 'thinking:Greet back.', 'message:Hello there!']);
  assert.equal(durations.formatThinkingLabel(items[1].durationMs), 'Thought for 3s');
});

test('an answer-less turn keeps only its thinking visible', () => {
  const items = threads.toThreadTranscript([
    { id: 'u1', role: 'user', content: 'hi' },
    { id: 'a1', role: 'assistant', content: '', thinking: 'Nothing to say.' },
  ], 'thread-empty');
  const rows = Array.from(items, (item, index) => ({ ...item, id: index + 1 }));
  const view = activity.buildTranscriptActivityView(rows, false);

  assert.deepEqual(Array.from(view.keys), ['item-1', 'item-2']);
  assert.equal(view.entries.get('item-2').item.type, 'thinking');
});
