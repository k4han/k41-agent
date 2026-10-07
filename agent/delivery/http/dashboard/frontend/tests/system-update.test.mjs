import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import * as solid from 'solid-js/dist/solid.js';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/versionStore.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const version = {
  current_version: '0.1.2', latest_version: '0.1.3', has_update: true,
  install_type: 'managed', is_managed_install: true, error: null,
};
const healthy = (version = '0.1.3', started_at = 2) => ({ status: 'ok', version, started_at });

function harness({ versions = [version], statuses = [], health = [healthy()], jobId = 'job', post } = {}) {
  const calls = [];
  const timeouts = [];
  let now = 0;
  let reloads = 0;
  let healthIndex = 0;
  let statusIndex = 0;
  let versionIndex = 0;
  const next = (values, index) => values[Math.min(index, values.length - 1)];
  const api = {
    apiFetch: async (path, init = {}) => {
      calls.push({ path, init });
      if (path.includes('/update/status')) {
        const value = next(statuses, statusIndex++);
        if (!value || value instanceof Error) throw value || new Error('Status not available');
        return value;
      }
      if (path.endsWith('/update')) return post ? post() : { status: 'started', update_id: jobId };
      if (path.includes('force=true') && versions.length === 1 && versions[0] === version) {
        const currentVersion = health.at(-1)?.version || version.current_version;
        return { ...version, current_version: currentVersion, latest_version: currentVersion, has_update: false };
      }
      const value = next(versions, versionIndex++);
      if (value instanceof Error) throw value;
      return value;
    },
  };
  const exports = {};
  runInNewContext(outputText, {
    exports,
    require: name => name === 'solid-js' ? solid : api,
    AbortSignal: { timeout: ms => { timeouts.push(ms); return AbortSignal.timeout(ms); } },
    Date: { now: () => now },
    Error,
    console: { error: () => {} },
    window: { location: { reload: () => reloads++ } },
    setTimeout: (callback, ms) => {
      if (ms === 1500) { callback(); return; }
      now += ms;
      queueMicrotask(callback);
    },
    fetch: async (path, init) => {
      calls.push({ path, init });
      const value = next(health, healthIndex++);
      if (value instanceof Error) throw value;
      return { ok: value !== null, json: async () => value };
    },
  });
  return { store: exports, calls, timeouts, reloads: () => reloads, elapsed: () => now };
}

test('download or dependency errors are shown immediately without waiting for a restart', async () => {
  const h = harness({ statuses: [{ status: 'failed', error: 'dependency sync failed' }] });
  await h.store.checkForUpdates();
  await h.store.startSystemUpdate();
  assert.equal(h.store.updateState(), 'error');
  assert.equal(h.store.updateError(), 'dependency sync failed');
  assert.equal(h.elapsed(), 2000);
  assert.equal(h.reloads(), 0);
});

test('a rollback restart or temporary network failure cannot report update success', async () => {
  const h = harness({ jobId: null, health: [new Error('offline'), healthy('0.1.2', 999)] });
  await h.store.checkForUpdates();
  await h.store.startSystemUpdate();
  assert.equal(h.store.updateState(), 'error');
  assert.match(h.store.updateError(), /longer than expected/);
  assert.equal(h.reloads(), 0);
});

test('an HTTP error followed by the unchanged server cannot report success', async () => {
  const h = harness({ jobId: null, health: [null, healthy('0.1.2')] });
  await h.store.checkForUpdates();
  await h.store.startSystemUpdate();
  assert.equal(h.store.updateState(), 'error');
  assert.equal(h.reloads(), 0);
});

test('success waits for the worker result and healthy installed version', async () => {
  const h = harness({
    statuses: [{ status: 'running' }, { status: 'updated', latest_version: '0.1.3' }],
  });
  await h.store.checkForUpdates();
  await h.store.startSystemUpdate();
  assert.equal(h.elapsed(), 4000);
  assert.equal(h.store.updateState(), 'success');
  assert.equal(h.store.versionInfo().current_version, '0.1.3');
  assert.equal(h.reloads(), 1);
  assert.equal(h.calls.filter(call => call.path.includes('/system/version')).length, 2);
  assert.ok(h.calls.some(call => call.path === '/dashboard-api/system/version?force=true'));
  assert.ok(h.timeouts.includes(5000));
});

test('the release selected by the worker can be newer than cached release metadata', async () => {
  const h = harness({
    statuses: [{ status: 'updated', latest_version: '0.1.4' }], health: [healthy('0.1.4')],
  });
  await h.store.checkForUpdates();
  await h.store.startSystemUpdate();
  assert.equal(h.store.updateState(), 'success');
  assert.equal(h.store.versionInfo().current_version, '0.1.4');
});

