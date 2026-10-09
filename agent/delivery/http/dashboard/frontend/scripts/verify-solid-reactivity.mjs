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
assert.ok(executable, 'Set BROWSER_EXECUTABLE to run browser checks.');

const agent = {
  name: 'default', display_name: 'Default', description: '', graph_type: 'react_agent',
  provider: 'test', model: 'model-a', valid: true, editable: true, source: 'user',
  tools: [], sub_agents: [], mcp_servers: [], plan_approval_targets: [],
  context_compact_threshold: 75, system_prompt: '', hidden: false,
};
const models = {
  provider_names: ['test'], default_provider: 'test', default_model: 'model-a',
  model_catalogs: [{ provider: 'test', default_model: 'model-a', models: [{ id: 'model-a', label: 'Model A' }] }],
};
const repository = {
  id: 1, repository_id: 1, installation_id: 1, full_name: 'example/solid', account_login: 'example',
  private: false, default_branch: 'main', enabled: true, agent_name: 'default', trigger_label: 'agent',
  mention_triggers: ['@agent'], notify_platform: '', notify_external_id: '', notify_channel_id: '',
  issue_label_enabled: true, issue_comment_enabled: true, pr_review_comment_enabled: true,
  repository_instructions: '', provider_name: '', model_name: '', context_compact_threshold: null,
  tool_policy_mode: 'inherit', allowed_tools: [], allowed_skills: [], branch_prefix: 'agent',
  workspace_backend: 'local', last_synced_at: null, created_at: null, updated_at: null,
};
let homeRequests = 0;
let sessionRequests = 0;
let chatResponse;
const sessionResponses = new Set();
const config = {
  settings: { 'runtime.name': { value: 'initial', input_type: 'text', label: 'Runtime name', category: 'runtime' } },
  by_category: { runtime: { 'runtime.name': { value: 'initial', input_type: 'text', label: 'Runtime name', category: 'runtime' } } },
  settings_sources: {},
};
const homePayload = () => ({
  services: [], system: { status: 'healthy', version: '1', uptime_display: '1s' },
  counters: { channels: { total: 0, running: 0, error: 0 }, agents: 1, tasks: { total: 0, active: 0, failed: 0 },
    scheduler: { total: 0, upcoming: 0 }, sessions_active: homeRequests, providers: { total: 1, ready: 1 },
    mcp_servers: { total: 0, connected: 0 } },
  recent: { tasks: [], threads: [], upcoming_jobs: [] }, active_sessions: [], providers_health: [],
  scheduler_timezone: 'UTC', onboarding: { show_checklist: false },
});
const fixtures = {
  '/dashboard-api/catalog': { backends: [{ name: 'local', title: 'Local', enabled: true }] },
  '/dashboard-api/agents/cards': { cards: [agent], agent_names: ['default'] },
  '/dashboard-api/agents/providers': models,
  '/dashboard-api/agents/workflows': { workflows: ['react_agent'] },
  '/dashboard-api/agents/tools': { tools: [], tool_groups: [] },
  '/dashboard-api/agents/mcp': { mcp_server_options: [], mcp_installs: {} },
  '/dashboard-api/sessions': { sessions: [] },
  '/dashboard-api/chat-history': { threads: [], has_more: false },
  '/dashboard-api/workspace/default': { workspace: null },
  '/dashboard-api/prompt-variables': { variables: [] },
  '/dashboard-api/github/repositories/1': {
    repository, activity: { active_count: 0, recent_count: 0, tasks: [] }, identities: [], agent_names: ['default'],
    tools: [], tool_groups: [], skills: [], repository_skill_dir: '', ...models,
  },
};
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/dashboard-api/sessions/events') {
    sessionRequests += 1;
    sessionResponses.add(res);
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
    res.write('event: snapshot\ndata: {"sessions":[]}\n\n');
    res.on('close', () => sessionResponses.delete(res));
    return;
  }
  if (url.pathname === '/api/chat/events') {
    for await (const chunk of req) { void chunk; }
    chatResponse = res;
    res.writeHead(200, { 'Content-Type': 'application/x-ndjson', 'Cache-Control': 'no-cache' });
    res.flushHeaders();
    return;
  }
  if (url.pathname === '/agents/cards/default' && req.method === 'PUT') {
    let body = '';
    for await (const chunk of req) body += chunk;
    Object.assign(agent, JSON.parse(body));
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ status: 'updated', card: agent }));
    return;
  }
  if (url.pathname.startsWith('/dashboard-api') || url.pathname === '/settings') {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('X-CSRF-Token', 'test-csrf');
    if (url.pathname === '/dashboard-api/home') { homeRequests += 1; res.end(JSON.stringify(homePayload())); return; }
    if (url.pathname === '/dashboard-api/config') { res.end(JSON.stringify(config)); return; }
    if (url.pathname === '/settings' && req.method === 'PUT') {
      let body = '';
      for await (const chunk of req) body += chunk;
      const { values } = JSON.parse(body);
      config.settings['runtime.name'].value = values['runtime.name'];
      config.by_category.runtime['runtime.name'].value = values['runtime.name'];
      res.end('{"status":"success"}'); return;
    }
    res.end(JSON.stringify(fixtures[url.pathname] || {})); return;
  }
  const relative = url.pathname.startsWith('/dashboard-assets/') ? url.pathname.slice('/dashboard-assets/'.length) : 'index.html';
  const target = path.resolve(staticRoot, relative);
  if (!target.startsWith(staticRoot + path.sep)) { res.writeHead(403); res.end(); return; }
  try {
    res.setHeader('Content-Type', { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' }[path.extname(target)] || 'application/octet-stream');
    res.end(fs.readFileSync(target));
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-solid-browser-'));
const browser = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run',
  '--disable-background-networking', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'],
{ windowsHide: true, stdio: 'ignore' });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(callback, label, timeout = 20000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (await callback()) return;
    await delay(100);
  }
  throw new Error(`Timeout: ${label}`);
}
let ws, call;
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
  let pausedPageRequest;
  call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const entry = pending.get(message.id); pending.delete(message.id);
      if (message.error) entry?.reject(new Error(message.error.message)); else entry?.resolve(message.result);
    }
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails);
    if (message.method === 'Fetch.requestPaused') pausedPageRequest = message.params.requestId;
  });
  ws.addEventListener('close', () => {
    for (const entry of pending.values()) entry.reject(new Error('Browser closed'));
    pending.clear();
  });
  await call('Page.enable'); await call('Runtime.enable');
  await call('Page.addScriptToEvaluateOnNewDocument', { source: `
    window.themeObserverCounts = { active: 0, created: 0 };
    const OriginalObserver = window.MutationObserver;
    window.MutationObserver = class extends OriginalObserver {
      themeSubscription = false;
      observe(target, options) {
        if (!this.themeSubscription && target === document.documentElement && options.attributeFilter?.includes('class')) {
          this.themeSubscription = true;
          window.themeObserverCounts.active++;
          window.themeObserverCounts.created++;
        }
        return super.observe(target, options);
      }
      disconnect() {
        if (this.themeSubscription) { this.themeSubscription = false; window.themeObserverCounts.active--; }
        return super.disconnect();
      }
    };
  ` });
  const js = async expression => {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const navigate = route => call('Page.navigate', { url: base + route });
  const clickText = text => js(`Array.from(document.querySelectorAll('button')).find(button => button.textContent.trim() === ${JSON.stringify(text)}).click()`);

  await navigate('/repositories/1');
  await until(() => js('document.body.textContent.includes("example/solid")'), 'repository detail');
  await clickText('Automation');
  const trigger = `Array.from(document.querySelectorAll('.field')).find(field => field.querySelector('label')?.textContent === 'Issue label trigger')?.querySelector('input')`;
  await until(() => js(`!!(${trigger})`), 'repository input');
  await js(`window.originalInput = ${trigger}; originalInput.focus();`);
  for (const character of 'solid') await call('Input.insertText', { text: character });
  assert.equal(await js('document.activeElement === originalInput && originalInput.isConnected'), true);
  assert.equal(await js('originalInput.value'), 'agentsolid');
  await clickText('Optimization');
  await js('window.originalTextarea = document.querySelector(".repository-instructions-input"); originalTextarea.focus();');
  await call('Input.insertText', { text: 'Keep focus and selection' });
  assert.equal(await js('document.activeElement === originalTextarea && originalTextarea.isConnected'), true);
  console.log('PASS: repository editing retains DOM nodes and keyboard focus.');

  const sessionsBeforeHome = sessionRequests;
  await navigate('/');
  await until(() => js('!!document.querySelector(".grid-metrics .metric-value")'), 'home metrics');
  await until(() => sessionRequests > sessionsBeforeHome, 'home sessions');
  await delay(200);
  assert.equal(sessionRequests - sessionsBeforeHome, 1);
  const firstMetric = await js('document.querySelector(".metric-value").textContent');
  await js('document.dispatchEvent(new Event("visibilitychange"))');
  await until(() => js(`document.querySelector('.metric-value').textContent !== ${JSON.stringify(firstMetric)}`), 'reactive home counters');
  console.log('PASS: home uses one sessions connection and counters update after refresh.');

  await navigate('/settings/config');
  await until(() => js('!!document.querySelector(".setting-row input")'), 'settings resource');
  await js('window.settingsPage = document.querySelector(".settings-page-stack"); window.settingsInput = document.querySelector(".setting-row input"); settingsInput.value = "changed"; settingsInput.dispatchEvent(new Event("input", { bubbles: true }));');
  await js('Array.from(document.querySelectorAll(".topbar button")).find(button => button.textContent.includes("Save")).click()');
  await until(() => js('!!document.querySelector(".dialog-footer")'), 'settings confirmation');
  await js('Array.from(document.querySelectorAll("button")).find(button => button.textContent.includes("Confirm Save")).click()');
  await until(() => js('!document.querySelector(".dialog-footer") && document.querySelector(".setting-row input")?.value === "changed"'), 'settings refresh');
  assert.equal(await js('settingsPage === document.querySelector(".settings-page-stack")'), true);
  console.log('PASS: settings save and resource refresh preserve the page instance.');

  await navigate('/settings/agents');
  await until(() => js('!!document.querySelector(".agent-list-name")'), 'cached agent list');
  await js('document.querySelector(".agent-list-name").click()');
  await until(() => js(`!!document.querySelector('input[placeholder="My Agent"]')`), 'agent editor');
  await js(`const input = document.querySelector('input[placeholder="My Agent"]'); input.value = 'Updated default'; input.dispatchEvent(new Event('input', { bubbles: true }));`);
  await clickText('Save');
  await until(() => js('location.pathname === "/settings/agents" && document.querySelector(".agent-list-name")?.textContent === "Updated default"'), 'agent list after mutation');
  console.log('PASS: agent edits invalidate cached list data before SPA navigation.');

  await navigate('/chat');
  await until(() => js('!!document.querySelector(".chat-composer textarea")'), 'chat composer');
  await js('const input = document.querySelector(".chat-composer textarea"); input.value = "Test streaming"; input.dispatchEvent(new Event("input", { bubbles: true }));');
  await js(`document.querySelector('button[aria-label="Send message"]').click()`);
  await until(() => Boolean(chatResponse), 'chat request');
  const send = event => chatResponse.write(JSON.stringify(event) + '\n');
  send({ type: 'message', content: 'Stable heading\n\n```text\ncompleted code\n```\n\nTrailing' });
  await until(() => js('!!document.querySelector(".markdown-code-frame")'), 'first streamed block');
  await js('window.messageRow = document.querySelector(".markdown-code-frame").closest("[data-transcript-item-id]"); window.codeFrame = document.querySelector(".markdown-code-frame");');
  for (let i = 0; i < 5; i += 1) {
    send({ type: 'message', content: ` chunk-${i}` });
    await delay(80);
  }
  assert.equal(await js('messageRow.isConnected && codeFrame.isConnected && document.querySelector(".markdown-code-frame") === codeFrame'), true);
  assert.equal(await js('messageRow.textContent.includes("chunk-4")'), true);
  send({ type: 'tool_call', id: 'tool-1', name: 'read_file', args: { path: 'example.txt' } });
  await until(() => js('!!document.querySelector("details.tool-call")'), 'streamed tool call');
  await js('window.toolDetails = document.querySelector("details.tool-call"); toolDetails.open = true;');
  send({ type: 'tool_result', tool_call_id: 'tool-1', name: 'read_file', content: 'completed result' });
  await until(() => js('document.querySelector("details.tool-call").textContent.includes("completed result")'), 'tool result');
  assert.equal(await js('toolDetails.isConnected && toolDetails.open && document.querySelector("details.tool-call") === toolDetails'), true);
  send({ type: 'message', content: 'Second assistant response' });
  await until(() => js('document.body.textContent.includes("Second assistant response")'), 'second Markdown consumer');
  assert.equal(await js('themeObserverCounts.active'), 1);
  chatResponse.end();
  await until(() => js(`!document.querySelector('button[aria-label="Stop generating"]')`), 'stream completion');
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: chat streaming preserves message rows, completed Markdown blocks, and expanded tools.');
  await js(`document.querySelector('a[href="/history"]').click()`);
  await until(() => js('location.pathname === "/history" && themeObserverCounts.active === 0'), 'theme observer cleanup');
  console.log('PASS: Markdown consumers share one theme observer and release it after navigation.');
  await call('Fetch.enable', { patterns: [{ urlPattern: '*SecurityPage-*.js', requestStage: 'Request' }] });
  await navigate('/settings/security');
  await until(() => Boolean(pausedPageRequest), 'paused lazy page');
  assert.equal(await js('!!document.querySelector("[role=status]") && document.body.textContent.includes("Loading")'), true);
  await call('Fetch.failRequest', { requestId: pausedPageRequest, errorReason: 'Failed' });
  await until(() => js('!!document.querySelector("[role=alert]") && document.body.textContent.includes("Reload page")'), 'lazy page error boundary');
  await call('Fetch.disable');
  console.log('PASS: lazy pages show loading feedback and recoverable errors when their chunk fails.');
} finally {
  if (call && ws?.readyState === WebSocket.OPEN) await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws?.close();
  browser.kill();
  chatResponse?.end();
  for (const response of sessionResponses) response.end();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  const resolvedProfile = path.resolve(profile);
  assert.equal(path.dirname(resolvedProfile), path.resolve(os.tmpdir()));
  assert.ok(path.basename(resolvedProfile).startsWith('k41-solid-browser-'));
  fs.rmSync(resolvedProfile, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
}
