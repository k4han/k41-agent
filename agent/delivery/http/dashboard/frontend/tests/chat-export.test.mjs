import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/chatExport.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
runInNewContext(outputText, { exports });
const { createChatExport } = exports;
const exportedAt = new Date('2026-11-07T10:20:30.000Z');
const thread = {
  thread_id: 'api/user/channel/default', title: 'Project notes', active_checkpoint_id: 'selected-branch',
  platform: 'api', user_id: 'user', channel_id: 'channel', agent_name: 'default', provider: 'test', model: 'model-a',
  workspace: { locator: '/private/workspace' }, pending_requests: [{ secret: 'runtime-only' }],
  messages: [
    { id: 'user', role: 'user', content: 'Keep **formatting** and Unicode: \u1ea3nh \ud83c\udf08',
      branch: { options: [{ message: 'Another branch' }] },
      attachments: [
        { name: 'notes.txt', mime_type: 'text/plain', size: 8, kind: 'text', content: '```\ncontent\n```' },
        { name: 'image.png', mime_type: 'image/png', size: 5, kind: 'image', base64: 'aW1hZ2U=' },
      ] },
    { id: 'call', role: 'assistant', content: '', tool_calls: [{ id: 'read-1', name: 'read_file', args: { path: 'notes.txt' } }] },
    { id: 'result', role: 'tool', name: 'read_file', tool_call_id: 'read-1', content: 'Tool output' },
    { id: 'answer', role: 'assistant', content: 'The answer.\n\n```js\nconst value = 1;\n```' },
  ],
};

test('Markdown exports readable messages in order and preserves formatting, Unicode, and attachment text', () => {
  const file = createChatExport(thread, 'markdown', false, exportedAt);
  assert.match(file.filename, /^conversation-Project notes-2026-11-07T10-20-30-000Z\.md$/);
  assert.equal(file.mimeType, 'text/markdown;charset=utf-8');
  assert.ok(file.content.startsWith('# Project notes\n'));
  assert.ok(file.content.includes(thread.messages[0].content));
  assert.ok(file.content.includes(thread.messages.at(-1).content));
  assert.ok(file.content.indexOf('## User') < file.content.indexOf('## Assistant'));
  assert.ok(file.content.includes('selected\\-branch'));
  assert.ok(file.content.includes('notes\\.txt'));
  assert.ok(file.content.includes('image\\.png'));
  assert.ok(file.content.includes('````\n```\ncontent\n```\n````'));
  assert.ok(!file.content.includes('Tool output'));
  assert.ok(!file.content.includes('Tool call:'));
  assert.ok(!file.content.includes('aW1hZ2U='));
  assert.ok(!file.content.includes('Another branch'));
});

test('tool activity can be included in both readable formats', () => {
  for (const format of ['markdown', 'text']) {
    const file = createChatExport(thread, format, true, exportedAt);
    assert.ok(file.content.includes('Tool output'));
    assert.ok(file.content.includes('Tool call: read'));
    assert.ok(file.content.includes('"path": "notes.txt"'));
    assert.ok(file.content.indexOf('"path"') < file.content.indexOf('Tool output'));
    assert.ok(file.content.indexOf('Tool output') < file.content.indexOf('The answer.'));
  }
});

test('tool call arguments are exported without the null fields a model did not use', () => {
  const call = { id: 'desk-1', name: 'computer_action', args: {
    actions: [{ action: 'click', x: 0, y: 20, x2: null, text: null, keys: null, modifiers: [] }], target: null, screenshot: false,
  } };
  const used = { actions: [{ action: 'click', x: 0, y: 20, modifiers: [] }], screenshot: false };
  const withCall = { ...thread, messages: [thread.messages[0], { id: 'call', role: 'assistant', content: '', tool_calls: [call] }] };
  const before = structuredClone(withCall);
  for (const format of ['markdown', 'text']) {
    const content = createChatExport(withCall, format, true, exportedAt).content;
    assert.ok(!content.includes('null'));
    assert.ok(content.includes('"x": 0') && content.includes('"screenshot": false') && content.includes('"modifiers": []'));
  }
  const data = JSON.parse(createChatExport(withCall, 'json', false, exportedAt).content);
  assert.deepEqual(data.messages[1].tool_calls, [{ ...call, args: used }]);
  assert.deepEqual(withCall, before);
});

