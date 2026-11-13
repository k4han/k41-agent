import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compileFunction } from 'node:vm';
import test from 'node:test';
import * as solid from 'solid-js';
import ts from 'typescript';

function load(path, dependencies = {}) {
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  compileFunction(outputText, ['exports', 'require', 'AbortController'])(exports, name => {
    assert.ok(name in dependencies, `Unexpected dependency: ${name}`);
    return dependencies[name];
  }, AbortController);
  return exports;
}

const toolCallStatus = load('../src/lib/toolCallStatus.ts');
const toolGroupSummary = load('../src/lib/toolGroupSummary.ts', {
  '@/lib/toolCallStatus': toolCallStatus,
  '@/lib/shellToolResult': load('../src/lib/shellToolResult.ts'),
});
const { summarizeSubagent } = load('../src/lib/subagentSummary.ts', {
  '@/lib/chatStatus': load('../src/lib/chatStatus.ts'),
  '@/lib/toolCallStatus': toolCallStatus,
  '@/lib/toolCallSummary': load('../src/lib/toolCallSummary.ts'),
  '@/lib/toolGroupSummary': toolGroupSummary,
});
const activity = (patch = {}) => ({
  delegation_id: 'child', agent_name: 'Kode', parent_thread_id: 'parent',
  thread_id: 'child-thread', tool_call_id: 'delegate', status: 'running', ...patch,
});
const tool = (id, name, patch = {}) => ({
  type: 'tool', tool_call_id: id, name, args: {}, result: null, ...patch,
});

test('parallel tools show the most recently started pending call and remaining count', () => {
  const items = [
    tool('read', 'read_file', { result: 'source' }),
    tool('first', 'bash', { args: { command: 'pnpm check' } }),
    tool('latest', 'bash', { args: { command: 'pnpm test' } }),
  ];
  const result = summarizeSubagent(activity(), items);
  assert.equal(result.summary.fullLabel, '3 tools: 1 read, 2 commands');
  assert.equal(result.detail, 'bash pnpm test · +1');
  assert.equal(result.runningCount, 2);
  items[2].resultReceived = true;
  assert.equal(summarizeSubagent(activity(), items).detail, 'bash pnpm check');
});

test('finished calls retain the latest tool detail while the agent remains active', () => {
  const result = summarizeSubagent(activity(), [
    tool('completed', 'read_file', { executionStatus: 'completed' }),
    tool('failed', 'bash', { executionStatus: 'failed' }),
    tool('empty', 'write_file', { resultReceived: true }),
    tool('legacy', 'edit_file', { result: '' }),
  ]);
  assert.equal(result.runningCount, 0);
  assert.equal(result.detail, 'Last tool: edit');
  assert.equal(result.summary.totalFailed, 1);
  assert.equal(result.live, true);
});

test('terminal agent statuses override calls without a result', () => {
  for (const [status, detail] of Object.entries({
    completed: 'Completed', failed: 'Failed', stopped: 'Stopped', interrupted: 'Interrupted',
  })) {
    const result = summarizeSubagent(activity({ status }), [tool('pending', 'bash')]);
    assert.equal(result.runningCount, 0);
    assert.equal(result.detail, detail);
    assert.equal(result.live, false);
  }
  assert.equal(summarizeSubagent(activity({ status: 'started' }), []).detail, 'Starting...');
  assert.equal(summarizeSubagent(activity(), []).detail, 'Working...');
  assert.equal(summarizeSubagent(activity(), []).summary.fullLabel, '0 tools');
});

test('counts remain local to each agent and completed nested agents are not pending tools', () => {
  const nested = tool('nested', 'delegate_task', {
    subagent: activity({ status: 'completed', items: [tool('nested-read', 'read_file')] }),
  });
  const result = summarizeSubagent(activity(), [nested, {
    type: 'message', role: 'assistant', text: 'Reviewing the results.',
  }]);
  assert.equal(result.summary.total, 1);
  assert.equal(result.summary.fullLabel, '1 tool: 1 delegate task');
  assert.equal(result.runningCount, 0);
  assert.equal(result.detail, 'Latest update: Reviewing the results.');
});

test('quick tool calls remain visible after their results arrive', () => {
  const items = [tool('fast', 'bash', { args: { command: 'pnpm check' }, executionStatus: 'running' })];
  assert.equal(summarizeSubagent(activity(), items).detail, 'bash pnpm check');
  items[0].resultReceived = true;
  items[0].executionStatus = 'completed';
  assert.equal(summarizeSubagent(activity(), items).detail, 'Last tool: bash pnpm check');
  items.push({ type: 'thinking', text: '  ' });
  items.push({ type: 'message', role: 'assistant', text: 'Thinking...' });
  assert.equal(summarizeSubagent(activity(), items).detail, 'Last tool: bash pnpm check');
});

test('actual reasoning replaces the last tool with its most recent paragraph', () => {
  const items = [
    tool('read', 'read_file', { result: 'source' }),
    { type: 'thinking', text: 'Inspect the files.\n\nNow checking\n the imports.  ' },
  ];
  assert.equal(summarizeSubagent(activity(), items).detail, 'Thinking: Now checking the imports.');
  items[1].durationMs = 100;
  assert.equal(summarizeSubagent(activity(), items).detail, 'Last thought: Now checking the imports.');
  items.push(tool('test', 'bash', { args: { command: 'pnpm test' }, result: '' }));
  assert.equal(summarizeSubagent(activity(), items).detail, 'Last tool: bash pnpm test');
  assert.equal(summarizeSubagent(activity({ status: 'completed' }), items).detail, 'Completed');
});

