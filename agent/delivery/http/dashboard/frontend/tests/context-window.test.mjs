import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import * as solid from 'solid-js/dist/solid.js';
import * as solidStore from 'solid-js/store/dist/store.js';
import ts from 'typescript';

function loadModule(path, dependencies = {}) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  runInNewContext(outputText, {
    exports, console, structuredClone,
    require: (name) => {
      assert.ok(name in dependencies, `Unexpected dependency: ${name}`);
      return dependencies[name];
    },
  });
  return exports;
}

function createFixture(t, { streaming = true, payload, provider = '', model = '', card } = {}) {
  const utils = loadModule('../src/lib/utils.ts');
  const store = loadModule('../src/lib/chatStreamStore.ts', { 'solid-js': solid, 'solid-js/store': solidStore, '@/lib/utils': utils, '@/components/Transcript': {} });
  const types = loadModule('../src/types.ts');
  const requests = [];
  const hookModule = loadModule('../src/lib/useContextWindow.ts', {
    'solid-js': solid,
    '@/lib/chatStreamStore': store,
    '@/lib/modelSelection': loadModule('../src/lib/modelSelection.ts'),
    '@/types': types,
    '@/lib/api': {
      apiFetch: (url) => new Promise((resolve) => requests.push({ url, resolve })),
    },
  });
  const handler = loadModule('../src/lib/chatStreamHandler.ts', {
    '@/components/Transcript': {},
    '@/lib/eventConstants': loadModule('../src/lib/eventConstants.ts'),
    '@/lib/generatedImages': {},
    '@/lib/userInputRequest': {},
    '@/types': types,
  });
  store.getOrCreateStreamSignals('thread-a');
  let setThreadId, setStreaming, setModel, control;
  solid.createRoot((dispose) => {
    t.after(dispose);
    const [threadId, updateThreadId] = solid.createSignal('thread-a');
    const [isStreaming, updateStreaming] = solid.createSignal(streaming);
    const [activeModel, updateModel] = solid.createSignal(model);
    setThreadId = updateThreadId;
    setStreaming = updateStreaming;
    setModel = updateModel;
    control = hookModule.useContextWindow({
      getCurrentThreadId: threadId,
      getStreaming: isStreaming,
      getSelectedCard: () => card,
      getData: () => payload,
      getProvider: () => provider,
      getModel: activeModel,
    });
  });
  const dispatch = (tokens, limit = 32000, threadId = 'thread-a', estimated = false, breakdown, usage = {}) => {
    handler.handleStreamEvent({
      type: 'context_usage', current_context_tokens: tokens,
      context_window: limit, estimated,
      context_breakdown: breakdown,
      ...usage,
    }, { id: null }, { received: false }, { id: threadId, message: '' }, {
      onContextUsage: control.updateContextUsage,
    });
  };
  return { control, dispatch, requests, setThreadId, setStreaming, setModel, store };
}

const flushAsync = () => new Promise((resolve) => setImmediate(resolve));
const compactedBreakdown = {
  system_prompt: 600, system_tools: 400, skills: 200, subagents: 100,
  user_messages: 300, agent_responses: 300, tool_calls: 100,
};

test('only provider reports update the indicator after each model call', (t) => {
  const { control, dispatch, requests } = createFixture(t);
  assert.equal(control.contextWindowData().formattedUsed, '—');
  assert.equal(control.contextWindowData().hasReportedUsage, false);
  dispatch(8000, 32000, 'thread-a', true);
  assert.equal(control.contextWindowData().formattedUsed, '—');
  dispatch(7900);
  assert.equal(control.contextWindowData().totalTokens, 7900);
  assert.equal(control.contextWindowData().formattedUsed, '7.90K');
  assert.equal(control.contextWindowData().hasReportedUsage, true);
  assert.equal(control.contextWindowData().maxTokens, 32000);
  dispatch(19000, 32000, 'thread-a', true);
  assert.equal(control.contextWindowData().totalTokens, 7900);
  dispatch(6900);
  assert.equal(control.contextWindowData().totalTokens, 6900);
  assert.equal(requests.length, 0);
});

