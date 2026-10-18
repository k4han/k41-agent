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
    require: name => {
      assert.ok(name in dependencies, `Unexpected dependency: ${name}`);
      return dependencies[name];
    },
  });
  return exports;
}

const toolCallStatus = loadModule('../src/lib/toolCallStatus.ts');
const { buildTranscriptActivityView, findTranscriptAnswerId, findTranscriptResumeId, isTranscriptActivityBoundary, isTranscriptWaitingForModel } = loadModule(
  '../src/lib/transcriptActivity.ts',
  {
    '@/lib/htmlPreview': { HTML_PREVIEW_TOOL_NAME: 'html_preview' },
    '@/lib/toolCallStatus': toolCallStatus,
  },
);

const userMessage = (id, text = 'hello') => ({ id, type: 'message', role: 'user', text });
const assistantMessage = (id, text = 'answer', extra = {}) => ({
  id, type: 'message', role: 'assistant', text, ...extra,
});
const toolCall = (id, name = 'read', args = {}, result = null) => ({
  id, type: 'tool', name, args, result,
});
const planReview = (id, status = 'pending') => ({ id, type: 'plan_review', plan: 'plan', status });
const userInputRequest = (id) => ({
  id, type: 'user_input_request', questions: [], status: 'pending',
});

const groupIds = (view, key) =>
  Array.from(view.entries.get(key).items, item => item.id);
const rowKeys = (view) => Array.from(view.keys);

test('tool runs are independent groups and assistant messages retain chronological order', () => {
  const view = buildTranscriptActivityView([
    userMessage(1),
    toolCall(2),
    toolCall(3),
    assistantMessage(4, 'I checked the files.'),
    toolCall(5, 'edit'),
    toolCall(6, 'write'),
    assistantMessage(7, 'All done.'),
  ], false);
  assert.deepEqual(rowKeys(view), ['item-1', 'activity-2', 'item-4', 'activity-5', 'item-7']);
  assert.deepEqual(groupIds(view, 'activity-2'), [2, 3]);
  assert.deepEqual(groupIds(view, 'activity-5'), [5, 6]);
  assert.equal(view.entries.get('item-4').item.text, 'I checked the files.');
  assert.equal(view.entries.get('item-7').item.text, 'All done.');
  assert.equal(view.liveKey, null);
});

test('narration splits two tool runs into separate groups', () => {
  const view = buildTranscriptActivityView([
    toolCall(1, 'read'),
    assistantMessage(2, 'Now I will edit the file.'),
    toolCall(3, 'edit'),
    assistantMessage(4, 'File updated.'),
    toolCall(5, 'test'),
  ], false);
  assert.deepEqual(rowKeys(view), ['activity-1', 'item-2', 'activity-3', 'item-4', 'activity-5']);
  assert.equal(view.entries.get('item-2').item.text, 'Now I will edit the file.');
  assert.equal(view.entries.get('item-4').item.text, 'File updated.');
});

test('empty assistant messages do not split tools or appear inside groups', () => {
  const view = buildTranscriptActivityView([
    assistantMessage(1, ''),
    userMessage(2),
    toolCall(3),
    assistantMessage(4, ''),
    assistantMessage(5, ' \n\t ', { attachments: [] }),
    toolCall(6),
    assistantMessage(7, 'Done.'),
    assistantMessage(8, ''),
  ], false);
  assert.deepEqual(rowKeys(view), ['item-2', 'activity-3', 'item-7']);
  assert.deepEqual(groupIds(view, 'activity-3'), [3, 6]);
  assert.equal(view.liveKey, null);
});

test('an empty streaming message keeps the tool group live until visible text arrives', () => {
  const message = assistantMessage(3, ' \n ');
  const items = [toolCall(1), toolCall(2), message];
  let view = buildTranscriptActivityView(items, true);
  assert.deepEqual(rowKeys(view), ['activity-1']);
  assert.deepEqual(groupIds(view, 'activity-1'), [1, 2]);
  assert.equal(view.liveKey, 'activity-1');

  message.text += 'I checked the files.';
  view = buildTranscriptActivityView(items, true);
  assert.deepEqual(rowKeys(view), ['activity-1', 'item-3']);
  assert.equal(view.entries.get('item-3').item.id, 3);
  assert.equal(view.liveKey, null);

  view = buildTranscriptActivityView([...items, toolCall(4)], true);
  assert.deepEqual(rowKeys(view), ['activity-1', 'item-3', 'activity-4']);
  assert.equal(view.liveKey, 'activity-4');
});

test('image placeholders and attachments still split tools without message text', () => {
  const view = buildTranscriptActivityView([
    toolCall(1),
    assistantMessage(2, '', { generatedImagePending: true }),
    toolCall(3),
    assistantMessage(4, ' \n ', { attachments: [{ name: 'chart.png' }] }),
    toolCall(5),
  ], true);
  assert.deepEqual(rowKeys(view), ['activity-1', 'item-2', 'activity-3', 'item-4', 'activity-5']);
  assert.equal(view.liveKey, 'activity-5');
});

