import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compileFunction } from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

function fixture(options = {}) {
  const workers = [];
  const timers = new Map();
  let now = 0;
  function advanceTime(milliseconds) {
    const end = now + milliseconds;
    while (true) {
      const next = [...timers].sort((a, b) => a[1].at - b[1].at)[0];
      if (!next || next[1].at > end) break;
      const [id, timer] = next;
      timers.delete(id);
      now = timer.at;
      timer.callback();
    }
    now = end;
  }
  class Worker {
    constructor(url, workerOptions) {
      if (options.blocked) throw new Error('Workers blocked');
      this.options = workerOptions;
      this.requests = [];
      workers.push(this);
    }
    postMessage(request) {
      if (options.failPostMessage) throw new Error('Cannot send message');
      this.requests.push(request);
    }
    terminate() { this.terminated = true; }
    reply(index, html) { this.onmessage({ data: { id: this.requests[index].id, html } }); }
  }
  const source = readFileSync(new URL('../src/lib/codeHighlight.ts', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  compileFunction(outputText.replaceAll('import.meta.url', JSON.stringify(import.meta.url)),
    ['exports', 'Worker', 'setTimeout', 'clearTimeout', 'Date'])(exports, Worker,
    (callback, delay) => {
      const id = Symbol();
      timers.set(id, { callback, at: now + delay });
      return id;
    },
    id => timers.delete(id), { now: () => now });
  return { ...exports, workers, timers, advanceTime };
}

test('plain, unknown and oversized code stays escaped without loading a worker', async () => {
  const f = fixture();
  const html = await f.highlightCode('<script>&"\'</script>', f.languageFromName('unknown'), true);
  assert.ok(html.includes('&lt;script&gt;&amp;&quot;&#39;&lt;/script&gt;'));
  for (const code of ['x'.repeat(100_001), 'x'.repeat(4_001), 'x\n'.repeat(2_002)]) {
    assert.ok(!(await f.highlightCode(code, 'typescript', true)).includes('<span'));
  }
  assert.equal(f.workers.length, 0);
});

test('concurrent requests share a lazy worker and match replies by id', async () => {
  const f = fixture();
  const first = f.highlightCode('const a = 1;', 'typescript', true);
  const duplicate = f.highlightCode('const a = 1;', 'typescript', true);
  const second = f.highlightCode('print(2)', 'python', false);
  assert.equal(f.workers.length, 1);
  const worker = f.workers[0];
  assert.equal(worker.options.type, 'module');
  assert.equal(worker.requests.length, 2);
  worker.reply(1, '<pre>python</pre>');
  worker.reply(0, '<pre>typescript</pre>');
  assert.deepEqual(await Promise.all([first, duplicate, second]),
    ['<pre>typescript</pre>', '<pre>typescript</pre>', '<pre>python</pre>']);
  assert.equal(await f.highlightCode('const a = 1;', 'typescript', true), '<pre>typescript</pre>');
  assert.equal(worker.requests.length, 2, 'remounts reuse cached output');
  assert.equal(f.timers.size, 0);
});

test('theme changes and cache eviction request fresh output', async () => {
  const f = fixture();
  for (let index = 0; index < 33; index++) {
    const result = f.highlightCode(`value${index}`, 'typescript', true);
    f.workers[0].reply(index, `<pre>${index}</pre>`);
    await result;
  }
  const evicted = f.highlightCode('value0', 'typescript', true);
  f.workers[0].reply(33, '<pre>fresh</pre>');
  assert.equal(await evicted, '<pre>fresh</pre>');
  const light = f.highlightCode('value0', 'typescript', false);
  assert.equal(f.workers[0].requests[34].dark, false);
  f.workers[0].reply(34, '<pre>light</pre>');
  assert.equal(await light, '<pre>light</pre>');
});

for (const event of ['onerror', 'onmessageerror']) {
  test(`${event} settles pending callers and allows a retry after backoff`, async () => {
    const f = fixture();
    const first = f.highlightCode('<first>', 'typescript', true);
    const second = f.highlightCode('<second>', 'python', true);
    const failed = f.workers[0];
    failed[event]({ preventDefault() {} });
    assert.ok((await first).includes('&lt;first&gt;'));
    assert.ok((await second).includes('&lt;second&gt;'));
    assert.equal(failed.terminated, true);
    assert.equal(f.timers.size, 0);
    f.advanceTime(4_999);
    assert.ok((await f.highlightCode('<first>', 'typescript', true)).includes('&lt;first&gt;'));
    assert.equal(f.workers.length, 1);
    f.advanceTime(1);
    const retry = f.highlightCode('<first>', 'typescript', true);
    assert.equal(f.workers.length, 2);
    const replacement = f.workers[1];
    failed.onmessage({ data: { id: replacement.requests[0].id, html: '<pre>stale</pre>' } });
    failed.onerror({ preventDefault() {} });
    failed.onmessageerror();
    assert.ok(!replacement.terminated, 'late events from the old worker are ignored');
    replacement.reply(0, '<pre>recovered</pre>');
    assert.equal(await retry, '<pre>recovered</pre>');
    assert.equal(f.timers.size, 0);
  });
}

test('queued requests can take over 30 seconds while the worker makes progress', async () => {
  const f = fixture();
  const requests = ['first', 'second', 'third'].map(code => f.highlightCode(code, 'typescript', true));
  const worker = f.workers[0];
  assert.equal(f.timers.size, 1, 'queued callers share one progress watchdog');
  for (let index = 0; index < requests.length; index++) {
    f.advanceTime(20_000);
    assert.ok(!worker.terminated, 'queue wait time does not count as a stall');
    worker.reply(index, `<pre>${index}</pre>`);
  }
  assert.deepEqual(await Promise.all(requests), ['<pre>0</pre>', '<pre>1</pre>', '<pre>2</pre>']);
  assert.equal(f.timers.size, 0);
  f.advanceTime(60_000);
  const later = f.highlightCode('later', 'python', false);
  f.advanceTime(29_999);
  assert.ok(!worker.terminated, 'an idle worker starts a fresh deadline');
  worker.reply(3, '<pre>later</pre>');
  assert.equal(await later, '<pre>later</pre>');
});

test('a stalled worker times out after the last reply and can recover', async () => {
  const f = fixture();
  const first = f.highlightCode('first', 'typescript', true);
  const slow = f.highlightCode('<slow>', 'python', true);
  f.advanceTime(20_000);
  f.workers[0].reply(0, '<pre>first</pre>');
  assert.equal(await first, '<pre>first</pre>');
  f.advanceTime(20_000);
  const queued = f.highlightCode('<queued>', 'typescript', true);
  f.advanceTime(9_999);
  assert.ok(!f.workers[0].terminated);
  f.advanceTime(1);
  assert.ok((await slow).includes('&lt;slow&gt;'));
  assert.ok((await queued).includes('&lt;queued&gt;'));
  assert.equal(f.workers[0].terminated, true);
  assert.equal(f.timers.size, 0);
  assert.equal(await f.highlightCode('first', 'typescript', true), '<pre>first</pre>');
  assert.ok((await f.highlightCode('<slow>', 'python', true)).includes('&lt;slow&gt;'));
  assert.equal(f.workers.length, 1);
  f.advanceTime(5_000);
  const retry = f.highlightCode('<slow>', 'python', true);
  assert.equal(f.workers.length, 2);
  f.workers[1].reply(0, '<pre>recovered</pre>');
  assert.equal(await retry, '<pre>recovered</pre>');
});

for (const failure of ['blocked', 'failPostMessage']) {
  test(`${failure} falls back immediately without preventing later recovery`, async () => {
    const options = { [failure]: true };
    const f = fixture(options);
    assert.ok((await f.highlightCode('<blocked>', 'python', true)).includes('&lt;blocked&gt;'));
    assert.equal(f.timers.size, 0);
    options[failure] = false;
    assert.ok((await f.highlightCode('<blocked>', 'python', true)).includes('&lt;blocked&gt;'));
    assert.equal(f.workers.length, failure === 'blocked' ? 0 : 1);
    if (failure === 'failPostMessage') assert.equal(f.workers[0].terminated, true);
    f.advanceTime(5_000);
    const retry = f.highlightCode('<blocked>', 'python', true);
    f.workers.at(-1).reply(0, '<pre>recovered</pre>');
    assert.equal(await retry, '<pre>recovered</pre>');
    assert.equal(f.timers.size, 0);
  });
}
