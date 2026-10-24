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
const shellToolResult = loadModule('../src/lib/shellToolResult.ts');
const { formatToolGroupSummary, getToolCategory, isToolFailed } = loadModule(
  '../src/lib/toolGroupSummary.ts',
  {
    '@/lib/toolCallStatus': toolCallStatus,
    '@/lib/shellToolResult': shellToolResult,
  },
);

test('tool categories are mapped properly', () => {
  assert.equal(getToolCategory('read_file').key, 'reads');
  assert.equal(getToolCategory('list_dir').key, 'reads');
  assert.equal(getToolCategory('bash').key, 'commands');
  assert.equal(getToolCategory('exec_command').key, 'commands');
  assert.equal(getToolCategory('write_file').key, 'edits');
  assert.equal(getToolCategory('edit_file').key, 'edits');
  assert.equal(getToolCategory('web_search').key, 'web');
  assert.equal(getToolCategory('mcp__stitch__generate').key, 'generate');
});

test('failure detection handles shell and generic errors', () => {
  assert.equal(isToolFailed({ name: 'read_file', result: 'file content', resultReceived: true }), false);
  assert.equal(isToolFailed({ name: 'bash', result: 'stdout\n\nCommand exited with code 0.', resultReceived: true }), false);
  assert.equal(isToolFailed({ name: 'bash', result: 'error\n\nCommand exited with code 1.', resultReceived: true }), true);
  assert.equal(isToolFailed({ name: 'read_file', result: '[error] not_found: file does not exist', resultReceived: true }), true);
  assert.equal(isToolFailed({ name: 'custom', result: { status: 'error', message: 'fail' }, resultReceived: true }), true);
});

test('formatToolGroupSummary matches Pattern 2 with breakdown and status', () => {
  // 9 reads, 8 commands (2 failed), 2 edits -> 19 tools
  const tools = [
    ...Array(9).fill(null).map((_, i) => ({ name: 'read_file', result: 'ok', resultReceived: true })),
    ...Array(6).fill(null).map((_, i) => ({ name: 'bash', result: 'out\n\nCommand exited with code 0.', resultReceived: true })),
    ...Array(2).fill(null).map((_, i) => ({ name: 'bash', result: 'err\n\nCommand exited with code 2.', resultReceived: true })),
    ...Array(2).fill(null).map((_, i) => ({ name: 'edit_file', result: 'saved', resultReceived: true })),
  ];

  const summary = formatToolGroupSummary(tools, { running: false });
  assert.equal(summary.total, 19);
  assert.equal(summary.totalFailed, 2);
  assert.equal(summary.hasFailed, true);
  assert.equal(summary.totalText, '19 tools:');
  assert.equal(summary.breakdownText, '9 reads, 8 commands (2 failed), 2 edits');
  assert.equal(summary.statusText, '2 failed');
  assert.equal(summary.fullLabel, '19 tools: 9 reads, 8 commands (2 failed), 2 edits · 2 failed');
});

test('formatToolGroupSummary when all tools succeed', () => {
  const tools = [
    { name: 'read_file', result: 'ok', resultReceived: true },
    { name: 'bash', result: 'done\n\nCommand exited with code 0.', resultReceived: true },
    { name: 'edit_file', result: 'saved', resultReceived: true },
  ];

  const summary = formatToolGroupSummary(tools, { running: false });
  assert.equal(summary.total, 3);
  assert.equal(summary.totalFailed, 0);
  assert.equal(summary.hasFailed, false);
  assert.equal(summary.totalText, '3 tools:');
  assert.equal(summary.breakdownText, '1 read, 1 command, 1 edit');
  assert.equal(summary.statusText, '');
  assert.equal(summary.fullLabel, '3 tools: 1 read, 1 command, 1 edit');
});

test('formatToolGroupSummary when running', () => {
  const tools = [
    { name: 'read_file', result: 'ok', resultReceived: true },
    { name: 'bash', result: null, resultReceived: false },
  ];

  const summary = formatToolGroupSummary(tools, { running: true });
  assert.equal(summary.total, 2);
  assert.equal(summary.totalText, '2 tools:');
  assert.equal(summary.breakdownText, '1 read, 1 command');
  assert.equal(summary.statusText, 'loading');
  assert.equal(summary.fullLabel, '2 tools: 1 read, 1 command');
});

test('formatToolGroupSummary stays loading while live: true even if individual tools returned results', () => {
  // Between tool calls during a turn, all current tools have returned results,
  // but the turn is still ongoing (live: true).
  const tools = [
    { name: 'read_file', result: 'ok', resultReceived: true },
  ];

  const summary = formatToolGroupSummary(tools, { live: true, running: false });
  assert.equal(summary.total, 1);
  assert.equal(summary.statusText, 'loading');
  assert.equal(summary.fullLabel, '1 tool: 1 read');
});

test('formatToolGroupSummary shows clean label when live: false', () => {
  const tools = [
    { name: 'read_file', result: 'ok', resultReceived: true },
  ];

  const summary = formatToolGroupSummary(tools, { live: false, running: false });
  assert.equal(summary.total, 1);
  assert.equal(summary.statusText, '');
  assert.equal(summary.fullLabel, '1 tool: 1 read');
});