test('empty user messages still separate turns', () => {
  const view = buildTranscriptActivityView([
    toolCall(1), userMessage(2, ''), toolCall(3),
  ], false);
  assert.deepEqual(rowKeys(view), ['activity-1', 'item-2', 'activity-3']);
});

test('every turn keeps its narration and answer visible', () => {
  const view = buildTranscriptActivityView([
    userMessage(1),
    toolCall(2),
    assistantMessage(3, 'first answer'),
    userMessage(4),
    assistantMessage(5, 'second answer'),
  ], false);
  assert.deepEqual(rowKeys(view), ['item-1', 'activity-2', 'item-3', 'item-4', 'item-5']);
  assert.equal(view.entries.get('item-3').item.text, 'first answer');
  assert.equal(view.entries.get('item-5').item.text, 'second answer');
});

test('while streaming only the trailing tool run is live', () => {
  const items = [userMessage(1), toolCall(2), toolCall(3)];
  let view = buildTranscriptActivityView(items, true);
  assert.deepEqual(rowKeys(view), ['item-1', 'activity-2']);
  assert.equal(view.liveKey, 'activity-2');

  // Narration arrives in a fresh trailing message: the run is done.
  const withNarration = [...items, assistantMessage(4, 'partial')];
  view = buildTranscriptActivityView(withNarration, true);
  assert.deepEqual(rowKeys(view), ['item-1', 'activity-2', 'item-4']);
  assert.equal(view.entries.get('item-4').item.text, 'partial');
  assert.equal(view.liveKey, null);

  // The next tool run opens a new group instead of reusing the old one.
  view = buildTranscriptActivityView([...withNarration, toolCall(5, 'grep')], true);
  assert.deepEqual(rowKeys(view), ['item-1', 'activity-2', 'item-4', 'activity-5']);
  assert.equal(view.liveKey, 'activity-5');
  assert.deepEqual(groupIds(view, 'activity-2'), [2, 3]);
  assert.deepEqual(groupIds(view, 'activity-5'), [5]);
});

test('interactive and rich items stay visible and break the group', () => {
  const view = buildTranscriptActivityView([
    userMessage(1),
    toolCall(2),
    planReview(3),
    toolCall(4, 'html_preview'),
    assistantMessage(5, '', { attachments: [{ name: 'chart.png' }] }),
    toolCall(6, 'write'),
  ], false);
  assert.deepEqual(rowKeys(view), ['item-1', 'activity-2', 'item-3', 'item-4', 'item-5', 'activity-6']);
});

test('a group that is no longer at the tail is not live', () => {
  const view = buildTranscriptActivityView([userMessage(1), toolCall(2), planReview(3)], true);
  assert.deepEqual(rowKeys(view), ['item-1', 'activity-2', 'item-3']);
  assert.equal(view.liveKey, null);
});

test('appending a follow-up tool keeps the same group key', () => {
  const first = buildTranscriptActivityView([userMessage(1), toolCall(2)], true);
  const second = buildTranscriptActivityView([userMessage(1), toolCall(2), toolCall(3)], true);
  assert.deepEqual(rowKeys(first), ['item-1', 'activity-2']);
  assert.deepEqual(rowKeys(second), ['item-1', 'activity-2']);
  assert.deepEqual(groupIds(second, 'activity-2'), [2, 3]);
  assert.equal(second.liveKey, 'activity-2');
});

test('completed tools wait for the model while their group stays available for follow-up calls', () => {
  const items = [userMessage(1), toolCall(2, 'read', {}, 'contents')];
  const view = buildTranscriptActivityView(items, true);
  assert.equal(view.liveKey, 'activity-2');
  assert.equal(isTranscriptWaitingForModel(items, true), true);
  assert.equal(isTranscriptWaitingForModel([...items, assistantMessage(3, ' \n ')], true), true);
  const withNextTool = [...items, toolCall(3, 'read')];
  assert.equal(buildTranscriptActivityView(withNextTool, true).liveKey, view.liveKey);
  assert.equal(isTranscriptWaitingForModel(withNextTool, true), false);
  assert.equal(isTranscriptWaitingForModel([...items, assistantMessage(3, 'Answer')], true), false);
  assert.equal(isTranscriptWaitingForModel(items, false), false);
});

test('parallel tools must all return results before the model waiting indicator appears', () => {
  const first = toolCall(1, 'read');
  const second = toolCall(2, 'read', {}, 'second result');
  assert.equal(isTranscriptWaitingForModel([first, second], true), false);
  first.result = 'first result';
  assert.equal(isTranscriptWaitingForModel([first, second], true), true);
});