test('invalid context events do not replace the current indicator', (t) => {
  const { control, dispatch } = createFixture(t);
  dispatch(8000);
  for (const tokens of [-1, NaN, Infinity, '1000', null]) {
    dispatch(tokens);
  }
  for (const limit of [0, -1, NaN, Infinity, '32000', null]) {
    dispatch(1000, limit);
  }
  assert.equal(control.contextWindowData().totalTokens, 8000);
});

test('updates stay with their stream when the user switches conversations', (t) => {
  const { control, dispatch, store, setThreadId } = createFixture(t);
  store.getOrCreateStreamSignals('thread-b');
  dispatch(7900);
  dispatch(15500, 64000, 'thread-b');
  assert.equal(control.contextWindowData().totalTokens, 7900);
  setThreadId('thread-b');
  assert.equal(control.contextWindowData().totalTokens, 15500);
  assert.equal(control.contextWindowData().maxTokens, 64000);
  dispatch(8900, 32000, 'thread-a');
  assert.equal(control.contextWindowData().totalTokens, 15500);
  setThreadId('thread-a');
  assert.equal(control.contextWindowData().totalTokens, 8900);
});

test('a delayed usage response cannot overwrite a newer model call', async (t) => {
  const { control, dispatch, requests, setStreaming } = createFixture(t, { streaming: false });
  assert.equal(requests.length, 1);
  setStreaming(true);
  dispatch(8000);
  requests[0].resolve({ thread_id: 'thread-a', latest_input_tokens: 100 });
  await flushAsync();
  assert.equal(control.contextWindowData().totalTokens, 8000);
  setStreaming(false);
  assert.equal(requests.length, 2);
  dispatch(18900);
  requests[1].resolve({ thread_id: 'thread-a', latest_input_tokens: 8000 });
  await flushAsync();
  assert.equal(control.contextWindowData().totalTokens, 18900);
  assert.equal(requests.length, 2);
});

test('finished streams retain the latest provider report while stored usage catches up', async (t) => {
  const { control, dispatch, requests, setStreaming, store } = createFixture(t);
  dispatch(8100);
  setStreaming(false);
  store.cleanupStreamSignals('thread-a');
  requests[0].resolve({ thread_id: 'thread-a', latest_input_tokens: 100 });
  await flushAsync();
  assert.equal(control.contextWindowData().totalTokens, 8100);
  assert.equal(control.contextWindowData().formattedUsed, '8.10K');
});

test('compacted context and history persist when revisiting the conversation', async (t) => {
  const { control, dispatch, requests, setStreaming, setThreadId, store } = createFixture(t);
  const chatModule = loadModule('../src/lib/useChatStreams.ts', {
    'solid-js': solid,
    '@/lib/chatStreamStore': store,
    '@/components/Transcript': {},
    '@/lib/generatedImages': {},
  });
  let chat;
  solid.createRoot((dispose) => {
    t.after(dispose);
    chat = chatModule.useChatStreams({
      scroll: {}, getCurrentThreadId: () => 'thread-a', getIsUnmounting: () => false,
    });
  });
  dispatch(8100);
  setStreaming(false);
  store.cleanupStreamSignals('thread-a');
  requests[0].resolve({ thread_id: 'thread-a', latest_input_tokens: 8000, latest_output_tokens: 100 });
  await flushAsync();
  assert.equal(control.contextWindowData().totalTokens, 8100);

  const compactedItems = [{ id: 1, type: 'message', role: 'assistant', text: 'Compacted history' }];
  chat.setItems(compactedItems, 'thread-a');
  control.updateCompactedContextUsage(2000, 'thread-a', compactedBreakdown);
  assert.equal(control.contextWindowData().hasReportedUsage, false);
  assert.equal(control.contextWindowData().estimated, true);
  assert.equal(control.contextWindowData().totalTokens, 2000);
  assert.equal(control.contextWindowData().formattedUsed, '~2.00K');
  assert.equal(control.contextWindowData().maxTokens, 32000);
  assert.equal(control.contextWindowData().totalPercent, 6.25);
  assert.equal(control.contextWindowData().categories[0].formattedValue, '600 tokens (1.9%)');
  assert.equal(control.contextWindowData().categories.reduce((sum, category) => sum + category.tokens, 0), 2000);

  setThreadId('thread-b');
  chat.setItems([]);
  setThreadId('thread-a');
  chat.setCurrentStreamThreadId(store.hasPersistedStream('thread-a') ? 'thread-a' : null);
  if (!store.hasPersistedStream('thread-a')) {
    chat.setItems(compactedItems);
  }
  assert.deepEqual(Array.from(chat.items(), item => solidStore.unwrap(item)), compactedItems);
  assert.equal(control.contextWindowData().totalTokens, 2000);
  assert.ok(control.contextWindowData().categories.every((category) => category.tokens !== null));
});

