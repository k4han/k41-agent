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

const provider = { name: 'test', type: 'google', api_key: 'fake-key', default_model: 'model-a', model_profiles: {} };
const profiles = {
  'model-a': { reasoning_effort_levels: ['low', 'high'], reasoning_effort_default: 'high' },
  'gpt-6.1-sol': { reasoning_effort_levels: ['low', 'medium', 'high'], reasoning_effort_default: 'high' },
  'custom-model': { reasoning_effort_levels: null, reasoning_effort_default: null },
  'no-default': { reasoning_effort_levels: ['minimal', 'low', 'high'], reasoning_effort_default: null },
  'unknown-model': { reasoning_effort_levels: null, reasoning_effort_default: null },
  'fixed-model': { reasoning_effort_levels: [], reasoning_effort_default: null },
  'claude-opus-5-5': { reasoning_effort_levels: ['low', 'medium', 'high', 'xhigh', 'max'], reasoning_effort_default: 'medium' },
};
const fieldLabels = { type: 'Provider Type', api_key: 'API Key', default_model: 'Default Model', model_profiles: 'Model Reasoning Profiles (JSON)' };
const settingsPayload = () => ({ settings: {}, by_category: {}, settings_sources: {},
  provider_field_order: Object.keys(fieldLabels), default_provider: 'test', default_model: 'model-a',
  provider_rows: [{ name: 'test', type: 'google', enabled: true, ready: true, is_default: true,
    requires_base_url: false, fields: Object.fromEntries(Object.entries(fieldLabels).map(([field, label]) =>
      [field, { key: `llm.providers.test.${field}`, info: { value: provider[field], label, input_type: 'text', source: 'database' } }])) }],
  providers_catalog: {}, provider_type_options: [],
});
const fixtures = {
  '/dashboard-api/catalog': {},
  '/dashboard-api/agents/cards': { cards: [{ name: 'default', display_name: 'Default', provider: 'test', model: 'model-a', valid: true }] },
  '/dashboard-api/sessions': { sessions: [] },
  '/dashboard-api/chat-history': { threads: [], has_more: false },
  '/dashboard-api/workspace/default': { workspace: null },
  '/dashboard-api/agents/workflows': { workflows: ['react_agent'] },
  '/dashboard-api/agents/tools': { tools: [], tool_groups: [] },
  '/dashboard-api/agents/mcp': { mcp_server_options: [], mcp_installs: {} },
  '/dashboard-api/prompt-variables': { variables: [] },
};
const effortAgent = {
  name: 'effort-agent', display_name: 'Effort Agent', description: '', graph_type: 'react_agent',
  provider: 'test', model: 'claude-opus-5-5', reasoning_effort: 'low',
  tools: [], mcp_servers: [], sub_agents: null, plan_approval_targets: [],
  context_compact_threshold: 75, system_prompt: '', internal: false,
  source: 'user', path: 'effort-agent.md', editable: true, valid: true,
};
fixtures['/dashboard-api/agents/cards'].cards.push(effortAgent);
fixtures['/dashboard-api/agents/cards'].cards.push({
  ...effortAgent, name: 'legacy-agent', display_name: 'Legacy Agent', provider: '', model: 'provider default',
});
let savedAgent;
let sentChat;
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/api/chat/events' && req.method === 'POST') {
    let body = '';
    for await (const chunk of req) body += chunk;
    sentChat = JSON.parse(body);
    res.setHeader('Content-Type', 'application/x-ndjson');
    res.end(); return;
  }
  if (url.pathname.startsWith('/dashboard-api') || url.pathname === '/settings' || url.pathname === '/agents/cards/effort-agent') {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('X-CSRF-Token', 'test-csrf');
    if (url.pathname === '/agents/cards/effort-agent' && req.method === 'PUT') {
      let body = '';
      for await (const chunk of req) body += chunk;
      savedAgent = JSON.parse(body);
      Object.assign(effortAgent, savedAgent);
      res.end(JSON.stringify({ status: 'updated', card: effortAgent })); return;
    }
    if (url.pathname === '/settings' && req.method === 'PUT') {
      let body = '';
      for await (const chunk of req) body += chunk;
      const { values } = JSON.parse(body);
      for (const [key, value] of Object.entries(values)) provider[key.slice('llm.providers.test.'.length)] = value;
      res.end('{"status":"success"}'); return;
    }
    if (url.pathname === '/dashboard-api/providers') { res.end(JSON.stringify(settingsPayload())); return; }
    if (url.pathname === '/dashboard-api/agents/providers') {
      res.end(JSON.stringify({ provider_names: ['test'], default_provider: 'test', default_model: 'model-a',
        model_catalogs: [{ provider: 'test', provider_type: 'google', default_model: 'model-a',
          models: Object.entries(profiles).map(([id, profile]) => ({ id, label: id, source: 'config', ...profile, ...provider.model_profiles[id] })) }] })); return;
    }
    res.end(JSON.stringify(fixtures[url.pathname] || {})); return;
  }
  const relative = url.pathname.startsWith('/dashboard-assets/') ? url.pathname.slice('/dashboard-assets/'.length) : 'index.html';
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
const scratchRoot = path.join(os.tmpdir(), 'k41-effort-ui');
fs.mkdirSync(scratchRoot, { recursive: true });
const profilePath = fs.mkdtempSync(path.join(scratchRoot, 'effort-browser-'));
const browser = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run',
  '--disable-background-networking', '--remote-debugging-port=0', `--user-data-dir=${profilePath}`, 'about:blank'],
{ windowsHide: true, stdio: 'ignore' });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(callback, label, timeout = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await callback()) return;
    await delay(100);
  }
  throw new Error(`Timeout: ${label}`);
}
let ws, call;
try {
  const portFile = path.join(profilePath, 'DevToolsActivePort');
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
  ws.addEventListener('close', () => {
    for (const entry of pending.values()) entry.reject(new Error('Browser closed.'));
    pending.clear();
  });
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const entry = pending.get(message.id); pending.delete(message.id);
      if (message.error) entry?.reject(new Error(message.error.message)); else entry?.resolve(message.result);
    }
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails);
  });
  await call('Page.enable'); await call('Runtime.enable');
  const js = async expression => {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const navigate = route => call('Page.navigate', { url: base + route });
  const effortBadge = '.model-picker-trigger .agent-model-badge-effort';
  const currentEffort = () => js(`document.querySelector('${effortBadge}')?.textContent.toLowerCase() || ''`);
  const openModels = async () => {
    await js('document.querySelector(".model-picker-trigger").click()');
    await until(() => js('!!document.querySelector(".model-item-row")'), 'model menu');
    assert.equal(await js('!!document.querySelector(".model-effort-panel")'), false, 'effort submenu waits for a model row');
    await js(`document.querySelector('.model-item-select[aria-pressed="true"]')?.closest('.model-item-row').querySelector('.model-item-effort-trigger')?.click()`);
  };
  const chooseEffort = async (level, modelId) => {
    await openModels();
    if (modelId) {
      await js(`Array.from(document.querySelectorAll('.model-item-row')).find(row => row.querySelector('.model-item-row-name')?.textContent === ${JSON.stringify(modelId)}).querySelector('.model-item-effort-trigger').click()`);
    }
    await js(`(() => {
      const button = Array.from(document.querySelectorAll('.model-effort-option')).find(button => button.textContent.toLowerCase() === ${JSON.stringify(level || 'auto')});
      button.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
      button.click();
    })()`);
    await until(() => js('!document.querySelector(".agent-model-popover")'), 'effort selected');
  };
  const effortOptions = async () => {
    await openModels();
    const options = await js(`Array.from(document.querySelectorAll('.model-effort-option')).map(button => button.textContent.toLowerCase())`);
    await js('document.querySelector(".agent-model-popover-close").click()');
    return options;
  };
  await navigate('/settings/providers/llm/test');
  const textarea = `Array.from(document.querySelectorAll('.setting-row')).find(row => row.textContent.includes('Model Reasoning Profiles'))?.querySelector('textarea')`;
  await until(() => js(`!!(${textarea})`), 'model profile editor');
  const saveButton = `Array.from(document.querySelectorAll('button')).find(button => button.textContent.includes('Save changes'))`;
  assert.equal(await js(`${saveButton}.disabled`), true);
  const configured = { 'custom-model': { reasoning_effort_levels: ['low', 'medium', 'high', 'max'], reasoning_effort_default: 'max' } };
  await js(`(() => { const input = ${textarea}; input.value = ${JSON.stringify(JSON.stringify(configured))}; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  await js(`${saveButton}.click()`);
  await until(() => js('!!document.querySelector(".dialog-footer")'), 'save confirmation');
  await js(`Array.from(document.querySelectorAll('.dialog-footer button')).find(button => button.textContent.includes('Save')).click()`);
  await until(() => js('!document.querySelector(".dialog-footer")'), 'profiles saved');
  assert.deepEqual(provider.model_profiles, configured);
  await until(() => js(`${saveButton}.disabled`), 'clean saved settings');
  assert.deepEqual(await js(`JSON.parse((${textarea}).value)`), configured);
  await navigate('/chat');
  try {
    await until(() => js(`!!document.querySelector('${effortBadge}')`), 'combined model and effort selector');
  } catch (error) {
    console.log(await js('document.body.textContent.slice(0, 2000)'));
    console.log(JSON.stringify(exceptions));
    throw error;
  }
  assert.equal(await currentEffort(), 'high');
  const selectModel = async modelId => {
    await openModels();
    await js(`Array.from(document.querySelectorAll('.model-item-row')).find(row => row.querySelector('.model-item-row-name').textContent === '${modelId}').click()`);
    await until(() => js('!document.querySelector(".agent-model-popover")'), 'model selected');
  };
  await js('document.querySelector(".model-picker-trigger").click()');
  await until(() => js('!!document.querySelector(".model-item-select")'), 'keyboard model menu');
  await js(`(() => {
    const button = document.querySelector('.model-item-select[aria-pressed="true"]');
    button.focus();
    button.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  })()`);
  assert.equal(await js(`document.activeElement?.matches('.model-effort-option[aria-checked="true"]')`), true, 'keyboard opens the separate effort menu');
  await js(`document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))`);
  await chooseEffort('low');
  await selectModel('custom-model');
  assert.equal(await currentEffort(), 'max');
  assert.deepEqual(await effortOptions(), ['auto', 'low', 'medium', 'high', 'max']);
  await selectModel('no-default');
  assert.equal(await currentEffort(), 'auto');
  assert.deepEqual(await effortOptions(), ['auto', 'minimal', 'low', 'high']);
  for (const modelId of ['unknown-model', 'fixed-model']) {
    await selectModel(modelId);
    assert.equal(await currentEffort(), '');
    assert.deepEqual(await effortOptions(), []);
  }
  await selectModel('model-a');
  assert.equal(await currentEffort(), 'high');
  await chooseEffort('medium', 'custom-model');
  assert.equal(await currentEffort(), 'medium', 'effort survives switching to a different model from the submenu');
  assert.equal(await js('document.querySelector(".model-picker-trigger .agent-model-badge-model").textContent'), 'custom-model');
  await openModels();
  await js(`(() => {
    const row = Array.from(document.querySelectorAll('.model-item-row')).find(row => row.querySelector('.model-item-row-name')?.textContent === 'model-a');
    row.dispatchEvent(new MouseEvent('mouseenter'));
  })()`);
  assert.deepEqual(await js(`Array.from(document.querySelectorAll('.model-effort-option')).map(button => button.textContent.toLowerCase())`), ['auto', 'low', 'high']);
  assert.equal(await currentEffort(), 'medium', 'hovering does not change the selected model or effort');
  await js(`(() => {
    const input = document.querySelector('.agent-model-search-input');
    input.value = 'gpt-6.1-sol'; input.dispatchEvent(new Event('input', { bubbles: true }));
    const button = document.querySelector('.model-item-select');
    button.focus(); button.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  })()`);
  assert.equal(await js(`document.activeElement?.matches('.model-effort-option[aria-checked="true"]')`), true);
  await js(`document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))`);
  assert.equal(await js(`document.activeElement.textContent.toLowerCase()`), 'auto');
  await js(`document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }))`);
  assert.equal(await js(`document.activeElement?.matches('.model-item-select')`), true);
  await js(`document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))`);
  assert.equal(await js('document.activeElement === document.querySelector(".model-picker-trigger")'), true);
  assert.equal(await currentEffort(), 'medium', 'Escape preserves the existing selection');
  await selectModel('gpt-6.1-sol');
  assert.equal(await js('document.querySelector(".model-picker-trigger .agent-model-badge-model").textContent'), 'GPT-6.1 Sol');
  assert.equal(await currentEffort(), 'high');
  await chooseEffort('');
  assert.equal(await currentEffort(), 'auto');
  for (const width of [320, 390, 1440]) {
    await call('Emulation.setDeviceMetricsOverride', { width, height: 844, deviceScaleFactor: 1, mobile: width < 500 });
    await delay(150);
    assert.equal(await js('document.documentElement.scrollWidth <= innerWidth'), true);
    assert.equal(await js(`getComputedStyle(document.querySelector('${effortBadge}')).display !== 'none'`), true);
    await openModels();
    assert.equal(await js(`(() => { const rect = document.querySelector('.agent-model-popover').getBoundingClientRect(); return rect.left >= 0 && rect.right <= innerWidth && rect.top >= 0 && rect.bottom <= innerHeight; })()`), true);
    assert.equal(await js(`(() => { const panel = document.querySelector('.model-effort-panel'); return panel.scrollWidth <= panel.clientWidth; })()`), true);
    assert.equal(await js(`(() => {
      const menu = document.querySelector('.agent-model-popover');
      const panel = document.querySelector('.model-effort-panel');
      const rect = panel.getBoundingClientRect();
      return !menu.contains(panel) && rect.left >= 0 && rect.right <= innerWidth && rect.top >= 0 && rect.bottom <= innerHeight;
    })()`), true, 'effort is a separate floating menu inside the viewport');
    if (width === 1440) {
      assert.equal(await js(`(() => {
        const parent = document.querySelector('.agent-model-popover').getBoundingClientRect();
        const panel = document.querySelector('.model-effort-panel').getBoundingClientRect();
        return panel.left >= parent.right || panel.right <= parent.left;
      })()`), true, 'desktop effort submenu sits beside the model menu');
    }
    if (width === 1440) {
      await js(`document.documentElement.classList.add('dark')`);
      const screenshot = await call('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(path.join(scratchRoot, 'model-effort-desktop.png'), Buffer.from(screenshot.data, 'base64'));
    }
    await js('document.querySelector(".agent-model-popover-close").click()');
  }
  await js('document.querySelector(".agent-picker-trigger").click()');
  await until(() => js('!!document.querySelector(".agent-card-item")'), 'agent menu');
  await js('Array.from(document.querySelectorAll(".agent-card-item")).find(item => item.querySelector(".agent-card-item-name").textContent.trim() === "Legacy Agent").click()');
  assert.equal(await currentEffort(), 'low');
  assert.equal(await js('!!document.querySelector(".agent-model-popover")'), false, 'agent selection closes its menu');
  await chooseEffort('');
  await js('(() => { const input = document.querySelector(".chat-prompt-input"); input.value = "Test automatic effort"; input.dispatchEvent(new Event("input", { bubbles: true })); })()');
  await js(`document.querySelector('button[aria-label="Send message"]').click()`);
  await until(() => sentChat !== undefined, 'explicit Auto chat request');
  assert.equal(sentChat.reasoning_effort, 'auto');
  assert.equal(sentChat.agent_name, 'legacy-agent');
  await until(() => js(`!!document.querySelector('button[aria-label="Send message"]')`), 'mock chat completed');
  await selectModel('custom-model');
  assert.equal(await currentEffort(), 'max');
  sentChat = undefined;
  await chooseEffort('low', 'gpt-6.1-sol');
  await js('(() => { const input = document.querySelector(".chat-prompt-input"); input.value = "Test explicit effort"; input.dispatchEvent(new Event("input", { bubbles: true })); })()');
  await js(`document.querySelector('button[aria-label="Send message"]').click()`);
  await until(() => sentChat !== undefined, 'explicit effort chat request');
  assert.equal(sentChat.reasoning_effort, 'low');
  assert.equal(sentChat.model, 'gpt-6.1-sol');
  await navigate('/settings/agents/effort-agent');
  const agentEffort = 'button[aria-label="Agent reasoning effort"]';
  await until(() => js(`!!document.querySelector('${agentEffort}')`), 'agent card effort editor');
  assert.equal(await js(`document.querySelector('${agentEffort}').disabled`), false);
  const compactInput = `Array.from(document.querySelectorAll('.field')).find(field => field.querySelector('label')?.textContent === 'Context Compact Threshold (%)')?.querySelector('input')`;
  assert.equal(await js(`(${compactInput}).value`), '75');
  assert.equal(await js(`(${compactInput}).min`), '1');
  assert.equal(await js(`(${compactInput}).max`), '100');
  await js(`(() => { const input = ${compactInput}; input.value = '80'; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  assert.equal(await js(`document.querySelector('${agentEffort}').textContent.trim()`), 'low');
  await js(`document.querySelector('${agentEffort}').click()`);
  const effortMenu = '[role="listbox"][aria-label="Agent reasoning effort"]';
  assert.deepEqual(await js(`Array.from(document.querySelectorAll('${effortMenu} [role="option"]')).map(option => option.textContent.trim())`),
    ['Auto (medium)', 'low', 'medium', 'high', 'xhigh', 'max']);
  await js(`Array.from(document.querySelectorAll('${effortMenu} [role="option"]')).find(option => option.textContent.trim() === 'high').click()`);
  assert.equal(await js(`document.querySelector('${agentEffort}').textContent.trim()`), 'high');
  const agentSave = `Array.from(document.querySelectorAll('button')).find(button => button.textContent.trim() === 'Save')`;
  await js(`${agentSave}.click()`);
  await until(() => savedAgent?.reasoning_effort === 'high', 'agent effort saved');
  assert.equal(savedAgent.context_compact_threshold, 80);
  assert.equal('context_trim_threshold' in savedAgent, false);
  assert.equal('max_context_tokens' in savedAgent, false);
  await until(() => js('location.pathname === "/settings/agents"'), 'saved agent navigation');
  await navigate('/settings/agents/effort-agent');
  await until(() => js(`!!document.querySelector('${agentEffort}')`), 'agent effort reopened');
  assert.equal(await js(`document.querySelector('${agentEffort}').textContent.trim()`), 'high');
  assert.equal(await js(`(${compactInput}).value`), '80');
  await js(`document.querySelector('${agentEffort}').click()`);
  await js(`document.querySelector('${effortMenu} [role="option"]').click()`);
  await js(`${agentSave}.click()`);
  await until(() => savedAgent?.reasoning_effort === null, 'agent effort reset to Auto');
  assert.deepEqual(exceptions, []);
  console.log('PASS: model profiles, chat effort, explicit Auto request, mobile widths, agent card effort choices, save, reload, and Auto reset.');
} finally {
  await Promise.race([call?.('Browser.close').catch(() => {}), delay(2000)]);
  ws?.close(); browser.kill();
  if (browser.exitCode === null && browser.signalCode === null) {
    await Promise.race([new Promise(resolve => browser.once('exit', resolve)), delay(3000)]);
  }
  await new Promise(resolve => server.close(resolve));
  if (path.dirname(profilePath) !== scratchRoot || !path.basename(profilePath).startsWith('effort-browser-')) {
    throw new Error('Refusing to remove a browser profile outside the test directory.');
  }
  try { fs.rmSync(profilePath, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); }
  catch (error) {
    if (!['EPERM', 'EACCES', 'EBUSY', 'ENOTEMPTY'].includes(error.code)) throw error;
    console.log('Browser profile is still locked; retained in the temporary directory.');
  }
}