test('active tools take precedence over older reasoning and failed tools keep their details', () => {
  const items = [
    { type: 'thinking', text: 'Run the checks.', durationMs: 10 },
    tool('test', 'bash', { args: { command: 'pnpm test' }, executionStatus: 'running' }),
  ];
  assert.equal(summarizeSubagent(activity(), items).detail, 'bash pnpm test');
  items[1].executionStatus = 'failed';
  assert.equal(summarizeSubagent(activity(), items).detail, 'Failed tool: bash pnpm test');
});

test('repeated projections of a call count once while separate calls to the same tool count separately', () => {
  const result = summarizeSubagent(activity({ status: 'completed' }), [
    tool('same', 'read_file'), tool('other', 'read_file', { result: 'ok' }),
    tool('same', 'read_file', { result: '[error] not_found: missing file' }),
  ]);
  assert.equal(result.summary.total, 2);
  assert.equal(result.summary.totalFailed, 1);
  assert.equal(result.summary.breakdownText, '2 reads (1 failed)');
});

const tick = () => new Promise(resolve => setImmediate(resolve));

function historyFixture(t, initial) {
  const requests = [];
  const conversions = [];
  const { useSubagentHistory } = load('../src/lib/useSubagentHistory.ts', {
    'solid-js': solid,
    '@/lib/api': { apiFetch: (path, init) => new Promise((resolve, reject) => {
      requests.push({ path, signal: init.signal, resolve, reject });
    }) },
    '@/lib/chatThreads': { toThreadTranscript: (messages, threadId) => {
      conversions.push(threadId);
      return messages.map(message => ({ type: 'message', role: message.role, text: message.content }));
    } },
  });
  let setActivity;
  let dispose;
  const history = solid.createRoot(cleanup => {
    dispose = cleanup;
    const [current, update] = solid.createSignal(initial);
    setActivity = update;
    return useSubagentHistory(current);
  });
  t.after(dispose);
  return { history, requests, conversions, setActivity, dispose };
}

test('saved history loads without expansion and subsequent reads reuse it', async t => {
  const { history, requests, conversions } = historyFixture(t, activity({ status: 'completed' }));
  assert.equal(requests.length, 1);
  assert.equal(history.ready(), false);
  assert.equal(history.loading(), true);
  requests[0].resolve({ messages: [{ role: 'assistant', content: 'Saved answer' }] });
  await tick();
  assert.equal(history.ready(), true);
  assert.equal(history.loading(), false);
  assert.equal(history.items()[0].text, 'Saved answer');
  assert.deepEqual(conversions, ['child-thread']);
  await history.retry();
  assert.equal(requests.length, 1);
});

test('an explicit empty live snapshot is known zero tools and does not fetch history', t => {
  const { history, requests } = historyFixture(t, activity({ items: [] }));
  assert.equal(history.ready(), true);
  assert.deepEqual(history.items(), []);
  assert.equal(requests.length, 0);
});

test('history errors remain retryable without an automatic request loop', async t => {
  const { history, requests } = historyFixture(t, activity());
  requests[0].reject(new Error('History unavailable'));
  await tick();
  assert.equal(history.error(), 'History unavailable');
  assert.equal(history.ready(), false);
  assert.equal(history.loading(), false);
  assert.equal(requests.length, 1);
  const retry = history.retry();
  assert.equal(history.error(), '');
  assert.equal(requests.length, 2);
  requests[1].resolve({ messages: [] });
  await retry;
  assert.equal(history.ready(), true);
});

test('switching threads cancels the old request and ignores its late response', async t => {
  const f = historyFixture(t, activity());
  f.setActivity(activity({ parent_thread_id: 'other/parent', thread_id: 'other-child' }));
  assert.equal(f.requests.length, 2);
  assert.equal(f.requests[0].signal.aborted, true);
  assert.equal(f.requests[1].path, '/v2/api/chat/other%2Fparent/subagents/child');
  f.requests[0].resolve({ messages: [{ role: 'assistant', content: 'Stale answer' }] });
  await tick();
  assert.equal(f.history.ready(), false);
  assert.equal(f.history.loading(), true);
  assert.deepEqual(f.history.items(), []);
  f.requests[1].resolve({ messages: [{ role: 'assistant', content: 'Current answer' }] });
  await tick();
  assert.equal(f.history.items()[0].text, 'Current answer');
  assert.deepEqual(f.conversions, ['other-child']);
});

test('live snapshots replace fetched history and cancel outstanding history requests', async t => {
  const f = historyFixture(t, activity());
  const liveItems = [tool('live', 'bash')];
  f.setActivity(activity({ items: liveItems }));
  assert.equal(f.requests[0].signal.aborted, true);
  assert.equal(f.history.items(), liveItems);
  assert.equal(f.history.loading(), false);
  f.requests[0].reject(new Error('Late request error'));
  await tick();
  assert.equal(f.history.error(), '');
  assert.equal(f.history.items(), liveItems);
  assert.equal(f.requests.length, 1);
});

test('an empty live snapshot takes precedence over already loaded history', async t => {
  const f = historyFixture(t, activity());
  f.requests[0].resolve({ messages: [{ role: 'assistant', content: 'Saved answer' }] });
  await tick();
  f.setActivity(activity({ items: [] }));
  assert.deepEqual(f.history.items(), []);
  assert.equal(f.requests.length, 1);
});

test('unmounting aborts history requests and ignores late completion', async t => {
  const f = historyFixture(t, activity());
  f.dispose();
  assert.equal(f.requests[0].signal.aborted, true);
  f.requests[0].resolve({ messages: [{ role: 'assistant', content: 'Late answer' }] });
  await tick();
  assert.deepEqual(f.conversions, []);
});