test('compaction replaces the old report and ignores delayed pre-compaction usage', async (t) => {
  const { control, dispatch, requests } = createFixture(t, { streaming: false });
  dispatch(8100, 32000, 'thread-a', false, breakdown);
  control.updateCompactedContextUsage(2000, 'thread-a');
  requests[0].resolve({ thread_id: 'thread-a', latest_input_tokens: 8000, latest_output_tokens: 100 });
  await flushAsync();
  assert.equal(control.contextWindowData().totalTokens, 2000);
  assert.ok(control.contextWindowData().categories.every((category) => category.tokens === null));

  const refresh = control.refreshThreadUsage('thread-a');
  requests[1].resolve({
    thread_id: 'thread-a', has_context_usage: false,
    context_estimated: true, current_context_tokens: 2000, latest_input_tokens: 0,
  });
  await refresh;
  assert.equal(control.contextWindowData().formattedUsed, '~2.00K');
  dispatch(2600, 32000, 'thread-a', false, undefined, { input_tokens: 2500, output_tokens: 100 });
  assert.equal(control.contextWindowData().totalTokens, 2600);
  assert.equal(control.contextWindowData().estimated, false);
  assert.equal(control.contextWindowData().hasReportedUsage, true);
});

test('reloaded conversations display the persisted compaction estimate', async (t) => {
  const { control, requests, dispatch } = createFixture(t, { streaming: false });
  requests[0].resolve({
    thread_id: 'thread-a', has_context_usage: false,
    context_estimated: true, current_context_tokens: 2000,
    latest_input_tokens: 0, latest_output_tokens: 0,
    context_breakdown: compactedBreakdown,
  });
  await flushAsync();
  assert.equal(control.contextWindowData().totalTokens, 2000);
  assert.equal(control.contextWindowData().formattedUsed, '~2.00K');
  assert.equal(control.contextWindowData().estimated, true);
  assert.equal(control.contextWindowData().categories[0].tokens, 600);
  assert.equal(control.contextWindowData().categories[4].tokens, 300);
  assert.equal(control.contextWindowData().categories[6].tokens, 100);
  dispatch(2600);
  assert.equal(control.contextWindowData().totalTokens, 2600);
  assert.equal(control.contextWindowData().estimated, false);
});

