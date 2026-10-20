import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/chatModelPreference.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const storageKey = 'k41.dashboard.lastChatModel';

function loadPreference(localStorage) {
  const exports = {};
  runInNewContext(outputText, { exports, window: { localStorage } });
  return exports;
}

test('preferences persist the agent and concrete provider/model identities without effort', () => {
  const values = new Map();
  const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) };
  const preference = loadPreference(storage);
  preference.writeChatModelPreference({ provider: ' primary ', model: ' shared-model ', agentName: ' other ', reasoningEffort: 'high' });
  assert.deepEqual(JSON.parse(values.get(storageKey)), { agentName: 'other', provider: 'primary', model: 'shared-model' });
  const reloaded = loadPreference(storage).readChatModelPreference();
  assert.equal(reloaded.provider, 'primary');
  assert.equal(reloaded.model, 'shared-model');
  assert.equal(reloaded.agentName, 'other');
  preference.writeChatModelPreference({ agentName: 'other', provider: '@pool', model: 'fallback-pool' });
  assert.deepEqual(JSON.parse(values.get(storageKey)), { agentName: 'other', provider: '@pool', model: 'fallback-pool' });
});

test('legacy preferences and invalid agent values retain the remembered model', () => {
  for (const agentName of [undefined, null, 42, '', '   ']) {
    const stored = JSON.stringify({ agentName, provider: 'test', model: 'a' });
    const preference = loadPreference({ getItem: () => stored }).readChatModelPreference();
    assert.equal(preference.agentName, undefined);
    assert.equal(preference.provider, 'test');
    assert.equal(preference.model, 'a');
  }
});

test('only explicit custom model markers are persisted and restored', () => {
  const values = new Map();
  const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) };
  const preference = loadPreference(storage);
  preference.writeChatModelPreference({ provider: 'test', model: 'manual-model', customModel: true });
  assert.deepEqual(JSON.parse(values.get(storageKey)), { provider: 'test', model: 'manual-model', customModel: true });
  assert.equal(loadPreference(storage).readChatModelPreference().customModel, true);

  for (const customModel of [undefined, false, 'true', 1, null]) {
    preference.writeChatModelPreference({ provider: 'test', model: 'catalog-model', customModel });
    assert.deepEqual(JSON.parse(values.get(storageKey)), { provider: 'test', model: 'catalog-model' });
    values.set(storageKey, JSON.stringify({ provider: 'test', model: 'catalog-model', customModel }));
    assert.equal(loadPreference(storage).readChatModelPreference().customModel, undefined);
  }
});

test('missing, malformed, and incomplete preferences are ignored', () => {
  for (const value of [null, '', '{', 'null', '42', '[]', '{}', '{"provider":3,"model":"a"}',
    '{"provider":"test","model":null}', '{"provider":" ","model":"a"}', '{"provider":"test","model":""}']) {
    assert.equal(loadPreference({ getItem: () => value }).readChatModelPreference(), null);
  }
  let written = false;
  const preference = loadPreference({ setItem: () => { written = true; } });
  preference.writeChatModelPreference({ provider: '', model: 'a' });
  preference.writeChatModelPreference({ provider: 'test', model: ' ' });
  assert.equal(written, false);
});

test('blocked storage and exhausted quota do not interrupt chat', () => {
  const preference = loadPreference({
    getItem: () => { throw new Error('Storage blocked'); },
    setItem: () => { throw new Error('Quota exceeded'); },
  });
  assert.equal(preference.readChatModelPreference(), null);
  assert.doesNotThrow(() => preference.writeChatModelPreference({ provider: 'test', model: 'a' }));
  const exports = {};
  runInNewContext(outputText, { exports, window: { get localStorage() { throw new Error('Storage blocked'); } } });
  assert.equal(exports.readChatModelPreference(), null);
  assert.doesNotThrow(() => exports.writeChatModelPreference({ provider: 'test', model: 'a' }));
});
