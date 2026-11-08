import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compileFunction } from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

function loadModule(file, dependencies) {
  const { outputText } = ts.transpileModule(readFileSync(new URL(file, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  compileFunction(outputText, ['exports', 'require'])(exports, name => {
    assert.ok(name in dependencies, `Unexpected dependency: ${name}`);
    return dependencies[name];
  });
  return exports;
}

const constants = { NO_WORKSPACE_KEY: 'no-workspace', NO_WORKSPACE_LABEL: 'No workspace' };
const workspace = loadModule('../src/lib/workspace.ts', {
  '../types': { isSandboxBackend: backend => ['daytona', 'modal'].includes(backend) },
  './workspaceConstants': constants,
});
const threads = loadModule('../src/lib/chatThreads.ts', {
  '@/components/Transcript': {}, '@/lib/userInputRequest': {}, '@/lib/generatedImages': {},
  '@/lib/workspaceConstants': constants, '@/lib/workspace': workspace,
});
const ref = (metadata, locator = '/sessions/first') => ({ backend: 'local', locator, label: 'Temp workspace', metadata });

test('current and legacy No workspace metadata share labels while retaining distinct identities', () => {
  for (const metadata of [{ no_workspace: true }, { temp: true }, { source: ' TEMP ' }, { source: 'no-workspace' }]) {
    const first = ref(metadata);
    const second = ref(metadata, '/sessions/second');
    assert.equal(workspace.isNoWorkspace(first), true);
    assert.equal(workspace.workspaceDisplayLabel(first), 'No workspace');
    const binding = workspace.bindWorkspaceRef(first);
    assert.equal(workspace.workspaceDisplayLabel(binding), 'No workspace');
    assert.notEqual(binding.scope.key, workspace.bindWorkspaceRef(second).scope.key);
    assert.equal(threads.threadWorkspaceKey({ workspace: binding }), 'no-workspace');
    assert.equal(threads.threadWorkspaceLabel({ workspace: binding }), 'No workspace');
  }
});

test('legacy group keys normalize and project conversations retain their group', () => {
  assert.equal(threads.threadWorkspaceKey({ workspace_key: 'temp' }), 'no-workspace');
  assert.equal(threads.threadWorkspaceLabel({ workspace_key: 'temp', workspace_label: 'Temp workspace' }), 'No workspace');
  assert.equal(workspace.isNoWorkspace(null), false);
  const project = workspace.bindWorkspaceRef({ ...ref({}), label: 'Project' });
  assert.equal(workspace.isNoWorkspace(project), false);
  assert.equal(threads.threadWorkspaceKey({ workspace: project }), project.scope.key);
  assert.equal(threads.threadWorkspaceLabel({ workspace: project }), 'Project');
});