test('an already-current installation completes even when GitHub has an older release', async () => {
  const h = harness({
    statuses: [{ status: 'current', current_version: '0.1.4', latest_version: '0.1.3' }],
    health: [healthy('0.1.4')],
  });
  await h.store.checkForUpdates();
  await h.store.startSystemUpdate();
  assert.equal(h.store.updateState(), 'success');
  assert.equal(h.store.versionInfo().current_version, '0.1.4');
});

test('legacy updater without a status endpoint still requires the target version', async () => {
  const h = harness({ jobId: null, health: [healthy('0.1.2'), healthy()] });
  await h.store.checkForUpdates();
  await h.store.startSystemUpdate();
  assert.equal(h.store.updateState(), 'success');
  assert.equal(h.elapsed(), 4000);
  assert.equal(h.reloads(), 1);
});

test('repeated clicks queue one request and active updates keep the dialog open', async () => {
  let resolvePost;
  const h = harness({
    post: () => new Promise(resolve => { resolvePost = resolve; }),
    statuses: [{ status: 'failed', error: 'failed' }],
  });
  await h.store.checkForUpdates();
  h.store.openUpdateDialog();
  const pending = h.store.startSystemUpdate();
  await h.store.startSystemUpdate();
  h.store.closeUpdateDialog();
  assert.equal(h.store.isUpdateDialogOpen(), true);
  assert.equal(h.calls.filter(call => call.init.method === 'POST').length, 1);
  resolvePost({ status: 'started', update_id: 'job' });
  await pending;
  h.store.closeUpdateDialog();
  assert.equal(h.store.isUpdateDialogOpen(), false);
  assert.equal(h.store.updateState(), 'idle');
});

test('an update request rejection surfaces the server error', async () => {
  const h = harness({ post: () => { throw new Error('Another update is already in progress.'); } });
  await h.store.checkForUpdates();
  await h.store.startSystemUpdate();
  assert.equal(h.store.updateState(), 'error');
  assert.match(h.store.updateError(), /already in progress/);
  assert.equal(h.reloads(), 0);
});

test('release lookup errors remain visible and a successful retry clears them', async () => {
  const h = harness({ versions: [{ ...version, has_update: false, error: 'GitHub unavailable' }, version] });
  await h.store.checkForUpdates();
  assert.equal(h.store.versionCheckError(), 'GitHub unavailable');
  await h.store.checkForUpdates(true);
  assert.equal(h.store.versionCheckError(), '');
  assert.equal(h.store.versionInfo().has_update, true);
});

test('transport errors during version checks remain visible', async () => {
  const h = harness({ versions: [new Error('Network unavailable')] });
  assert.equal(await h.store.checkForUpdates(), null);
  assert.equal(h.store.versionCheckError(), 'Network unavailable');
  assert.equal(h.store.versionLoading(), false);
});

test('older version responses cannot overwrite a more recent manual check', async () => {
  let resolveFirst;
  const first = new Promise(resolve => { resolveFirst = resolve; });
  const h = harness({ versions: [first, { ...version, latest_version: '0.1.4' }] });
  const pending = h.store.checkForUpdates();
  await h.store.checkForUpdates(true);
  resolveFirst(version);
  await pending;
  assert.equal(h.store.versionInfo().latest_version, '0.1.4');
  assert.equal(h.store.versionLoading(), false);
});

test('success refreshes metadata and retains a newer release published during installation', async () => {
  const h = harness({
    versions: [version, { ...version, current_version: '0.1.3', latest_version: '0.1.4', has_update: true }],
    statuses: [{ status: 'updated', latest_version: '0.1.3' }],
  });
  await h.store.checkForUpdates();
  await h.store.startSystemUpdate();
  assert.equal(h.store.updateState(), 'success');
  assert.equal(h.store.versionInfo().current_version, '0.1.3');
  assert.equal(h.store.versionInfo().latest_version, '0.1.4');
  assert.equal(h.store.versionInfo().has_update, true);
  assert.equal(h.reloads(), 1);
});

test('a slow GitHub refresh cannot delay reload or falsely clear the update badge', async () => {
  const pendingRefresh = new Promise(() => {});
  const h = harness({
    versions: [version, pendingRefresh], statuses: [{ status: 'updated', latest_version: '0.1.3' }],
  });
  await h.store.checkForUpdates();
  await h.store.startSystemUpdate();
  assert.equal(h.store.updateState(), 'success');
  assert.equal(h.store.versionInfo().current_version, '0.1.3');
  assert.equal(h.store.versionInfo().has_update, true);
  assert.equal(h.store.versionLoading(), true);
  assert.equal(h.reloads(), 1);
});
