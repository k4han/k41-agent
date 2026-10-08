import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import * as solid from 'solid-js/dist/solid.js';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/skillSuggestions.ts', import.meta.url), 'utf8');
const exports = {};
runInNewContext(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports });
const { getSkillQuery, filterSkillOptions, insertSkillSuggestion } = exports;

test('slash suggestions accept skill searches and lifecycle commands', () => {
  for (const [prompt, action, search] of [
    ['/', 'load', ''], ['/demo', 'load', 'demo'], ['/skill', 'load', ''],
    ['/skill DEM', 'load', 'dem'], ['/skill refresh ', 'refresh', ''],
    ['/skill unload demo', 'unload', 'demo'],
    ['/skill load ', 'load', ''], ['/skill load de', 'load', 'de'],
    ['/SKILL demo', 'load', 'demo'], ['/SKILL LOAD de', 'load', 'de'],
    ['/Skill ReFresh de', 'refresh', 'de'], ['/SKILL UNLOAD de', 'unload', 'de'],
  ]) {
    const query = getSkillQuery(prompt, prompt.length);
    assert.ok(query, prompt);
    assert.equal(query.action, action);
    assert.equal(query.search, search);
  }
});

test('ordinary text, paths, selections and task lines do not open suggestions', () => {
  for (const prompt of ['https://example.com', 'Please use /demo', '/usr/local', '/skill demo task', 'Task\n/', '\n/']) {
    assert.equal(getSkillQuery(prompt, prompt.length), null, prompt);
  }
  assert.equal(getSkillQuery('/demo', 1, 3), null);
  assert.equal(getSkillQuery('', 5), null);
});

test('multiple leading skill commands can be completed without changing the task', () => {
  const prompt = '/skill first\n/skill refresh de\nKeep the task';
  const caret = prompt.indexOf('\nKeep');
  const query = getSkillQuery(prompt, caret);
  const result = insertSkillSuggestion(prompt, query, 'demo');
  assert.equal(result.prompt, '/skill first\n/skill refresh demo \nKeep the task');
  assert.equal(result.caret, result.prompt.indexOf('\nKeep'));
});

test('uppercase leading commands allow subsequent skill suggestions', () => {
  const prompt = '/SKILL LOAD first\n/Skill Unload de';
  const query = getSkillQuery(prompt, prompt.length);
  assert.equal(query.action, 'unload');
  assert.equal(query.search, 'de');
  assert.equal(insertSkillSuggestion(prompt, query, 'demo').prompt, '/SKILL LOAD first\n/skill unload demo ');
});

test('completion replaces the whole token at the caret and preserves surrounding text', () => {
  const prompt = '/skill demo-existing Keep this task';
  const query = getSkillQuery(prompt, '/skill de'.length);
  const result = insertSkillSuggestion(prompt, query, 'demo');
  assert.equal(result.prompt, '/skill demo Keep this task');
  assert.equal(result.caret, '/skill demo '.length);
});

test('suggestions filter unavailable packages and prioritize name matches over descriptions', () => {
  const option = (name, extra = {}) => ({ id: name, name, description: '', enabled: true, shadowed: false, ...extra });
  const options = [
    option('alpha', { description: 'Demo resources' }), option('demo'),
    option('demo-disabled', { enabled: false }), option('demo-shadowed', { shadowed: true }),
    option('demo-invalid', { diagnostics: [{ severity: 'error' }] }),
    option('demo-warning', { diagnostics: [{ severity: 'warning' }] }),
  ];
  assert.equal(Array.from(filterSkillOptions(options, getSkillQuery('/DEM', 4)), item => item.name).join(','), 'demo,demo-warning,alpha');
});

test('an already focused input opens suggestions and late workspace responses are ignored', async (t) => {
  const requests = [];
  const input = { selectionStart: 1, selectionEnd: 1 };
  const hook = {};
  const hookSource = readFileSync(new URL('../src/lib/useSkillSuggestions.ts', import.meta.url), 'utf8');
  runInNewContext(ts.transpileModule(hookSource, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, {
    exports: hook, AbortController,
    document: { activeElement: input, getElementById: () => null },
    require: (name) => {
      if (name === 'solid-js') return solid;
      if (name === '@/lib/skillSuggestions') return exports;
      assert.equal(name, '@/lib/api');
      return { apiFetch: (url, init) => new Promise(resolve => requests.push({ url, init, resolve })) };
    },
  });
  let suggestions;
  let setWorkspace;
  solid.createRoot(dispose => {
    t.after(dispose);
    const [workspace, updateWorkspace] = solid.createSignal(null);
    setWorkspace = updateWorkspace;
    suggestions = hook.useSkillSuggestions({
      prompt: '/', get workspace() { return workspace(); },
      currentThreadId: '', inputDisabled: false, onPromptChange: () => {},
    }, () => input);
  });
  suggestions.syncSelection(true);
  assert.equal(suggestions.open(), true);
  assert.equal(requests.length, 1);
  setWorkspace({ backend: 'local', locator: 'D:/other', metadata: {} });
  assert.equal(suggestions.open(), false);
  assert.equal(requests[0].init.signal.aborted, true);
  suggestions.syncSelection(true);
  const currentRequest = requests.at(-1);
  assert.equal(JSON.parse(new URL(currentRequest.url, 'http://localhost').searchParams.get('workspace')).locator, 'D:/other');
  const option = name => ({ id: name, name, description: '', enabled: true, shadowed: false });
  currentRequest.resolve({ packages: [option('current')] });
  await new Promise(resolve => setImmediate(resolve));
  requests[0].resolve({ packages: [option('stale')] });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(suggestions.matches()[0].name, 'current');
  assert.equal(suggestions.loading(), false);
});