test('refresh fills in a missing legacy compaction breakdown', async (t) => {
  const { control, dispatch, requests } = createFixture(t, { streaming: false });
  dispatch(8100, 32000, 'thread-a', false, breakdown);
  control.updateCompactedContextUsage(700, 'thread-a');
  const refresh = control.refreshThreadUsage('thread-a');
  requests[1].resolve({
    thread_id: 'thread-a', has_context_usage: false,
    context_estimated: true, current_context_tokens: 2000,
    latest_input_tokens: 0, context_breakdown: compactedBreakdown,
  });
  await refresh;
  const data = control.contextWindowData();
  assert.equal(data.totalTokens, 2000);
  assert.equal(data.formattedUsed, '~2.00K');
  assert.equal(data.categories[0].tokens, 600);
  assert.equal(data.categories[4].tokens, 300);
  assert.equal(data.categories[5].tokens, 300);
  assert.equal(data.categories.reduce((sum, category) => sum + category.tokens, 0), data.totalTokens);
});

test('repeated compaction replaces categories and shows valid zeros', (t) => {
  const { control, dispatch } = createFixture(t);
  dispatch(8100, 32000, 'thread-a', false, breakdown);
  control.updateCompactedContextUsage(2000, 'thread-a', compactedBreakdown);
  const nextBreakdown = { ...compactedBreakdown, user_messages: 100, agent_responses: 100, tool_calls: 0 };
  control.updateCompactedContextUsage(1500, 'thread-a', nextBreakdown);
  const data = control.contextWindowData();
  assert.equal(data.totalTokens, 1500);
  assert.equal(data.categories[4].tokens, 100);
  assert.equal(data.categories[5].tokens, 100);
  assert.equal(data.categories[6].formattedValue, '0 tokens (0.0%)');
  assert.equal(data.categories.reduce((sum, category) => sum + category.tokens, 0), data.totalTokens);
});

test('a refreshed provider report supersedes the local compaction estimate', async (t) => {
  const { control, dispatch, requests, store } = createFixture(t, { streaming: false });
  dispatch(8100);
  store.cleanupStreamSignals('thread-a');
  control.updateCompactedContextUsage(2000, 'thread-a');
  assert.equal(store.hasPersistedStream('thread-a'), false);
  const refresh = control.refreshThreadUsage('thread-a');
  requests[1].resolve({
    thread_id: 'thread-a', has_context_usage: true, context_estimated: false,
    latest_input_tokens: 2500, latest_output_tokens: 100,
  });
  await refresh;
  assert.equal(control.contextWindowData().totalTokens, 2600);
  assert.equal(control.contextWindowData().formattedUsed, '2.60K');
  assert.equal(control.contextWindowData().estimated, false);
  assert.equal(control.contextWindowData().maxTokens, 32000);
  assert.equal(store.hasPersistedStream('thread-a'), false);
});

test('invalid compaction counts do not become zero or measured usage', (t) => {
  const { control, dispatch } = createFixture(t);
  for (const tokens of [undefined, null, -1, NaN, Infinity, '2000', 1.5]) {
    dispatch(8100);
    control.updateCompactedContextUsage(tokens, 'thread-a');
    assert.equal(control.contextWindowData().formattedUsed, '—');
    assert.equal(control.contextWindowData().estimated, false);
  }
  control.updateCompactedContextUsage(0, 'thread-a');
  assert.equal(control.contextWindowData().formattedUsed, '~0');
  assert.equal(control.contextWindowData().estimated, true);
});

test('clearing context for an existing stream keeps its transcript', (t) => {
  const { control, dispatch, store } = createFixture(t);
  const stream = store.persistedStreams.get('thread-a');
  const items = [{ id: 1, type: 'message', role: 'assistant', text: 'Retained history' }];
  stream.items[1](items);
  dispatch(8100);
  control.clearContextUsage('thread-a');
  assert.equal(control.contextWindowData().hasReportedUsage, false);
  assert.deepEqual(Array.from(stream.items[0](), item => solidStore.unwrap(item)), items);
});

