import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import * as solid from 'solid-js/dist/solid.js';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/useReasoningEffort.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
runInNewContext(outputText, { exports, require: () => solid });

const selectionSource = readFileSync(new URL('../src/lib/modelSelection.ts', import.meta.url), 'utf8');
const selectionModule = {};
runInNewContext(ts.transpileModule(selectionSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: selectionModule });

test('legacy model/provider defaults resolve to concrete identities for effort matching', () => {
  const catalogs = [
    { provider: 'primary', default_model: 'provider-model' },
    { provider: 'other', default_model: 'other-model' },
  ];
  for (const provider of ['', 'default', 'primary']) {
    for (const model of ['', 'default', 'provider default']) {
      const selected = selectionModule.resolveModelAndProvider(provider, model, 'primary', 'global-model', catalogs);
      assert.equal(selected.provider, 'primary');
      assert.equal(selected.model, provider === 'primary' ? 'provider-model' : 'global-model');
    }
  }
  const explicit = selectionModule.resolveModelAndProvider('other', 'override-model', 'primary', 'global-model', catalogs);
  assert.equal(explicit.provider, 'other');
  assert.equal(explicit.model, 'override-model');
  const noGlobalModel = selectionModule.resolveModelAndProvider('', 'provider default', 'primary', '', catalogs);
  assert.equal(noGlobalModel.model, 'provider-model');
});

test('pending model data does not access an absent override', () => {
  solid.createRoot((dispose) => {
    const [key, setKey] = solid.createSignal();
    const [option, setOption] = solid.createSignal();
    const control = exports.useReasoningEffort(key, option);
    assert.equal(control.requestEffort(), undefined);
    solid.batch(() => {
      setKey('loaded-model');
      setOption({ reasoning_effort_levels: ['low', 'high'], reasoning_effort_default: 'high' });
    });
    assert.equal(control.effort(), 'high');
    dispose();
  });
});

test('model changes select their defaults and do not send stale overrides', () => {
  let dispose;
  let setKey, setOption, control;
  solid.createRoot((cleanup) => {
    dispose = cleanup;
    const [key, updateKey] = solid.createSignal('provider/first');
    const [option, updateOption] = solid.createSignal({ reasoning_effort_levels: ['low', 'high'], reasoning_effort_default: 'high' });
    setKey = updateKey;
    setOption = updateOption;
    control = exports.useReasoningEffort(key, option);
  });
  try {
    assert.equal(control.effort(), 'high');
    assert.equal(control.requestEffort(), 'high');
    control.setEffort('low');
    assert.equal(control.requestEffort(), 'low');
    solid.batch(() => {
      setKey('provider/second');
      setOption({ reasoning_effort_levels: ['medium', 'max'], reasoning_effort_default: 'max' });
    });
    assert.equal(control.effort(), 'max');
    control.setEffort('invalid');
    assert.equal(control.effort(), 'max');
    control.setEffort('');
    assert.equal(control.requestEffort(), 'auto');
    setOption({ reasoning_effort_levels: [] });
    assert.equal(control.effort(), '');
    assert.equal(control.requestEffort(), 'auto');
    solid.batch(() => {
      setKey('provider/first');
      setOption({ reasoning_effort_levels: ['low', 'high'], reasoning_effort_default: 'high' });
    });
    assert.equal(control.effort(), 'high');
    setOption({ reasoning_effort_levels: ['low', 'high'] });
    assert.equal(control.effort(), '');
    setOption(undefined);
    assert.equal(control.requestEffort(), undefined);
  } finally { dispose(); }
});

test('metadata refresh removes invalid overrides and follows a new default', () => {
  solid.createRoot((dispose) => {
    const [option, setOption] = solid.createSignal({ reasoning_effort_levels: ['low', 'high'], reasoning_effort_default: 'high' });
    const control = exports.useReasoningEffort(() => 'same-model', option);
    control.setEffort('low');
    setOption({ reasoning_effort_levels: ['medium', 'max'], reasoning_effort_default: 'max' });
    assert.equal(control.requestEffort(), 'max');
    setOption({ reasoning_effort_levels: ['medium'], reasoning_effort_default: 'dynamic' });
    assert.equal(control.requestEffort(), undefined);
    dispose();
  });
});

test('agent defaults take precedence and reset overrides when switching agents', () => {
  let dispose, setKey, setAgentEffort, control;
  solid.createRoot((cleanup) => {
    dispose = cleanup;
    const [key, updateKey] = solid.createSignal('first-agent/model');
    const [agentEffort, updateAgentEffort] = solid.createSignal('low');
    setKey = updateKey;
    setAgentEffort = updateAgentEffort;
    control = exports.useReasoningEffort(key, () => ({
      reasoning_effort_levels: ['low', 'medium', 'high'], reasoning_effort_default: 'medium',
    }), agentEffort);
  });
  try {
    assert.equal(control.requestEffort(), 'low');
    control.setEffort('high');
    assert.equal(control.requestEffort(), 'high');
    solid.batch(() => {
      setKey('second-agent/model');
      setAgentEffort('medium');
    });
    assert.equal(control.requestEffort(), 'medium');
    setAgentEffort(undefined);
    assert.equal(control.requestEffort(), 'medium');
  } finally { dispose(); }
});

test('unavailable agent effort is not replaced with a provider default in requests', () => {
  solid.createRoot((dispose) => {
    const control = exports.useReasoningEffort(() => 'agent/model', () => ({
      reasoning_effort_levels: ['medium', 'high'], reasoning_effort_default: 'high',
    }), () => 'low');
    assert.equal(control.requestEffort(), undefined);
    dispose();
  });
});

test('explicit Auto bypasses an agent default even when the model default is unknown', () => {
  let dispose, setKey, setOption, control;
  solid.createRoot((cleanup) => {
    dispose = cleanup;
    const [key, updateKey] = solid.createSignal('agent/model');
    const [option, updateOption] = solid.createSignal({
      reasoning_effort_levels: ['low', 'medium', 'high'], reasoning_effort_default: 'medium',
    });
    setKey = updateKey;
    setOption = updateOption;
    control = exports.useReasoningEffort(key, option, () => 'low');
  });
  try {
    assert.equal(control.requestEffort(), 'low');
    control.setEffort('');
    assert.equal(control.effort(), '');
    assert.equal(control.requestEffort(), 'auto');
    setOption({ reasoning_effort_levels: ['low', 'high'] });
    assert.equal(control.requestEffort(), 'auto');
    setKey('second-agent/model');
    assert.equal(control.requestEffort(), 'low');
  } finally { dispose(); }
});
