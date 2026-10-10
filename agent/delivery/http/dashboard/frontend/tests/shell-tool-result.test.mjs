import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/shellToolResult.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
runInNewContext(outputText, { exports });
const { isShellTool, shellToolResult } = exports;

test('shell output uses the final receipt rather than a matching line in stdout', () => {
  const result = shellToolResult('Command exited with code 2.\n\nCommand exited with code 0.');
  assert.equal(result.output, 'Command exited with code 2.');
  assert.equal(result.outcome, 'Command exited with code 0.');
  assert.equal(result.failed, false);
  assert.equal(result.running, false);
});

test('failed exits, timeouts and cancellations are visibly distinct from running output', () => {
  for (const outcome of ['Command exited with code 2.', 'Command exited with code -9.', 'Command timed out.', 'Command was cancelled.']) {
    const result = shellToolResult(`detail\n\n${outcome}`);
    assert.equal(result.output, 'detail');
    assert.equal(result.failed, true);
    assert.equal(result.running, false);
  }
  const running = shellToolResult('ready\n\nProcess is still running.');
  assert.equal(running.running, true);
  assert.equal(running.failed, false);
});

test('unrecognized and legacy results retain the generic tool rendering', () => {
  for (const value of [null, {}, 'plain output', '(no output)\n\nProcess old-id: completed; exit_code=0; next_cursor=0.']) {
    assert.equal(shellToolResult(value), null);
  }
  assert.equal(isShellTool('bash'), true);
  assert.equal(isShellTool('read_process_output'), true);
  assert.equal(isShellTool('read'), false);
});