test('a usage response for a previous conversation is ignored', async (t) => {
  const { control, dispatch, requests, setThreadId } = createFixture(t, { streaming: false });
  setThreadId('thread-b');
  dispatch(11900, 64000, 'thread-b');
  requests[0].resolve({ thread_id: 'thread-a', latest_input_tokens: 100 });
  requests[1].resolve({ thread_id: 'thread-b', latest_input_tokens: 200 });
  await flushAsync();
  assert.equal(control.contextWindowData().totalTokens, 11900);
  assert.equal(control.contextWindowData().maxTokens, 64000);
});

test('checkpoint estimates never become reported tokens', async (t) => {
  const { control, requests } = createFixture(t, { streaming: false });
  requests[0].resolve({
    thread_id: 'thread-a', latest_input_tokens: 8100, has_context_usage: true,
    current_context_tokens: 2000, estimated_context_tokens: 2000,
  });
  await flushAsync();
  assert.equal(control.contextWindowData().totalTokens, 8100);
});

test('missing provider usage stays unavailable even when the API supplies a zero fallback', async (t) => {
  const { control, requests } = createFixture(t, { streaming: false });
  requests[0].resolve({
    thread_id: 'thread-a', latest_input_tokens: 0, has_context_usage: false,
    estimated_context_tokens: 2000,
  });
  await flushAsync();
  assert.equal(control.contextWindowData().formattedUsed, '—');
  assert.equal(control.contextWindowData().hasReportedUsage, false);
});

test('a provider-reported zero is displayed as a measured value', (t) => {
  const { control, dispatch } = createFixture(t);
  dispatch(0);
  assert.equal(control.contextWindowData().formattedUsed, '0');
  assert.equal(control.contextWindowData().hasReportedUsage, true);
});

const breakdown = {
  system_prompt: 2000, system_tools: 1000, skills: 500, subagents: 500,
  user_messages: 1000, agent_responses: 1000, tool_calls: 2000,
};

test('all seven categories show tokens and percentage of context capacity', (t) => {
  const { control, dispatch } = createFixture(t);
  dispatch(8000, 32000, 'thread-a', false, breakdown);
  const categories = control.contextWindowData().categories;
  assert.deepEqual(Array.from(categories, (category) => category.label), [
    'System prompt', 'System tools', 'Skills', 'Subagents',
    'User messages', 'Agent responses', 'Tool calls',
  ]);
  assert.equal(categories[0].formattedValue, '2.00k tokens (6.3%)');
  assert.equal(categories[2].formattedValue, '500 tokens (1.6%)');
  assert.equal(categories.reduce((sum, category) => sum + category.tokens, 0), 8000);
});

test('stored breakdown is restored when a conversation is reloaded', async (t) => {
  const { control, requests } = createFixture(t, { streaming: false });
  requests[0].resolve({
    thread_id: 'thread-a', latest_input_tokens: 8000, has_context_usage: true,
    context_breakdown: breakdown,
  });
  await flushAsync();
  assert.equal(control.contextWindowData().categories[6].tokens, 2000);
});

test('the first completed response is included immediately and survives delayed stored usage', async (t) => {
  const { control, dispatch, requests, setStreaming } = createFixture(t);
  const inputBreakdown = { ...breakdown, agent_responses: 0, tool_calls: 0 };
  const completedBreakdown = { ...inputBreakdown, agent_responses: 300 };
  dispatch(5300, 32000, 'thread-a', false, completedBreakdown, {
    input_tokens: 5000, output_tokens: 300,
  });
  assert.equal(control.contextWindowData().categories[5].tokens, 300);
  assert.equal(control.contextWindowData().inputTokens, 5000);
  assert.equal(control.contextWindowData().outputTokens, 300);
  assert.equal(control.contextWindowData().totalTokens, 5300);
  assert.equal(control.contextWindowData().categories.reduce((sum, category) => sum + category.tokens, 0), 5300);
  setStreaming(false);
  requests[0].resolve({
    thread_id: 'thread-a', latest_input_tokens: 100, latest_output_tokens: 10,
    context_breakdown: inputBreakdown,
  });
  await flushAsync();
  assert.equal(control.contextWindowData().categories[5].tokens, 300);
  assert.equal(control.contextWindowData().totalTokens, 5300);
  assert.equal(control.contextWindowData().outputTokens, 300);
});

