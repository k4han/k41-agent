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
    assert.equal(control.requestEffort(), undefined);
    setOption({ reasoning_effort_levels: [] });
    assert.equal(control.effort(), '');
    assert.equal(control.requestEffort(), undefined);
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