test('empty results are completed only after a result event has been received', () => {
  const tool = toolCall(1);
  assert.equal(toolCallStatus.hasToolCallResult(tool), false);
  assert.equal(isTranscriptWaitingForModel([tool], true), false);
  tool.resultReceived = true;
  assert.equal(toolCallStatus.hasToolCallResult(tool), true);
  assert.equal(isTranscriptWaitingForModel([tool], true), true);
  for (const result of ['', false, 0, {}]) {
    assert.equal(isTranscriptWaitingForModel([toolCall(1, 'read', {}, result)], true), true);
  }
});

test('waiting uses the full transcript and respects interactive and rich boundaries', () => {
  const completed = toolCall(1, 'read', {}, 'done');
  for (const boundary of [
    userMessage(2), assistantMessage(2), planReview(2), userInputRequest(2),
    assistantMessage(2, '', { generatedImagePending: true }),
    assistantMessage(2, '', { attachments: [{ name: 'image.png' }] }),
    { id: 2, type: 'message', role: 'error', text: 'Failed' },
  ]) {
    assert.equal(isTranscriptWaitingForModel([completed, boundary], true), false);
  }
  assert.equal(isTranscriptWaitingForModel([completed, toolCall(2, 'write_todos')], true), false);
  assert.equal(isTranscriptWaitingForModel([toolCall(1, 'html_preview', {}, '')], true), true);
  assert.equal(isTranscriptWaitingForModel([], true), false);
  assert.equal(isTranscriptWaitingForModel([assistantMessage(1, 'Thinking...')], true), false);
});

test('empty transcripts produce an empty view', () => {
  const view = buildTranscriptActivityView([], false);
  assert.deepEqual(rowKeys(view), []);
  assert.equal(view.liveKey, null);
  const emptyMessages = buildTranscriptActivityView([
    assistantMessage(1, ''), assistantMessage(2, ' \n '),
  ], true);
  assert.deepEqual(rowKeys(emptyMessages), []);
  assert.equal(emptyMessages.liveKey, null);
});

test('the answer id points at the last assistant message only', () => {
  assert.equal(findTranscriptAnswerId([]), null);
  assert.equal(findTranscriptAnswerId([userMessage(1), toolCall(2)]), null);
  const answerId = findTranscriptAnswerId([
    userMessage(1),
    toolCall(2),
    assistantMessage(3, 'first answer'),
    userMessage(4),
    assistantMessage(5, 'final answer'),
  ]);
  assert.equal(answerId, 5);
});

test('empty assistant messages do not take answer actions from the visible answer', () => {
  assert.equal(findTranscriptAnswerId([
    assistantMessage(1, 'Answer.'),
    assistantMessage(2, ''),
    assistantMessage(3, ' \n '),
  ]), 1);
  assert.equal(findTranscriptAnswerId([assistantMessage(1, '')]), null);
  assert.equal(findTranscriptAnswerId([
    assistantMessage(1, '', { attachments: [{ name: 'chart.png' }] }),
  ]), 1);
});

test('boundary classification keeps messages, interactions and rich results visible', () => {
  assert.equal(isTranscriptActivityBoundary(userMessage(1)), true);
  assert.equal(isTranscriptActivityBoundary({ id: 2, type: 'message', role: 'error', text: 'boom' }), true);
  assert.equal(isTranscriptActivityBoundary({ id: 3, type: 'message', role: 'system', text: 'note' }), true);
  assert.equal(isTranscriptActivityBoundary(planReview(4)), true);
  assert.equal(isTranscriptActivityBoundary(userInputRequest(5)), true);
  assert.equal(isTranscriptActivityBoundary(toolCall(6, 'html_preview')), true);
  assert.equal(
    isTranscriptActivityBoundary(assistantMessage(7, '', { generatedImagePending: true })),
    true,
  );
  assert.equal(isTranscriptActivityBoundary(assistantMessage(8, 'hello')), true);
  assert.equal(isTranscriptActivityBoundary(toolCall(9)), false);
  assert.equal(isTranscriptActivityBoundary(assistantMessage(10, '')), false);
  assert.equal(isTranscriptActivityBoundary(assistantMessage(11, ' \n ')), false);
});

test('only the latest failed run offers checkpoint continuation', () => {
  const failure = { id: 2, type: 'message', role: 'error', text: 'Provider timed out', resumable: true };
  assert.equal(findTranscriptResumeId([]), null);
  assert.equal(findTranscriptResumeId([userMessage(1), failure]), 2);
  assert.equal(findTranscriptResumeId([failure, assistantMessage(3, '')]), 2);
  assert.equal(findTranscriptResumeId([{ ...failure, resumable: false }]), null);
  for (const next of [userMessage(3), assistantMessage(3), toolCall(3), planReview(3), userInputRequest(3)]) {
    assert.equal(findTranscriptResumeId([failure, next]), null);
  }
  assert.equal(findTranscriptResumeId([failure, { ...failure, id: 3 }]), 3);
});