test('reloaded context includes the latest response without adding all past output tokens', async (t) => {
  const { control, requests } = createFixture(t, { streaming: false });
  requests[0].resolve({
    thread_id: 'thread-a', has_context_usage: true,
    latest_input_tokens: 5000, latest_output_tokens: 300,
    current_context_tokens: 5300, output_tokens: 9999,
    context_breakdown: { ...breakdown, agent_responses: 300, tool_calls: 0 },
  });
  await flushAsync();
  assert.equal(control.contextWindowData().categories[5].tokens, 300);
  assert.equal(control.contextWindowData().totalTokens, 5300);
  assert.equal(control.contextWindowData().inputTokens, 5000);
  assert.equal(control.contextWindowData().outputTokens, 300);
});

test('a new model call replaces the previous response tokens', (t) => {
  const { control, dispatch } = createFixture(t);
  dispatch(5300, 32000, 'thread-a', false, undefined, { input_tokens: 5000, output_tokens: 300 });
  dispatch(6000, 32000, 'thread-a', false, undefined, { input_tokens: 5500, output_tokens: 500 });
  assert.equal(control.contextWindowData().totalTokens, 6000);
  assert.equal(control.contextWindowData().inputTokens, 5500);
  assert.equal(control.contextWindowData().outputTokens, 500);
});

test('a newer report without breakdown never reuses categories from an older call', async (t) => {
  const { control, requests, dispatch } = createFixture(t, { streaming: false });
  requests[0].resolve({ thread_id: 'thread-a', latest_input_tokens: 8000, context_breakdown: breakdown });
  await flushAsync();
  dispatch(1000);
  assert.equal(control.contextWindowData().totalTokens, 1000);
  assert.ok(control.contextWindowData().categories.every((category) => category.formattedValue === '—'));
});

test('invalid breakdowns stay unavailable while valid zero categories show zero', (t) => {
  const { control, dispatch } = createFixture(t);
  for (const value of [null, {}, [], { ...breakdown, skills: -1 }, { ...breakdown, skills: '500' },
    { ...breakdown, skills: NaN }, { ...breakdown, skills: Infinity }]) {
    dispatch(8000, 32000, 'thread-a', false, value);
    assert.ok(control.contextWindowData().categories.every((category) => category.tokens === null));
  }
  dispatch(7500, 32000, 'thread-a', false, { ...breakdown, skills: 0 });
  assert.equal(control.contextWindowData().categories[2].formattedValue, '0 tokens (0.0%)');
});

test('breakdowns follow their conversation during streaming', (t) => {
  const { control, dispatch, setThreadId, store } = createFixture(t);
  store.getOrCreateStreamSignals('thread-b');
  dispatch(8000, 32000, 'thread-a', false, breakdown);
  dispatch(9000, 64000, 'thread-b', false, { ...breakdown, skills: 1500 });
  assert.equal(control.contextWindowData().categories[2].tokens, 500);
  setThreadId('thread-b');
  assert.equal(control.contextWindowData().categories[2].tokens, 1500);
});

const modelPayload = {
  default_provider: 'test', default_model: 'model-a',
  model_catalogs: [{ provider: 'test', default_model: 'model-a', models: [
    { id: 'model-a', context_window: 200000 }, { id: 'model-b', context_window: 32000 },
  ] }],
};
const before = '2026-10-18T01:00:00.000000+00:00';
const after = '2026-10-18T01:00:01.000000+00:00';

