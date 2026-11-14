import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compileFunction } from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

function loadModule(name, dependencies = {}) {
  const source = readFileSync(new URL(`../src/lib/${name}.ts`, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  compileFunction(outputText, ['require', 'exports'])(name => {
    assert.ok(name in dependencies, `Unexpected dependency: ${name}`);
    return dependencies[name];
  }, exports);
  return exports;
}

const endpoints = loadModule('endpoints');
const threadId = 'thread/with/slashes';
const message = id => ({ id, role: 'assistant', content: id });
const page = (messages, nextBefore = null) => ({
  thread_id: threadId, active_checkpoint_id: 'selected checkpoint', messages: messages.map(message),
  has_more: nextBefore !== null, next_before: nextBefore, total_messages: 5,
});
const history = apiFetch => loadModule('threadHistory', {
  '@/lib/api': { apiFetch }, '@/lib/endpoints': endpoints,
});

test('complete exports collect pages in order and pin all requests to the first checkpoint', async () => {
  const requests = [];
  const controller = new AbortController();
  const pages = [page(['d', 'e'], 3), page(['b', 'c'], 1), page(['a'])];
  const { fetchCompleteThread } = history(async (path, init) => {
    const url = new URL(path, 'http://localhost');
    requests.push(url);
    assert.equal(init.signal, controller.signal);
    return pages[requests.length - 1];
  });
  const result = await fetchCompleteThread(threadId, undefined, controller.signal);
  assert.deepEqual(result.messages.map(message => message.id), ['a', 'b', 'c', 'd', 'e']);
  assert.equal(result.has_more, false);
  assert.equal(result.next_before, null);
  assert.equal(result.active_checkpoint_id, 'selected checkpoint');
  assert.equal(requests.length, 3);
  assert.equal(requests[0].pathname, '/dashboard-api/chat-history/thread%2Fwith%2Fslashes');
  assert.equal(requests[0].search, '');
  assert.deepEqual(requests.slice(1).map(url => url.searchParams.get('checkpoint_id')),
    ['selected checkpoint', 'selected checkpoint']);
  assert.deepEqual(requests.slice(1).map(url => url.searchParams.get('before')), ['3', '1']);
  assert.deepEqual(pages[0].messages.map(message => message.id), ['d', 'e']);
});

test('single and empty pages finish without extra requests and preserve explicit branches', async () => {
  for (const messages of [[], ['single']]) {
    const calls = [];
    const { fetchCompleteThread } = history(async path => {
      calls.push(path);
      return page(messages);
    });
    const result = await fetchCompleteThread(threadId, 'older/branch');
    assert.deepEqual(result.messages.map(message => message.id), messages);
    assert.equal(calls.length, 1);
    assert.equal(new URL(calls[0], 'http://localhost').searchParams.get('checkpoint_id'), 'older/branch');
  }
});

test('earlier pages leave the current transcript unchanged until the caller applies them', async () => {
  const current = page(['d', 'e'], 3);
  const { fetchEarlierThreadPage } = history(async () => page(['b', 'c'], 1));
  const earlier = await fetchEarlierThreadPage(current);
  assert.deepEqual(earlier.messages.map(message => message.id), ['b', 'c']);
  assert.deepEqual(current.messages.map(message => message.id), ['d', 'e']);
});

test('invalid page responses stop exports instead of downloading incomplete or mixed history', async () => {
  for (const invalid of [
    { active_checkpoint_id: 'changed' }, { thread_id: 'other' }, { messages: [] },
    { next_before: 3 }, { next_before: 4 }, { next_before: 0 }, { next_before: -1 },
    { next_before: null },
  ]) {
    let calls = 0;
    const { fetchCompleteThread } = history(async () => ++calls === 1
      ? page(['d', 'e'], 3) : { ...page(['b', 'c'], 1), ...invalid });
    await assert.rejects(fetchCompleteThread(threadId), /history changed/);
    assert.equal(calls, 2);
  }
});

test('later page errors and cancellation propagate without producing a partial export', async () => {
  const controller = new AbortController();
  let calls = 0;
  const { fetchCompleteThread } = history(async (path, init) => {
    if (++calls === 1) return page(['d', 'e'], 3);
    controller.abort();
    init.signal.throwIfAborted();
  });
  await assert.rejects(fetchCompleteThread(threadId, undefined, controller.signal), { name: 'AbortError' });
  assert.equal(calls, 2);
  const failed = history(async path => {
    if (new URL(path, 'http://localhost').searchParams.has('before')) throw new Error('History unavailable');
    return page(['d', 'e'], 3);
  });
  await assert.rejects(failed.fetchCompleteThread(threadId), /History unavailable/);
});

test('regeneration loads older pages until the original prompt is available on the selected checkpoint', async () => {
  const current = { ...page(['answer'], 300), total_user_messages: 126 };
  current.messages[0].message_index = 300;
  const prompt = { id: 'prompt', role: 'user', content: 'Original question',
    message_index: 99, source_checkpoint_id: 'prompt checkpoint' };
  const earlier = [page(['tool-result'], 200), { ...page([], 99), messages: [prompt] }];
  const requests = [];
  const controller = new AbortController();
  const { fetchThreadForRegeneration } = history(async (path, init) => {
    const url = new URL(path, 'http://localhost');
    requests.push(url);
    assert.equal(init.signal, controller.signal);
    assert.equal(url.searchParams.get('checkpoint_id'), current.active_checkpoint_id);
    return earlier[requests.length - 1];
  });
  const result = await fetchThreadForRegeneration(current, 300, controller.signal);
  assert.deepEqual(requests.map(url => url.searchParams.get('before')), ['300', '200']);
  assert.deepEqual(result.messages.map(message => message.id), ['prompt', 'tool-result', 'answer']);
  assert.equal(result.has_more, true, 'older turns do not need to be loaded');
  assert.equal(result.total_user_messages, 126);
  assert.deepEqual(current.messages.map(message => message.id), ['answer']);
});

test('regeneration only uses editable prompts before the selected answer', async () => {
  const prompt = { id: 'prompt', role: 'user', content: 'Original question',
    message_index: 1, source_checkpoint_id: 'prompt checkpoint' };
  const current = { ...page(['answer'], 1), messages: [prompt, message('answer')] };
  const { fetchThreadForRegeneration } = history(async () => assert.fail('Unexpected history request'));
  assert.equal(await fetchThreadForRegeneration(current, 2), current);

  let calls = 0;
  const lookup = history(async () => {
    calls++;
    return { ...page([]), messages: [prompt] };
  });
  const later = { ...current, messages: [message('answer'), { ...prompt, message_index: 3 },
    { id: 'uneditable', role: 'user', content: 'Permission response' }] };
  const result = await lookup.fetchThreadForRegeneration(later, 2);
  assert.equal(calls, 1);
  assert.equal(result.messages[0], prompt);
});

test('regeneration leaves the current history untouched when earlier pages fail or are cancelled', async () => {
  const current = page(['answer'], 3);
  const failed = history(async () => { throw new Error('History unavailable'); });
  await assert.rejects(failed.fetchThreadForRegeneration(current), /History unavailable/);
  const controller = new AbortController();
  const cancelled = history(async (path, init) => {
    controller.abort();
    init.signal.throwIfAborted();
  });
  await assert.rejects(cancelled.fetchThreadForRegeneration(current, undefined, controller.signal), { name: 'AbortError' });
  assert.deepEqual(current.messages.map(message => message.id), ['answer']);
});
