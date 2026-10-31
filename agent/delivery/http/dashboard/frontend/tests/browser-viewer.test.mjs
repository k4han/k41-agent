import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/browserViewer.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
const exports = {};
runInNewContext(outputText, { exports });
const { browserPointer, browserModifiers, browserImageUrl } = exports;

test('browser input maps scaled screenshots to viewport coordinates', () => {
  const point = browserPointer(420, 250, { left: 100, top: 50, width: 640, height: 400 });
  assert.equal(point.x, 640);
  assert.equal(point.y, 400);
  assert.equal(browserPointer(-10, 900, { left: 0, top: 0, width: 320, height: 200 }).y, 800);
  assert.equal(browserPointer(-10, 900, { left: 0, top: 0, width: 320, height: 200 }).x, 0);
});

test('keyboard modifiers and browser artifact URLs stay bounded', () => {
  assert.equal(browserModifiers({ altKey: true, ctrlKey: true, metaKey: false, shiftKey: true }), 11);
  const path = '/dashboard-api/browser/artifacts/' + 'a'.repeat(32);
  assert.equal(browserImageUrl('Browser image: ' + path), path);
  assert.equal(browserImageUrl('Browser image: https://other.example/image'), null);
  assert.equal(browserImageUrl({ image_url: path }), null);
});