for (const model of ['', 'default', ' DEFAULT ', 'provider default']) {
  test(`reloaded context resolves the default model alias ${JSON.stringify(model)}`, async (t) => {
    const { control, requests } = createFixture(t, {
      streaming: false, payload: modelPayload, provider: ' Default ', model,
    });
    requests[0].resolve({ thread_id: 'thread-a', has_context_usage: true,
      latest_input_tokens: 50000, latest_output_tokens: 0 });
    await flushAsync();
    assert.equal(control.contextWindowData().maxTokens, 200000);
    assert.equal(control.contextWindowData().totalPercent, 25);
  });
}

test('an empty global default model uses the provider catalog default', (t) => {
  const { control } = createFixture(t, { payload: { ...modelPayload, default_model: '' }, model: 'default' });
  assert.equal(control.contextWindowData().maxTokens, 200000);
});

test('a newer stored snapshot replaces a completed live report', async (t) => {
  const { control, dispatch, requests, setStreaming, store } = createFixture(t);
  dispatch(8100, 32000, 'thread-a', false, breakdown, { context_updated_at: before });
  setStreaming(false);
  store.cleanupStreamSignals('thread-a');
  requests[0].resolve({ thread_id: 'thread-a', has_context_usage: true,
    context_updated_at: after, latest_input_tokens: 20000, latest_output_tokens: 100,
    context_breakdown: { ...breakdown, user_messages: 13100 },
  });
  await flushAsync();
  assert.equal(control.contextWindowData().totalTokens, 20100);
  assert.equal(control.contextWindowData().categories[4].tokens, 13100);
  assert.equal(store.hasPersistedStream('thread-a'), false);
});

test('an older stored snapshot cannot replace a completed live report', async (t) => {
  const { control, dispatch, requests, setStreaming } = createFixture(t);
  dispatch(20100, 32000, 'thread-a', false, undefined, { context_updated_at: after });
  setStreaming(false);
  requests[0].resolve({ thread_id: 'thread-a', has_context_usage: true,
    context_updated_at: before, latest_input_tokens: 8000, latest_output_tokens: 100,
  });
  await flushAsync();
  assert.equal(control.contextWindowData().totalTokens, 20100);
});

test('out of order usage requests retain the latest requested snapshot', async (t) => {
  const { control, requests } = createFixture(t, { streaming: false });
  const refresh = control.refreshThreadUsage('thread-a');
  requests[1].resolve({ thread_id: 'thread-a', has_context_usage: true, latest_input_tokens: 20000 });
  await refresh;
  requests[0].resolve({ thread_id: 'thread-a', has_context_usage: true, latest_input_tokens: 8000 });
  await flushAsync();
  assert.equal(control.contextWindowData().totalTokens, 20000);
});

test('a completed call without usage clears the previous report and ignores older stored usage', async (t) => {
  const { control, dispatch, requests, setStreaming } = createFixture(t);
  dispatch(8100, 32000, 'thread-a', false, breakdown, { context_updated_at: before });
  dispatch(0, 32000, 'thread-a', false, undefined, {
    has_context_usage: false, context_updated_at: after,
  });
  const data = control.contextWindowData();
  assert.equal(data.hasReportedUsage, false);
  assert.equal(data.estimated, false);
  assert.equal(data.formattedUsed, '—');
  assert.equal(data.totalTokens, 0);
  assert.ok(data.categories.every(category => category.tokens === null));
  setStreaming(false);
  requests[0].resolve({ thread_id: 'thread-a', has_context_usage: true,
    context_updated_at: before, latest_input_tokens: 8000, latest_output_tokens: 100,
  });
  await flushAsync();
  assert.equal(control.contextWindowData().formattedUsed, '—');
});

test('a newer stored call without usage invalidates a completed live report', async (t) => {
  const { control, dispatch, requests, setStreaming } = createFixture(t);
  dispatch(8100, 32000, 'thread-a', false, breakdown, { context_updated_at: before });
  setStreaming(false);
  requests[0].resolve({ thread_id: 'thread-a', has_context_usage: false,
    context_updated_at: after, latest_input_tokens: 0, latest_output_tokens: 0,
  });
  await flushAsync();
  assert.equal(control.contextWindowData().formattedUsed, '—');
  assert.ok(control.contextWindowData().categories.every(category => category.tokens === null));
});

