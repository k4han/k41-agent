import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import * as solid from 'solid-js/dist/solid.js';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/useVisualViewport.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});

function createFixture(t, { enabled = true, hasViewport = true } = {}) {
  const viewport = Object.assign(new EventTarget(), { height: 844, offsetTop: 0, scale: 1 });
  const frames = new Map();
  let nextFrame = 0;
  const window = Object.assign(new EventTarget(), {
    visualViewport: hasViewport ? viewport : null,
    requestAnimationFrame: callback => {
      frames.set(++nextFrame, callback);
      return nextFrame;
    },
    cancelAnimationFrame: id => frames.delete(id),
  });
  const exports = {};
  runInNewContext(outputText, {
    exports, window,
    require: name => {
      assert.equal(name, 'solid-js');
      return solid;
    },
  });
  let dispose, bounds, setEnabled;
  solid.createRoot(cleanup => {
    dispose = cleanup;
    const [active, setActive] = solid.createSignal(enabled);
    setEnabled = setActive;
    bounds = exports.useVisualViewport(active);
  });
  t.after(() => dispose());
  return {
    bounds, viewport, window, frames, dispose, setEnabled,
    flush: () => {
      const pending = [...frames.values()];
      frames.clear();
      pending.forEach(callback => callback());
    },
  };
}

test('keyboard resize and viewport panning update without a window resize', t => {
  const f = createFixture(t);
  assert.equal(f.bounds().height, 844);
  f.viewport.height = 460;
  f.viewport.dispatchEvent(new Event('resize'));
  f.viewport.offsetTop = 120;
  f.viewport.dispatchEvent(new Event('scroll'));
  assert.equal(f.frames.size, 1, 'Viewport events share one animation frame');
  f.flush();
  assert.equal(f.bounds().height, 460);
  assert.equal(f.bounds().offsetTop, 120);

  f.viewport.height = 844;
  f.viewport.offsetTop = 0;
  f.viewport.dispatchEvent(new Event('resize'));
  f.flush();
  assert.equal(f.bounds().height, 844);
  assert.equal(f.bounds().offsetTop, 0);
});

test('pinch zoom preserves the layout until normal scale is restored', t => {
  const f = createFixture(t);
  f.viewport.scale = 2;
  f.viewport.height = 422;
  f.viewport.offsetTop = 100;
  f.viewport.dispatchEvent(new Event('resize'));
  f.flush();
  assert.equal(f.bounds().height, 844);
  assert.equal(f.bounds().offsetTop, 0);
  f.viewport.scale = 1;
  f.viewport.dispatchEvent(new Event('resize'));
  f.flush();
  assert.equal(f.bounds().height, 422);
});

test('disabling tracking clears bounds, cancels frames, and removes listeners', t => {
  const f = createFixture(t);
  f.viewport.dispatchEvent(new Event('resize'));
  assert.equal(f.frames.size, 1);
  f.setEnabled(false);
  assert.equal(f.bounds(), undefined);
  assert.equal(f.frames.size, 0);
  f.viewport.dispatchEvent(new Event('resize'));
  f.viewport.dispatchEvent(new Event('scroll'));
  f.window.dispatchEvent(new Event('resize'));
  assert.equal(f.frames.size, 0);
  f.viewport.height = 500;
  f.setEnabled(true);
  assert.equal(f.bounds().height, 500);
  f.viewport.dispatchEvent(new Event('resize'));
  f.dispose();
  assert.equal(f.frames.size, 0);
  f.viewport.dispatchEvent(new Event('scroll'));
  f.window.dispatchEvent(new Event('resize'));
  assert.equal(f.frames.size, 0);
});

test('disabled pages and browsers without VisualViewport keep CSS sizing', t => {
  const disabled = createFixture(t, { enabled: false });
  assert.equal(disabled.bounds(), undefined);
  disabled.viewport.dispatchEvent(new Event('resize'));
  assert.equal(disabled.frames.size, 0);
  const unsupported = createFixture(t, { hasViewport: false });
  assert.equal(unsupported.bounds(), undefined);
  unsupported.window.dispatchEvent(new Event('resize'));
  assert.equal(unsupported.frames.size, 0);
});