test('text exports keep literal labels and the original message content', () => {
  const file = createChatExport(thread, 'text', false, exportedAt);
  assert.ok(file.filename.endsWith('.txt'));
  assert.equal(file.mimeType, 'text/plain;charset=utf-8');
  assert.ok(file.content.startsWith('Project notes\n'));
  assert.ok(file.content.includes('\nUser\n'));
  assert.ok(file.content.includes('Attachment: notes.txt (text/plain, 8 bytes)'));
  assert.ok(file.content.includes(thread.messages.at(-1).content));
  assert.ok(!file.content.includes('Tool output'));
});

test('JSON exports all saved messages and attachment data without runtime state or other branch prompts', () => {
  const before = structuredClone(thread);
  const file = createChatExport(thread, 'json', false, exportedAt);
  assert.equal(file.mimeType, 'application/json;charset=utf-8');
  assert.ok(file.filename.endsWith('.json'));
  const data = JSON.parse(file.content);
  assert.equal(data.format_version, 1);
  assert.equal(data.exported_at, exportedAt.toISOString());
  assert.equal(data.active_checkpoint_id, 'selected-branch');
  assert.equal(data.messages.length, 4);
  assert.deepEqual(data.messages[0].attachments, thread.messages[0].attachments);
  assert.deepEqual(data.messages[1].tool_calls, thread.messages[1].tool_calls);
  assert.equal(data.messages[2].tool_call_id, 'read-1');
  assert.equal(data.messages[0].branch, undefined);
  assert.equal(data.workspace, undefined);
  assert.equal(data.pending_requests, undefined);
  assert.deepEqual(thread, before);
});

test('filenames remove forbidden characters, limit length, and keep Unicode', () => {
  const title = '../CON:\\bad<>"|?*\n\u0000 name. ';
  const file = createChatExport({ ...thread, title }, 'json', false, exportedAt);
  assert.ok(!/[<>:"/\\|?*\u0000-\u001f]/.test(file.filename));
  assert.ok(file.filename.startsWith('conversation-'));
  const long = createChatExport({ ...thread, title: '\ud83c\udf08'.repeat(300) }, 'text', false, exportedAt);
  assert.ok(long.filename.length < 255);
  assert.ok(long.filename.includes('\ud83c\udf08'));
  const fallback = createChatExport({ ...thread, title: ' ' }, 'text', false, exportedAt);
  assert.ok(fallback.content.startsWith(thread.thread_id));
});

test('empty history and exports with only hidden tool activity report actionable errors', () => {
  assert.throws(() => createChatExport({ ...thread, messages: [] }, 'markdown'), /No saved messages/);
  const onlyTools = { ...thread, messages: thread.messages.slice(1, 3) };
  assert.throws(() => createChatExport(onlyTools, 'text'), /including tool activity/);
  assert.ok(createChatExport(onlyTools, 'text', true).content.includes('Tool output'));
});

test('downloads use UTF-8 blobs, remove their link, and release URLs after the click', async () => {
  const events = [];
  let blob;
  const link = { click: () => events.push('click'), remove: () => events.push('remove') };
  const context = { exports: {}, Blob, URL: {
    createObjectURL: (value) => { blob = value; return 'blob:export'; },
    revokeObjectURL: (url) => events.push(`revoke:${url}`),
  }, document: {
    createElement: () => link,
    body: { appendChild: () => events.push('append') },
  }, setTimeout: (callback) => { events.push('schedule'); callback(); } };
  runInNewContext(outputText, context);
  const file = createChatExport(thread, 'text', false, exportedAt);
  context.exports.downloadChatExport(file);
  assert.equal(await blob.text(), file.content);
  assert.equal(blob.type, file.mimeType);
  assert.equal(link.download, file.filename);
  assert.equal(link.href, 'blob:export');
  assert.deepEqual(events, ['append', 'click', 'remove', 'schedule', 'revoke:blob:export']);
});
