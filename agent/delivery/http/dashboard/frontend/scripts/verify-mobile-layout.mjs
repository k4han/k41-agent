import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const staticRoot = fileURLToPath(new URL('../../static', import.meta.url));
const executable = process.env.BROWSER_EXECUTABLE || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].find(candidate => fs.existsSync(candidate));
assert.ok(executable, 'Set BROWSER_EXECUTABLE to a Chromium browser to run mobile UI checks.');

const variable = {
  name: 'team_instructions', placeholder: '{{team_instructions}}',
  value: 'Long content '.repeat(40), created_at: '2026-01-01T00:00:00Z', updated_at: null,
};
const writes = [];
const runtimeSettings = Object.fromEntries([
  ['server.host', 'Host address', 'text', '127.0.0.1'],
  ['server.api_key', 'API key', 'password', 'fake-secret'],
  ['server.mode', 'Execution mode', 'select', 'production'],
  ['server.enabled', 'Enable runtime', 'boolean', true],
].map(([key, label, input_type, value]) => [key, {
  key, label, input_type, value, category: 'server', source: 'database',
  description: 'Runtime configuration description', options: ['production', 'development'],
}]));
const fixtures = {
  '/dashboard-api/catalog': { backends: [{ name: 'daytona', title: 'Daytona', enabled: true }] },
  '/dashboard-api/prompt-variables': { variables: [variable] },
  '/dashboard-api/chat-history': { threads: [], has_more: false, next_offset: null },
  '/dashboard-api/sessions': { sessions: [] },
  '/dashboard-api/agents/cards': { cards: [{ name: 'team-agent', display_name: 'Team agent',
    description: 'Long description '.repeat(20), provider: 'provider', model: 'model', valid: true, editable: true }] },
  '/dashboard-api/sandboxes': { sandboxes: [{ sandbox_id: 'sandbox-with-a-long-identifier', backend: 'daytona',
    label: 'Test sandbox', root: '/workspace', status: 'started', on_cloud: true, thread_id: null,
    repository_full_name: 'organization/repository-with-a-long-name', last_used_at: null,
    created_at: null, updated_at: null, metadata: {} }] },
  '/dashboard-api/config': { settings: runtimeSettings, by_category: { server: runtimeSettings } },
  '/dashboard-api/usage': {
    summary: { event_count: 1, missing_usage_count: 0, internal_event_count: 0, input_tokens: 100, output_tokens: 50, total_tokens: 150 },
    rows: [{ platform: 'dashboard', user_id: 'test-user', channel_id: 'test-channel', identity_label: 'Test user',
      event_count: 1, missing_usage_count: 0, internal_event_count: 0, input_tokens: 100, output_tokens: 50, total_tokens: 150, last_used_at: null }],
    workspaces: [], threads: [], filters: { platforms: ['dashboard'], users: [], channels: [], agents: [], providers: [], models: [], call_kinds: [] },
    pagination: { limit: 50, offset: 0, total: 1, has_more: false, next_offset: null }, range: { start: '2026-01-01', end: '2026-01-07' },
  },
  '/dashboard-api/home': {
    services: [{ name: 'scheduler', status: 'stopped', error: null }],
    system: { status: 'healthy', uptime_seconds: 60, uptime_display: '1m', version: 'test' },
    counters: { channels: { total: 0, running: 0, error: 0 }, agents: 0,
      tasks: { total: 0, active: 0, failed: 0 }, scheduler: { total: 0, upcoming: 0 },
      sessions_active: 0, providers: { total: 0, ready: 0 }, mcp_servers: { total: 0, connected: 0 } },
    recent: { tasks: [], threads: [], upcoming_jobs: [] }, active_sessions: [], providers_health: [],
    scheduler_timezone: 'UTC',
    onboarding: { show_checklist: true, needs_provider: true, needs_channel: true, needs_agent: true },
  },
};
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname.startsWith('/dashboard-api') || url.pathname.startsWith('/prompt-variables')) {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('X-CSRF-Token', 'test-csrf');
    if (req.method !== 'GET') {
      let body = '';
      for await (const chunk of req) body += chunk;
      writes.push({ path: url.pathname, method: req.method, body: JSON.parse(body || '{}') });
      res.end('{"status":"success"}');
    } else {
      res.end(JSON.stringify(fixtures[url.pathname] || {}));
    }
    return;
  }
  const relative = url.pathname.startsWith('/dashboard-assets/')
    ? url.pathname.slice('/dashboard-assets/'.length) : 'index.html';
  const target = path.resolve(staticRoot, relative);
  if (!target.startsWith(staticRoot + path.sep)) { res.writeHead(403); res.end(); return; }
  try {
    const types = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' };
    res.setHeader('Content-Type', types[path.extname(target)] || 'application/octet-stream');
    res.end(fs.readFileSync(target));
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-mobile-browser-'));
const browser = spawn(executable, [
  '--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-networking',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank',
], { windowsHide: true, stdio: 'ignore' });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(callback, label, timeout = 10000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await callback()) return;
    await delay(50);
  }
  throw new Error(`Timeout: ${label}`);
}
let ws;
let call;
try {
  const portFile = path.join(profile, 'DevToolsActivePort');
  await until(() => fs.existsSync(portFile), 'browser debugger');
  const port = Number(fs.readFileSync(portFile, 'utf8').split('\n')[0]);
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  ws = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
  await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }));
  let sequence = 0;
  const pending = new Map();
  const exceptions = [];
  call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const entry = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) entry?.reject(new Error(message.error.message));
      else entry?.resolve(message.result);
    }
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails.text);
  });
  await call('Page.enable');
  await call('Runtime.enable');
  await call('Emulation.setTouchEmulationEnabled', { enabled: true });
  const js = async expression => {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const tapAt = async (x, y) => {
    await call('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
    await call('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  };
  const tap = async selector => {
    await js(`document.querySelector(${JSON.stringify(selector)})?.scrollIntoView({ block: 'center', inline: 'nearest' })`);
    await until(() => js(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) return false;
      const r = el.getBoundingClientRect();
      return el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
    })()`), `uncovered touch target: ${selector}`);
    const point = await js(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) throw new Error('Missing target');
      el.scrollIntoView({ block: 'center', inline: 'nearest' });
      const r = el.getBoundingClientRect();
      const x = r.x + r.width / 2, y = r.y + r.height / 2;
      const hit = document.elementFromPoint(x, y);
      if (!el.contains(hit)) throw new Error('Target is covered: ' + ${JSON.stringify(selector)} +
        ', hit=' + hit?.className + ', rect=' + JSON.stringify(r.toJSON()));
      return { x, y };
    })()`);
    await tapAt(point.x, point.y);
  };
  const navigate = async route => {
    await call('Page.navigate', { url: base + route });
    await until(() => js('!!document.querySelector(".app-layout .main")'), 'page shell');
  };
  const assertFits = async selector => {
    const overflow = await js(`Array.from(document.querySelectorAll(${JSON.stringify(selector)})).filter(el => {
      const r = el.getBoundingClientRect();
      return r.left < -1 || r.right > innerWidth + 1;
    }).map(el => ({ class: el.className, width: el.getBoundingClientRect().width }))`);
    assert.deepEqual(overflow, [], `Content must fit the viewport: ${selector}`);
  };
  for (const [width, height] of [[320, 568], [375, 812], [640, 480], [641, 480], [768, 1024], [980, 600], [981, 800], [1440, 900]]) {
    await call('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: true });
    for (const preference of ['collapsed', 'expanded']) {
      await navigate('/settings/prompt-variables');
      await js(`localStorage.setItem('k41-dashboard-sidebar', '${preference}');
        localStorage.setItem('k41-dashboard-settings-sidebar', '${preference}');`);
      for (const route of ['/', '/settings/prompt-variables']) {
        await navigate(route);
        const isSettings = route.startsWith('/settings');
        await until(() => js(isSettings ? '!!document.querySelector("tbody td")' : '!!document.querySelector(".onboarding-list")'), 'page data');
        await assertFits('.main, .content, .settings-page-body, .panel, .table-wrap');
        if (isSettings && width <= 980) {
          const scrolling = await js(`(() => {
            const wrap = document.querySelector('.table-wrap');
            wrap.scrollLeft = wrap.scrollWidth;
            const actions = wrap.querySelector('tbody td:last-child').getBoundingClientRect();
            const bounds = wrap.getBoundingClientRect();
            return (wrap.scrollWidth <= wrap.clientWidth || wrap.scrollLeft > 0) && actions.right <= bounds.right + 1;
          })()`);
          assert.equal(scrolling, true, 'The last table column must be reachable by scrolling');
        }
        if (width > 980) continue;
        await tap('.topbar-menu-toggle');
        await until(() => js('document.querySelector(".sidebar").getBoundingClientRect().left >= -1'), 'visible drawer');
        await delay(250);
        assert.equal(await js('getComputedStyle(document.querySelector(".sidebar")).visibility'), 'visible');
        assert.equal(await js(`Array.from(document.querySelectorAll('.sidebar .nav-link .nav-label')).every(el =>
          getComputedStyle(el).display !== 'none' && el.getBoundingClientRect().width > 0)`), true, 'Drawer labels must remain visible');
        await tap('.sidebar a.nav-link');
        await until(() => js('!document.querySelector(".app-layout--drawer-open")'), 'navigation closes drawer');
        await tap('.topbar-menu-toggle');
        await delay(250);
        await tap('.drawer-close-btn');
        await until(() => js('!document.querySelector(".app-layout--drawer-open")'), 'close button');
        await tap('.topbar-menu-toggle');
        await delay(250);
        await tapAt(width - 5, Math.floor(height / 2));
        await until(() => js('!document.querySelector(".app-layout--drawer-open")'), 'backdrop closes drawer');
        assert.equal(await js('document.body.style.overflow'), '');
        assert.equal(await js('document.documentElement.style.overflow'), '');
      }
    }
    await navigate('/settings/prompt-variables');
    await until(() => js('!!document.querySelector("tbody td")'), 'variables table');
    await tap('.settings-resource-actions .btn-primary');
    await until(() => js('!!document.querySelector(".dialog input")'), 'create dialog');
    await delay(250);
    await assertFits('.dialog, .dialog-header, .dialog-footer');
    await js(`(() => {
      const input = document.querySelector('.dialog input');
      input.value = 'mobile_test'; input.dispatchEvent(new Event('input', { bubbles: true }));
      const value = document.querySelector('.dialog textarea');
      value.value = 'Mobile content'; value.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
    await tap('.dialog-footer .btn-primary');
    await until(() => js('!document.querySelector(".dialog-backdrop")'), 'save dialog');
    assert.deepEqual(writes.at(-1), { path: '/prompt-variables', method: 'POST', body: { name: 'mobile_test', value: 'Mobile content' } });
    assert.equal(await js('document.body.style.overflow'), '');
    assert.equal(await js('document.documentElement.style.overflow'), '');
    await tap('tbody .btn-danger');
    await until(() => js('!!document.querySelector(".confirm-dialog-content")'), 'delete confirmation');
    await delay(250);
    await assertFits('.dialog, .dialog-footer');
    await tap('.dialog-close-btn');
    await until(() => js('!document.querySelector(".dialog-backdrop")'), 'close confirmation');
    assert.equal(writes.at(-1).method, 'POST', 'Closing confirmation must not delete the variable');
    for (const route of ['/settings/agents', '/settings/sandboxes', '/settings/usage', '/settings/config']) {
      await navigate(route);
      await until(() => js(route.endsWith('/config') ? '!!document.querySelector(".version-settings-card")' : '!!document.querySelector("tbody td")'), `data for ${route}`);
      await assertFits('.main, .content, .settings-page-body, .panel, .table-wrap, .sandboxes-table-wrap, .usage-filter-grid, .field');
      assert.equal(await js(`Array.from(document.querySelectorAll('.setting-row input, .setting-row .form-select-trigger, .setting-row .toggle-control')).every(el => {
        const row = el.closest('.setting-row').getBoundingClientRect();
        const r = el.getBoundingClientRect();
        return r.left >= row.left && r.right <= row.right + 1;
      })`), true, `Setting controls must not be clipped: ${route}`);
      assert.equal(await js(`Array.from(document.querySelectorAll('.table-wrap, .sandboxes-table-wrap')).every(wrap => {
        wrap.scrollLeft = wrap.scrollWidth;
        const cell = wrap.querySelector('tbody td:last-child').getBoundingClientRect();
        return cell.right <= wrap.getBoundingClientRect().right + 1;
      })`), true, `Reachable table columns: ${route}`);
    }
    console.log(`PASS: ${width}x${height}, both sidebar preferences, dashboard, settings table, navigation, dialog save`);
  }
  assert.deepEqual(exceptions, []);
} finally {
  if (call && ws?.readyState === WebSocket.OPEN) {
    await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  }
  ws?.close();
  browser.kill();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  // Remove only the unique temporary profile created by this test.
  for (let attempt = 0; attempt < 5; attempt++) {
    try { fs.rmSync(profile, { recursive: true, force: true }); break; }
    catch { await delay(200); }
  }
}
