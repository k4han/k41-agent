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
  tools: ['delegate_task', 'read'], sub_agents: ['child'], mcp_servers: [], plan_approval_targets: [],
  context_compact_threshold: 75, system_prompt: '', internal: false,
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
  '/dashboard-api/nav-projects': { projects: [], has_more: false, next_cursor: null },
  '/dashboard-api/catalog': { backends: [{ name: 'local', title: 'Local', enabled: true }] },
  '/dashboard-api/agents/cards': { cards: [agent], agent_names: ['default', 'child'] },
  '/dashboard-api/agents/providers': models,
  '/dashboard-api/agents/workflows': { workflows: ['react_agent'] },
  '/dashboard-api/agents/tools': { tools: ['delegate_task', 'read'], tool_groups: [
    { category: 'agent', tools: ['delegate_task'] }, { category: 'coding', tools: ['read'] },
  ] },
  '/dashboard-api/agents/mcp': { mcp_server_options: [], mcp_installs: {} },
  '/dashboard-api/sessions': { sessions: [] },
  '/dashboard-api/chat-history': { threads: [
    { thread_id: 'history-one', title: 'First history thread', platform: 'dashboard', user_id: 'dashboard', checkpoint_count: 1, workspace_key: 'one', workspace_label: 'Workspace one' },
    { thread_id: 'history-two', title: 'Second history thread', platform: 'dashboard', user_id: 'dashboard', checkpoint_count: 1, workspace_key: 'two', workspace_label: 'Workspace two' },
  ], has_more: false },
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
  assert.equal(await js(`Array.from(document.querySelectorAll('button')).find(button => button.textContent.trim() === 'Save').disabled`), true);
  await clickText('Capabilities & Tools');
  await until(() => js('!!document.querySelector(".agent-config-stat")'), 'agent capabilities');
  assert.equal(await js('document.querySelector(".agent-config-stat strong").textContent'), '1/1');
  assert.equal(await js('Array.from(document.querySelectorAll(".agent-config-option-text")).some(item => item.textContent === "delegate_task")'), false);
  const subagentCheckbox = `Array.from(document.querySelectorAll('.agent-config-option')).find(item => item.textContent.trim() === 'child').querySelector('input')`;
  assert.equal(await js(`(${subagentCheckbox}).checked`), true);
  await js(`(${subagentCheckbox}).click()`);
  await until(() => js('document.querySelectorAll(".agent-config-stat strong")[2].textContent === "0/1"'), 'sub-agent deselection');
  await js(`(${subagentCheckbox}).click()`);
  await clickText('General Config');
  await js(`const input = document.querySelector('input[placeholder="My Agent"]'); input.value = 'Updated default'; input.dispatchEvent(new Event('input', { bubbles: true }));`);
  await clickText('Save');
  await until(() => js('location.pathname === "/settings/agents" && document.querySelector(".agent-list-name")?.textContent === "Updated default"'), 'agent list after mutation');
  assert.deepEqual(agent.tools, ['read']);
  assert.deepEqual(agent.sub_agents, ['child']);
  console.log('PASS: legacy delegation is automatic, hidden from tool choices and omitted from saved tools.');
  console.log('PASS: agent edits invalidate cached list data before SPA navigation.');

  await navigate('/chat');
  await until(() => js('!!document.querySelector(".chat-composer textarea")'), 'chat composer');
  await js('const input = document.querySelector(".chat-composer textarea"); input.value = "Test streaming"; input.dispatchEvent(new Event("input", { bubbles: true }));');
  await js(`document.querySelector('button[aria-label="Send message"]').click()`);
  await until(() => Boolean(chatResponse), 'chat request');
  const send = event => chatResponse.write(JSON.stringify(event) + '\n');
  send({ type: 'thread_created', thread_id: 'streaming-scroll' });
  send({ type: 'message', content: 'Stable heading\n\n```text\ncompleted code\n```\n\nTrailing' });
  await until(() => js('!!document.querySelector(".markdown-code-frame")'), 'first streamed block');
  await js('window.messageRow = document.querySelector(".markdown-code-frame").closest("[data-transcript-item-id]"); window.codeFrame = document.querySelector(".markdown-code-frame");');
  for (let i = 0; i < 5; i += 1) {
    send({ type: 'message', content: ` chunk-${i}` });
    await delay(80);
  }
  assert.equal(await js('messageRow.isConnected && codeFrame.isConnected && document.querySelector(".markdown-code-frame") === codeFrame'), true);
  assert.equal(await js('messageRow.textContent.includes("chunk-4")'), true);
  send({ type: 'message', content: '\n\n```mermaid\nflowchart LR\n  A[Ready] --> B[Streaming]' });
  await until(() => js('!!document.querySelector("pre.markdown-mermaid-source")'), 'unfinished streamed diagram');
  assert.equal(await js('document.querySelector(".markdown-mermaid svg")'), null);
  send({ type: 'message', content: '\n```\n\nContinued narration' });
  await until(() => js('!!document.querySelector(".markdown-mermaid svg.pele")'), 'completed diagram while streaming');
  assert.equal(await js('!!document.querySelector(\'button[aria-label="Stop generating"]\')'), true);
  await js('window.streamingSvg = document.querySelector(".markdown-mermaid svg")');
  for (let index = 0; index < 5; index++) {
    send({ type: 'message', content: ` diagram-tail-${index}` });
    await delay(80);
  }
  assert.equal(await js('streamingSvg === document.querySelector(".markdown-mermaid svg") && messageRow.isConnected && codeFrame.isConnected'), true);
  console.log('PASS: a completed Mermaid fence renders with Pele before the response ends and retains its SVG during subsequent chunks.');
  send({ type: 'message', content: '\n\n' + Array.from({ length: 40 }, (_, index) => `Long narration ${index}.`).join('\n\n') });
  await until(() => js('messageRow.textContent.includes("Long narration 39.")'), 'overflowing streamed message');
  await js('const el = document.querySelector(".transcript"); el.dispatchEvent(new WheelEvent("wheel", { deltaY: -400, bubbles: true })); el.scrollTop = Math.max(0, el.scrollHeight - el.clientHeight - 400)');
  await until(() => js('!!document.querySelector(".scroll-to-bottom-btn")'), 'bottom control for overflowing turn');
  await js('document.querySelector(".scroll-to-bottom-btn").click()');
  await delay(150);
  await js(`window.scrollSamples = []; window.recordScroll = true;
    window.recordScrollFrame = () => {
      const el = document.querySelector('.transcript');
      scrollSamples.push({ top: el.scrollTop, height: el.scrollHeight, viewport: el.clientHeight });
      if (recordScroll) requestAnimationFrame(recordScrollFrame);
    }; recordScrollFrame();`);
  send({ type: 'tool_call', id: 'tool-1', name: 'read_file', args: { path: 'example.txt' } });
  await until(() => js('!!document.querySelector("details.tool-call")'), 'streamed tool call');
  assert.equal(await js('document.querySelector(".activity-group-label").textContent'), '1 tool: 1 read');
  assert.equal(await js('document.querySelector(".transcript-thinking-indicator")'), null);
  await js('window.toolGroup = document.querySelector(".activity-group")');
  await until(() => js('!!codeFrame.querySelector("pre.markdown-code-highlighted, pre.markdown-code-plain")'), 'completed message highlighting during tool execution');
  assert.equal(await js('!!document.querySelector(\'button[aria-label="Stop generating"]\')'), true);
  await delay(150);
  const streamScrollSamples = await js('recordScroll = false; scrollSamples');
  assert.ok(streamScrollSamples[0].top > 500, 'The scroll regression needs an overflowing transcript.');
  assert.ok(streamScrollSamples.every(sample => sample.top >= streamScrollSamples[0].top - 1), 'A tool call must not flash the transcript back toward the top.');
  const lastScrollSample = streamScrollSamples.at(-1);
  assert.ok(lastScrollSample.height - lastScrollSample.viewport - lastScrollSample.top <= 1, 'Following must stay at the bottom after a tool call.');
  await js('window.toolDetails = document.querySelector("details.tool-call"); toolDetails.open = true;');
  send({ type: 'tool_result', tool_call_id: 'tool-1', name: 'read_file', content: 'completed result' });
  await until(() => js('document.querySelector("details.tool-call").textContent.includes("completed result")'), 'tool result');
  await until(() => js('!!document.querySelector(".transcript-thinking-indicator")'), 'model waiting after tool result');
  assert.equal(await js('document.querySelector(".activity-group-label").textContent'), '1 tool: 1 read');
  assert.equal(await js('!!document.querySelector(".activity-group-icon .spin-icon")'), true);
  assert.equal(await js('document.querySelector(".activity-group-check-icon")'), null);
  assert.equal(await js('toolDetails.isConnected && toolDetails.open && document.querySelector("details.tool-call") === toolDetails'), true);
  send({ type: 'tool_call', id: 'shell-1', name: 'bash', args: { command: 'git status --short', workdir: '.' } });
  await until(() => js('document.querySelectorAll("details.tool-call").length === 2'), 'shell summary');
  assert.equal(await js('document.querySelector(".activity-group") === toolGroup'), true);
  assert.equal(await js('document.querySelector(".activity-group-label").textContent'), '2 tools: 1 read, 1 command');
  assert.equal(await js('document.querySelector(".transcript-thinking-indicator")'), null);
  assert.equal(await js('document.querySelectorAll("details.tool-call")[1].querySelector(".tool-call-body")'), null);
  await js('window.shellDetails = document.querySelectorAll("details.tool-call")[1]; shellDetails.open = true;');
  await until(() => js('!!document.querySelector(".tool-call-arguments")'), 'shell parameters');
  assert.equal(await js('document.querySelector(".tool-call-arguments").open'), false);
  send({ type: 'tool_result', tool_call_id: 'shell-1', name: 'bash', content: 'ready\n\nProcess is still running.' });
  await until(() => js('!!document.querySelector(".tool-shell-outcome.is-running")'), 'running shell outcome');
  send({ type: 'tool_result', tool_call_id: 'shell-1', name: 'bash', content: 'failure detail\n\nCommand exited with code 2.' });
  await until(() => js('!!document.querySelector(".tool-shell-outcome.is-error")'), 'failed shell outcome');
  assert.equal(await js('document.querySelector(".tool-shell-outcome").textContent'), 'Command exited with code 2.');
  assert.equal(await js('shellDetails.isConnected && shellDetails.open && shellDetails.querySelector("pre").textContent.includes("git status --short")'), true);
  assert.equal(await js('shellDetails.querySelector(".tool-call-body > pre").textContent'), 'failure detail');
  console.log('PASS: shell parameters stay collapsed while running and failed outcomes update in place.');
  send({ type: 'tool_call', id: 'tool-empty', name: 'read_file', args: { path: 'empty.txt' } });
  await until(() => js('document.querySelector(".activity-group-label").textContent === "3 tools: 2 reads, 1 command (1 failed)"'), 'follow-up tool after completed calls');
  assert.equal(await js('document.querySelector(".transcript-thinking-indicator")'), null);
  send({ type: 'tool_call', id: 'tool-parallel', name: 'read_file', args: { path: 'parallel.txt' } });
  await until(() => js('document.querySelector(".activity-group-label").textContent === "4 tools: 3 reads, 1 command (1 failed)"'), 'parallel tool calls');
  send({ type: 'tool_result', tool_call_id: 'tool-parallel', name: 'read_file', content: 'parallel result' });
  await js('document.querySelectorAll("details.tool-call")[3].open = true;');
  await until(() => js('document.querySelectorAll("details.tool-call")[3].textContent.includes("parallel result")'), 'out-of-order tool result');
  assert.equal(await js('document.querySelector(".activity-group-label").textContent'), '4 tools: 3 reads, 1 command (1 failed)');
  assert.equal(await js('document.querySelector(".transcript-thinking-indicator")'), null);
  await js('document.querySelectorAll("details.tool-call")[2].open = true;');
  send({ type: 'tool_result', tool_call_id: 'tool-empty', name: 'read_file', content: null });
  await until(() => js('document.querySelector(".activity-group-label").textContent === "4 tools: 3 reads, 1 command (1 failed)" && !!document.querySelector(".transcript-thinking-indicator")'), 'empty tool result completion');
  assert.equal(await js('document.querySelectorAll("details.tool-call")[2].textContent.includes("Waiting for tool result")'), false);
  assert.equal(await js('document.querySelectorAll(".activity-group").length === 1 && document.querySelector(".activity-group") === toolGroup'), true);
  const toolGroupHeight = await js('toolGroup.getBoundingClientRect().height');
  const transcriptScrollTop = await js('document.querySelector(".transcript").scrollTop');
  send({ type: 'message', content: 'Second assistant response' });
  await until(() => js('document.body.textContent.includes("Second assistant response")'), 'second Markdown consumer');
  assert.equal(await js('toolGroup.querySelector(".activity-group-toggle").getAttribute("aria-expanded")'), 'true', 'A streamed answer must not collapse the preceding tools.');
  assert.equal(await js('toolGroup.getBoundingClientRect().height'), toolGroupHeight, 'Tool completion must preserve the group height.');
  assert.equal(await js('document.querySelector(".transcript").scrollTop'), transcriptScrollTop, 'A paused transcript must retain its scroll position.');
  assert.equal(await js('document.querySelector(".transcript-thinking-indicator")'), null);
  assert.equal(await js('document.querySelector(".activity-group-label").textContent'), '4 tools: 3 reads, 1 command (1 failed) · 1 failed');
  console.log('PASS: completed tools stop spinning while the model waits, including empty results and follow-up calls in the same group.');
  send({ type: 'tool_call', id: 'manual-1', name: 'read_file', args: { path: 'manual.txt' } });
  await until(() => js('document.querySelectorAll(".activity-group").length === 2'), 'next tool group');
  await js('window.manualGroup = document.querySelectorAll(".activity-group")[1]; manualGroup.querySelector(".activity-group-toggle").click()');
  send({ type: 'tool_call', id: 'manual-2', name: 'read_file', args: { path: 'another.txt' } });
  await until(() => js('manualGroup.querySelector(".activity-group-label").textContent === "2 tools: 2 reads"'), 'follow-up call in manually collapsed group');
  assert.equal(await js('manualGroup.querySelector(".activity-group-body").hidden'), true, 'New calls must respect a manual collapse.');
  send({ type: 'tool_result', tool_call_id: 'manual-1', name: 'read_file', content: 'done' });
  send({ type: 'tool_result', tool_call_id: 'manual-2', name: 'read_file', content: 'done' });
  send({ type: 'message', content: 'Final assistant response' });
  await until(() => js('document.body.textContent.includes("Final assistant response")'), 'final response after manual collapse');
  assert.equal(await js('manualGroup.querySelector(".activity-group-body").hidden'), true);
  assert.equal(await js('themeObserverCounts.active'), 1);
  chatResponse.end();
  await until(() => js(`!document.querySelector('button[aria-label="Stop generating"]')`), 'stream completion');
  assert.equal(await js('manualGroup.querySelector(".activity-group-label").textContent'), '2 tools: 2 reads');
  assert.equal(await js('!!manualGroup.querySelector(".activity-group-check-icon")'), true);
  assert.equal(await js('manualGroup.querySelector(".activity-group-icon .spin-icon")'), null);
  assert.equal(await js('toolGroup.getBoundingClientRect().height'), toolGroupHeight, 'Ending the stream must not collapse tools either.');
  await js('toolGroup.querySelector(".activity-group-toggle").click()');
  assert.equal(await js('toolGroup.querySelector(".activity-group-body").hidden'), true);
  await js('toolGroup.querySelector(".activity-group-toggle").click()');
  assert.equal(await js('toolGroup.querySelector(".activity-group-body").hidden'), false);
  console.log('PASS: streamed tool groups retain their height across answers and completion, and manual toggles stay in control.');
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log('PASS: chat streaming preserves message rows, completed Markdown blocks, and expanded tools.');
  const previousChatResponse = chatResponse;
  await js('(() => { const input = document.querySelector(".chat-composer textarea"); input.value = "Next scroll turn"; input.dispatchEvent(new Event("input", { bubbles: true })); document.querySelector(\'button[aria-label="Send message"]\').click(); })()');
  await until(() => chatResponse !== previousChatResponse, 'next turn request');
  await delay(150);
  await js('scrollSamples = []; recordScroll = true; recordScrollFrame()');
  send({ type: 'tool_call', id: 'next-turn-tool', name: 'read_file', args: { path: 'next.txt' } });
  await until(() => js('document.querySelectorAll(".activity-group").length === 3'), 'tool replacing pending turn status');
  send({ type: 'tool_result', tool_call_id: 'next-turn-tool', name: 'read_file', content: 'done' });
  send({ type: 'message', content: Array.from({ length: 40 }, (_, index) => `Next turn narration ${index}.`).join('\n\n') });
  await until(() => js('document.body.textContent.includes("Next turn narration 39.")'), 'long reply beneath the turn anchor');
  await delay(150);
  const anchorSamples = await js('recordScroll = false; scrollSamples');
  assert.ok(anchorSamples[0].top > 500, 'The turn anchor needs earlier overflowing history.');
  assert.ok(anchorSamples.every(sample => Math.abs(sample.top - anchorSamples[0].top) <= 1), 'Tools and replies must not move the anchored turn between frames.');
  const pausedTop = await js('(() => { const el = document.querySelector(".transcript"); el.dispatchEvent(new WheelEvent("wheel", { deltaY: -300, bubbles: true })); el.scrollTop -= 300; return el.scrollTop; })()');
  send({ type: 'tool_call', id: 'paused-tool', name: 'read_file', args: { path: 'paused.txt' } });
  await until(() => js('document.querySelectorAll(".activity-group").length === 4'), 'tool while reading earlier messages');
  await delay(150);
  assert.equal(await js('document.querySelector(".transcript").scrollTop'), pausedTop, 'Manual scrolling must stay paused during tool layout.');
  await js('document.querySelector(".scroll-to-bottom-btn").click()');
  await delay(150);
  await js('document.querySelectorAll(".activity-group-body")[3].dispatchEvent(new WheelEvent("wheel", { deltaY: -120, bubbles: true }))');
  send({ type: 'tool_result', tool_call_id: 'paused-tool', name: 'read_file', content: 'done' });
  send({ type: 'message', content: 'Reply after scrolling inside tools.\n\n' });
  await until(() => js('document.body.textContent.includes("Reply after scrolling inside tools.")'), 'reply after scrolling inside tools');
  await delay(150);
  assert.equal(await js('(() => { const el = document.querySelector(".transcript"); return el.scrollHeight - el.clientHeight - el.scrollTop <= 1; })()'), true, 'Scrolling inside a tool group must not pause the transcript.');
  await js('document.querySelectorAll(".activity-group-summary")[3].dispatchEvent(new WheelEvent("wheel", { deltaY: 120, bubbles: true }))');
  send({ type: 'message', content: Array.from({ length: 20 }, (_, index) => `Following after tools ${index}.`).join('\n\n') });
  await until(() => js('document.body.textContent.includes("Following after tools 19.")'), 'reply after scrolling down over a tool group');
  await delay(150);
  assert.equal(await js('(() => { const el = document.querySelector(".transcript"); return el.scrollHeight - el.clientHeight - el.scrollTop <= 1; })()'), true, 'Scrolling down at the bottom over a tool group must keep following the next reply.');
  await js('document.querySelector(".transcript").dispatchEvent(new KeyboardEvent("keydown", { key: "End", bubbles: true }))');
  send({ type: 'message', content: '\n\nFollowing after End.' });
  await until(() => js('document.body.textContent.includes("Following after End.")'), 'reply after the End key');
  await delay(150);
  assert.equal(await js('(() => { const el = document.querySelector(".transcript"); return el.scrollHeight - el.clientHeight - el.scrollTop <= 1; })()'), true, 'The End key at the bottom must keep following new content.');
  await js(`(() => {
    const el = document.querySelector('.transcript');
    const touch = clientY => new Touch({ identifier: 1, target: el, clientY });
    el.dispatchEvent(new TouchEvent('touchstart', { touches: [touch(200)], bubbles: true }));
    el.dispatchEvent(new TouchEvent('touchmove', { touches: [touch(100)], bubbles: true }));
    el.dispatchEvent(new TouchEvent('touchend', { touches: [], bubbles: true }));
  })()`);
  send({ type: 'message', content: '\n\nFollowing after touch.' });
  await until(() => js('document.body.textContent.includes("Following after touch.")'), 'reply after swiping toward the bottom');
  await delay(150);
  assert.equal(await js('(() => { const el = document.querySelector(".transcript"); return el.scrollHeight - el.clientHeight - el.scrollTop <= 1; })()'), true, 'Swiping toward the bottom must keep following new content.');
  console.log('PASS: nested tool scrolling stays independent, and downward wheel, keyboard, and touch input keep following at the bottom.');
  chatResponse.end();
  await until(() => js(`!document.querySelector('button[aria-label="Stop generating"]')`), 'next turn completion');
  console.log('PASS: tool calls and streamed replies preserve the turn anchor and respect manual scrolling in a long conversation.');
  await js(`document.querySelector('a[href="/history"]').click()`);
  await until(() => js('location.pathname === "/history" && themeObserverCounts.active === 0'), 'theme observer cleanup');
  console.log('PASS: Markdown consumers share one theme observer and release it after navigation.');
  await until(() => js('document.querySelectorAll(".history-workspace-section").length === 2'), 'history workspace groups');
  await js('window.historyGroups = Array.from(document.querySelectorAll(".history-workspace-section")); window.historyRows = Array.from(document.querySelectorAll(".history-table tbody tr")); document.querySelector(".history-table input").click();');
  await js('window.dispatchEvent(new CustomEvent("k41:thread-title-updated", { detail: { threadId: "history-one", title: "Updated history thread" } }))');
  await until(() => js('document.querySelector(".history-thread-link").textContent.includes("Updated history thread")'), 'history title update');
  assert.equal(await js('historyGroups.every((group, index) => document.querySelectorAll(".history-workspace-section")[index] === group)'), true);
  assert.equal(await js('historyRows.every((row, index) => document.querySelectorAll(".history-table tbody tr")[index] === row)'), true);
  assert.equal(await js('document.querySelector(".history-table input").checked'), true);
  console.log('PASS: history title updates retain workspace sections, thread rows, and selection.');
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
