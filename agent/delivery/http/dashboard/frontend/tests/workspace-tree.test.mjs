import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compileFunction } from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

const { outputText } = ts.transpileModule(readFileSync(new URL('../src/lib/workspaceTree.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
compileFunction(outputText, ['exports'])(exports);
const { flattenWorkspaceTree } = exports;
const entry = (path, kind = 'file') => ({ path, name: path, kind, size: 0, modified_at: 0 });

test('flattening visits only open reachable folders and keeps row order and depth', () => {
  const first = entry('src', 'directory'), nested = entry('src/nested', 'directory');
  const entries = { '': [first, entry('README')], src: [nested, entry('src/file')],
    'src/nested': [entry('src/nested/file')], unrelated: [entry('hidden')] };
  const rows = flattenWorkspaceTree(entries, { src: true, unrelated: true }, {}, {});
  assert.deepEqual(rows.map(row => [row.key, row.depth]), [
    ['entry:src', 0], ['entry:src/nested', 1], ['entry:src/file', 1], ['entry:README', 0],
  ]);
  assert.equal(rows[0].entry, first);
  assert.deepEqual(flattenWorkspaceTree(entries, {}, {}, {}).map(row => row.key), ['entry:src', 'entry:README']);
});

test('loading and truncated notices appear in their directory with stable unique keys', () => {
  const entries = { '/root': [entry('/root/a', 'directory'), entry('/root/b', 'directory')], '/root/b': [] };
  const rows = flattenWorkspaceTree(entries, { '/root/a': true, '/root/b': true },
    { '/root/a': true, '/root/b': true }, { '/root': true, '/root/a': true }, '/root');
  assert.deepEqual(rows.map(row => [row.key, row.depth]), [
    ['entry:/root/a', 0], ['loading:/root/a', 1], ['truncated:/root/a', 1],
    ['entry:/root/b', 0], ['truncated:/root', 0],
  ]);
  assert.equal(new Set(rows.map(row => row.key)).size, rows.length);
  assert.equal(rows[1].text, 'Loading...');
  assert.equal(rows[2].text, 'Tree truncated');
});

test('large and deeply expanded trees flatten without recursive stack growth', () => {
  const entries = { '': Array.from({ length: 5000 }, (_, i) => entry(`file-${i}`)) };
  assert.equal(flattenWorkspaceTree(entries, {}, {}, {}).length, 5000);
  const deep = { '': [entry('dir-0', 'directory')] }, expanded = {};
  for (let i = 0; i < 12000; i += 1) {
    deep[`dir-${i}`] = [entry(`dir-${i + 1}`, 'directory')];
    expanded[`dir-${i}`] = true;
  }
  const rows = flattenWorkspaceTree(deep, expanded, {}, {});
  assert.equal(rows.length, 12001);
  assert.equal(rows.at(-1).depth, 12000);
});