test('a versioned compaction ignores older provider usage and accepts a later model call', async (t) => {
  const { control, dispatch, requests } = createFixture(t, { streaming: false });
  dispatch(8100, 32000, 'thread-a', false, breakdown, { context_updated_at: before });
  control.updateCompactedContextUsage(2000, 'thread-a', compactedBreakdown, {
    context_window: 32000, context_updated_at: after, reserved_tokens: 6000,
  });
  const stale = control.refreshThreadUsage('thread-a');
  requests[1].resolve({ thread_id: 'thread-a', has_context_usage: true,
    context_updated_at: before, latest_input_tokens: 8000, latest_output_tokens: 100,
  });
  await stale;
  assert.equal(control.contextWindowData().formattedUsed, '~2.00K');
  assert.equal(control.contextWindowData().reservedPercent, 18.75);
  const fresh = control.refreshThreadUsage('thread-a');
  requests[2].resolve({ thread_id: 'thread-a', has_context_usage: true,
    context_updated_at: '2026-10-18T01:00:02+00:00', latest_input_tokens: 2500, latest_output_tokens: 100,
  });
  await fresh;
  assert.equal(control.contextWindowData().totalTokens, 2600);
  assert.equal(control.contextWindowData().estimated, false);
});

test('a newer compaction from another session replaces the live report', async (t) => {
  const { control, dispatch, requests, setStreaming } = createFixture(t);
  dispatch(8100, 32000, 'thread-a', false, breakdown, { context_updated_at: before });
  setStreaming(false);
  requests[0].resolve({ thread_id: 'thread-a', has_context_usage: false,
    context_updated_at: after, context_estimated: true, current_context_tokens: 2000,
    context_breakdown: compactedBreakdown,
  });
  await flushAsync();
  assert.equal(control.contextWindowData().formattedUsed, '~2.00K');
  assert.equal(control.contextWindowData().categories[0].tokens, 600);
});

test('response reservations follow the runtime default for each context capacity', (t) => {
  const { control, dispatch } = createFixture(t);
  for (const [window, reserve] of [[32000, 4000], [128000, 4096], [200000, 4096]]) {
    dispatch(1000, window);
    assert.equal(control.contextWindowData().reservedPercent, reserve / window * 100);
  }
});

test('explicit runtime reservations survive streaming and reloading', async (t) => {
  const { control, dispatch } = createFixture(t);
  dispatch(1000, 32000, 'thread-a', false, undefined, { reserved_tokens: 6000 });
  assert.equal(control.contextWindowData().reservedPercent, 18.75);
  const reloaded = createFixture(t, { streaming: false });
  reloaded.requests[0].resolve({ thread_id: 'thread-a', has_context_usage: true,
    context_window: 32000, reserved_tokens: 6000, latest_input_tokens: 1000,
  });
  await flushAsync();
  assert.equal(reloaded.control.contextWindowData().maxTokens, 32000);
  assert.equal(reloaded.control.contextWindowData().reservedPercent, 18.75);
});

test('switching models recalculates capacity and drops the previous model reservation', (t) => {
  const { control, dispatch, setModel } = createFixture(t, { payload: modelPayload, model: 'model-a' });
  dispatch(8000, 200000, 'thread-a', false, undefined, {
    reserved_tokens: 12000, provider: 'test', model: 'model-a',
  });
  assert.equal(control.contextWindowData().reservedPercent, 6);
  setModel('model-b');
  assert.equal(control.contextWindowData().maxTokens, 32000);
  assert.equal(control.contextWindowData().totalPercent, 25);
  assert.equal(control.contextWindowData().reservedPercent, 12.5);
});
