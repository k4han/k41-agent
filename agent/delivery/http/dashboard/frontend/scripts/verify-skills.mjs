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

let removed = false;
let fileVersion = 'resource-v1';
let guide = '# Reference guide';
let enabled = true;
let savedDocument;
let documentVersion = 'document-v1';
let imported = false;
let savedSettings;
let skillWorkspace;
let sentChat;
let skillFailure = false;
const chatFixtures = {
  '/dashboard-api/catalog': {},
  '/dashboard-api/agents/cards': { cards: [{ name: 'default', display_name: 'Default', provider: 'test', model: 'demo-model', valid: true }] },
  '/dashboard-api/agents/providers': { provider_names: ['test'], default_provider: 'test', default_model: 'demo-model', model_catalogs: [{ provider: 'test', provider_type: 'openai', default_model: 'demo-model', models: [{ id: 'demo-model', label: 'Demo model', source: 'config', reasoning_effort_levels: [] }] }] },
  '/dashboard-api/sessions': { sessions: [] },
  '/dashboard-api/chat-history': { threads: [], has_more: false },
  '/dashboard-api/chat-history/scoped': { thread_id: 'scoped', messages: [], platform: 'web', user_id: 'test', channel_id: 'test', workspace: { execution: { backend: 'local', locator: 'D:/selected-workspace', metadata: {} } } },
  '/dashboard-api/workspace/tree': { path: '', root: 'D:/selected-workspace', entries: [], truncated: false },
  '/dashboard-api/workspace/changes': { entries: [], changes: [], summary: {} },
};
const settings = { 'skills.repository_dir': { value: '.agent/skills' }, 'skills.additional_roots': { value: [] },
  'skills.local_execution_mode': { value: 'snapshot' }, 'skills.cache_root': { value: '' } };
const detail = { id: 'demo-id', name: 'demo', description: 'A skill with resources.', enabled: true, shadowed: false,
  source: { scope: 'global', root: 'D:/test/skills', directory: 'demo' }, diagnostics: [],
  content: '---\nname: demo\ndescription: A skill with resources.\ncustom: keep\n---\n# Instructions\n',
  file_version: documentVersion, frontmatter: { name: 'demo', description: 'A skill with resources.', custom: 'keep', metadata: { version: '1' } }, body: '# Instructions' };
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/api/chat/events' && req.method === 'POST') {
    let body = ''; for await (const chunk of req) body += chunk;
    sentChat = JSON.parse(body); res.setHeader('Content-Type', 'application/x-ndjson'); res.end(); return;
  }
  if (url.pathname.startsWith('/dashboard-api') || url.pathname === '/settings' || url.pathname === '/health') {
    res.setHeader('Content-Type', 'application/json'); res.setHeader('X-CSRF-Token', 'skills-csrf');
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const body = Buffer.concat(chunks).toString();
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(req.method)) assert.equal(req.headers['x-csrf-token'], 'skills-csrf');
    const reply = value => res.end(JSON.stringify(value));
    if (chatFixtures[url.pathname]) return reply(chatFixtures[url.pathname]);
    if (url.pathname === '/dashboard-api/workspace/default') return reply({ workspace: { execution: { backend: 'local', locator: 'D:/workspace', metadata: {} } } });
    if (url.pathname === '/dashboard-api/skills') return reply({ settings });
    if (url.pathname === '/settings' && req.method === 'PUT') {
      savedSettings = JSON.parse(body).values;
      for (const [key, value] of Object.entries(savedSettings)) settings[key] = { value };
      return reply({ status: 'updated' });
    }
    if (url.pathname === '/dashboard-api/skill-packages') {
      skillWorkspace = url.searchParams.get('workspace');
      if (skillFailure) { res.statusCode = 500; return reply({ detail: 'Skills temporarily unavailable' }); }
      return reply({ packages: removed ? [] : [{ ...detail, enabled },
        { ...detail, id: 'alpha', name: 'alpha', description: 'Another available skill.' },
        { ...detail, id: 'disabled', name: 'disabled', enabled: false },
        { ...detail, id: 'shadowed', name: 'shadowed', shadowed: true }] });
    }
    if (url.pathname === '/dashboard-api/skill-packages/imports/preview') return reply({ preview_id: 'preview-id', commit: null, skills: [{ name: 'imported', description: 'A preview', diagnostics: [], conflict: false }] });
    if (url.pathname === '/dashboard-api/skill-packages/imports') { imported = true; return reply({ status: 'imported' }); }
    if (url.pathname === '/dashboard-api/skill-packages/demo-id') {
      if (req.method === 'DELETE') { removed = true; return reply({ status: 'deleted' }); }
      if (req.method === 'PATCH') { enabled = JSON.parse(body).enabled; return reply({ status: 'updated' }); }
      return reply({ package: { ...detail, file_version: documentVersion } });
    }
    if (url.pathname === '/dashboard-api/skill-packages/demo-id/document') {
      savedDocument = JSON.parse(body); assert.equal(savedDocument.expected_version, documentVersion);
      detail.frontmatter = { ...detail.frontmatter, ...savedDocument.frontmatter }; detail.body = savedDocument.body;
      documentVersion = 'document-v2'; return reply({ version: documentVersion });
    }
    if (url.pathname === '/dashboard-api/skill-packages/demo-id/files') {
      if (req.method === 'POST') return reply({ status: 'updated' });
      return reply({ entries: url.searchParams.get('path') === 'docs' ? [{ name: 'guide.md', path: 'docs/guide.md', kind: 'file', size: guide.length }] :
        [{ name: 'docs', path: 'docs', kind: 'directory', size: 0 }, { name: 'template.bin', path: 'template.bin', kind: 'file', size: 3 }], next_offset: null });
    }
    if (url.pathname === '/dashboard-api/skill-packages/demo-id/files/docs/guide.md') {
      if (req.method === 'PUT') {
        if (req.headers['if-match'] !== fileVersion) { res.statusCode = 409; return reply({ detail: 'File changed; reload before saving.' }); }
        guide = body; fileVersion = 'resource-v2'; return reply({ version: fileVersion });
      }
      return reply({ path: 'docs/guide.md', content: guide, version: fileVersion, size: guide.length, mime_type: 'text/markdown' });
    }
    if (url.pathname === '/dashboard-api/skill-packages/demo-id/files/template.bin') return reply({ path: 'template.bin', content: null, version: 'binary-v1', size: 3, mime_type: 'application/octet-stream' });
    return reply({ tools: [], agents: [], sessions: [], threads: [], settings: {}, by_category: {}, backends: [], providers: [], current_version: 'test' });
  }
  const relative = url.pathname.startsWith('/dashboard-assets/') ? url.pathname.slice('/dashboard-assets/'.length) : 'index.html';
  const target = path.resolve(staticRoot, relative);
  if (!target.startsWith(staticRoot + path.sep)) { res.writeHead(403); res.end(); return; }
  try { res.setHeader('Content-Type', { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' }[path.extname(target)] || 'application/octet-stream'); res.end(fs.readFileSync(target)); }
  catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const scratchRoot = path.join(os.tmpdir(), 'k41-skills-ui'); fs.mkdirSync(scratchRoot, { recursive: true });
const profile = fs.mkdtempSync(path.join(scratchRoot, 'skills-browser-'));
const browser = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-networking', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(callback, label, timeout = 15000) {
  const start = Date.now(); while (Date.now() - start < timeout) { if (await callback()) return; await delay(100); }
  throw new Error(`Timeout: ${label}`);
}
let ws, call;
try {
  const portFile = path.join(profile, 'DevToolsActivePort'); await until(() => fs.existsSync(portFile), 'browser');
  const port = Number(fs.readFileSync(portFile, 'utf8').split('\n')[0]);
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  ws = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
  await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }));
  let sequence = 0; const pending = new Map(); const exceptions = [];
  call = (method, params = {}) => new Promise((resolve, reject) => { const id = ++sequence; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })); });
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data); if (message.id) { const entry = pending.get(message.id); pending.delete(message.id); if (message.error) entry?.reject(new Error(message.error.message)); else entry?.resolve(message.result); }
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails);
  });
  await call('Page.enable'); await call('Runtime.enable');
  const js = async expression => { const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails)); return result.result.value; };
  const button = label => `Array.from(document.querySelectorAll('button')).find(item => item.textContent.trim() === ${JSON.stringify(label)})`;
  const click = async label => { await until(() => js(`!!(${button(label)}) && !(${button(label)}).disabled`), label); await js(`${button(label)}.click()`); };
  await call('Page.navigate', { url: base + '/chat' });
  await until(() => js('!!document.querySelector(".chat-prompt-input") && !document.querySelector(".chat-prompt-input").disabled'), 'chat input');
  assert.equal(await js(`!!document.querySelector('button[aria-label="Select skill"]')`), false, 'The old skill button must be removed.');
  const typePrompt = async (value, caret = value.length) => js(`(() => { const input = document.querySelector('.chat-prompt-input'); input.focus(); input.value = ${JSON.stringify(value)}; input.setSelectionRange(${caret}, ${caret}); input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  const key = async value => { await call('Input.dispatchKeyEvent', { type: 'keyDown', key: value }); await call('Input.dispatchKeyEvent', { type: 'keyUp', key: value }); };
  const waitOptions = async () => {
    try {
      await until(() => js(`document.querySelectorAll('.chat-skill-suggestion').length > 0 && document.querySelector('#chat-skill-suggestions[aria-busy="false"]') !== null`), 'skill suggestions');
    } catch (error) {
      console.log(await js(`JSON.stringify({ input: document.querySelector('.chat-prompt-input')?.outerHTML, value: document.querySelector('.chat-prompt-input')?.value, caret: document.querySelector('.chat-prompt-input')?.selectionStart, menu: document.querySelector('.chat-skill-suggestions')?.outerHTML, focused: document.activeElement?.outerHTML })`));
      console.log(JSON.stringify({ skillWorkspace, exceptions }));
      throw error;
    }
  };
  await typePrompt('/'); await waitOptions();
  assert.equal(await js('document.querySelectorAll(".chat-skill-suggestion").length'), 2, 'Disabled and shadowed packages must be excluded.');
  assert.equal(skillWorkspace, null, 'An unselected workspace must use the shared skill inventory.');
  await key('ArrowDown');
  assert.equal(await js('document.querySelector(".chat-skill-suggestion.active strong").textContent'), 'demo');
  await key('Enter');
  assert.equal(await js('document.querySelector(".chat-prompt-input").value'), '/skill demo ');
  assert.equal(sentChat, undefined, 'Selecting a skill must not send a message.');
  assert.equal(await js('document.activeElement.classList.contains("chat-prompt-input")'), true);
  await typePrompt('/DEM'); await waitOptions();
  assert.equal(await js('document.querySelectorAll(".chat-skill-suggestion").length'), 1);
  await key('Tab');
  assert.equal(await js('document.querySelector(".chat-prompt-input").value'), '/skill demo ');
  await typePrompt('/skill refresh de'); await waitOptions();
  await js('document.querySelector(".chat-skill-suggestion").dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true })); document.querySelector(".chat-skill-suggestion").click()');
  assert.equal(await js('document.querySelector(".chat-prompt-input").value'), '/skill refresh demo ');
  await typePrompt('/skill unload de'); await waitOptions(); await key('Enter');
  assert.equal(await js('document.querySelector(".chat-prompt-input").value'), '/skill unload demo ');
  await typePrompt('/'); await waitOptions(); await key('Escape');
  assert.equal(await js('!!document.querySelector(".chat-skill-suggestions")'), false);
  await typePrompt('https://example.com');
  assert.equal(await js('!!document.querySelector(".chat-skill-suggestions")'), false);
  await typePrompt('/no-matching-skill');
  await until(() => js('document.body.textContent.includes("No matching skills available")'), 'no matching skills');
  await typePrompt('/skill de Keep this task', '/skill de'.length); await waitOptions(); await key('Enter');
  assert.equal(await js('document.querySelector(".chat-prompt-input").value'), '/skill demo Keep this task');
  await call('Page.navigate', { url: base + '/c/scoped' });
  await until(() => js(`document.querySelector('.chat-prompt-input')?.placeholder === 'Reply or attach files...' && !document.querySelector('.chat-prompt-input').disabled`), 'scoped conversation');
  await typePrompt('/'); await waitOptions();
  assert.equal(JSON.parse(skillWorkspace).locator, 'D:/selected-workspace', 'Suggestions must use the selected workspace.');
  await js(`document.querySelector('button[aria-label="Close workspace explorer"]')?.click()`);
  await typePrompt('');
  skillFailure = true; await typePrompt('/');
  await until(() => js('document.body.textContent.includes("Skills temporarily unavailable")'), 'skill API error');
  skillFailure = false; await typePrompt(''); await typePrompt('/'); await waitOptions();
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  assert.equal(await js('document.documentElement.scrollWidth <= window.innerWidth + 1'), true, 'Suggestions must fit the mobile viewport.');
  assert.equal(await js('(() => { const rect = document.querySelector(".chat-skill-suggestions").getBoundingClientRect(); return rect.top >= 0 && rect.left >= 0 && rect.right <= window.innerWidth; })()'), true, 'Suggestions must remain visible on mobile.');
  await call('Emulation.clearDeviceMetricsOverride');
  await call('Page.navigate', { url: base + '/settings/skills' });
  await until(() => js(`!!document.querySelector('#local-skill-execution') && !(${button('Save sources')}).disabled`), 'execution settings');
  assert.equal(await js(`document.querySelector('#local-skill-execution').value`), 'snapshot');
  await js(`(() => { const input = document.querySelector('#local-skill-execution'); input.value = 'source'; input.dispatchEvent(new Event('change', { bubbles: true })); const cache = document.querySelector('#skill-cache-root'); cache.value = 'D:/skill-cache'; cache.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  await click('Save sources'); await until(() => Boolean(savedSettings), 'execution settings saved');
  assert.equal(savedSettings['skills.local_execution_mode'], 'source'); assert.equal(savedSettings['skills.cache_root'], 'D:/skill-cache');
  await until(() => js(`document.body.textContent.includes('Sources saved.') && !(${button('Save sources')}).disabled`), 'execution settings settled');
  assert.equal(await js(`document.querySelector('#local-skill-execution').value`), 'source');
  await click('Open package'); await until(() => js(`!!document.querySelector('[role="dialog"] textarea')`), 'package editor');
  await js(`(() => { const textarea = Array.from(document.querySelectorAll('label')).find(item => item.textContent === 'Description').parentElement.querySelector('textarea'); textarea.value = 'Updated description'; textarea.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  await click('Save SKILL.md'); await until(() => Boolean(savedDocument), 'document save'); assert.equal(savedDocument.frontmatter.description, 'Updated description'); assert.equal(detail.frontmatter.custom, 'keep');
  await click('docs'); await click('guide.md'); await until(() => js(`!!document.querySelector('[aria-label="Resource content"]')`), 'reference file');
  await js(`(() => { const input = document.querySelector('[aria-label="Resource content"]'); input.value = '# Updated guide'; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  await click('Save file'); await until(() => guide === '# Updated guide', 'resource save');
  await until(() => js(`document.body.textContent.includes('File saved.') && !(${button('Save file')}).disabled`), 'save settled');
  await js(`(() => { const input = Array.from(document.querySelectorAll('label')).find(item => item.textContent.trim() === 'Replace file').querySelector('input[type="file"]'); const transfer = new DataTransfer(); transfer.items.add(new File(['# Replacement guide'], 'guide.md', { type: 'text/markdown' })); input.files = transfer.files; input.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await until(async () => await js(`!(${button('Save file')}).disabled`) && guide === '# Replacement guide', 'replacement upload');
  assert.equal(await js(`document.querySelector('[aria-label="Resource content"]').value`), '# Replacement guide');
  await click('Save file'); await until(() => js(`!(${button('Save file')}).disabled`), 'replacement save');
  assert.equal(guide, '# Replacement guide', 'Saving after replacement must preserve the uploaded content.');
  fileVersion = 'externally-changed'; await click('Save file'); await until(() => js(`document.body.textContent.includes('File changed; reload before saving.')`), 'stale version warning');
  await click('Parent directory'); await click('template.bin'); await until(() => js(`document.body.textContent.includes('Binary or large resource')`), 'binary handling');
  await js(`document.querySelector('[role="dialog"] .dialog-close-btn').click()`); await click('Disable'); await until(() => !enabled, 'disable');
  await click('Import'); await js(`(() => { const input = document.querySelector('[aria-label="Import source"]'); input.value = 'directory'; input.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await js(`(() => { const input = document.querySelector('[aria-label="Import location"]'); input.value = 'D:/example'; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  await click('Preview import'); await click('Import 1 packages'); await until(() => imported, 'import commit');
  await click('Delete package'); await until(() => js(`document.querySelectorAll('[role="dialog"] button').length > 0`), 'delete confirmation');
  await js(`Array.from(document.querySelectorAll('[role="dialog"] button')).find(item => item.textContent.trim() === 'Delete package').click()`);
  await until(() => js(`document.body.textContent.includes('No skills found in this scope.')`), 'whole package deletion');
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  assert.equal(await js('document.documentElement.scrollWidth <= window.innerWidth + 1'), true, 'Skills page must fit the mobile viewport.');
  assert.deepEqual(exceptions, []); console.log('PASS: slash skill suggestions, keyboard and mouse selection, workspace scope, lifecycle commands, API errors, mobile layout, execution settings, package editor, metadata, file save/replacement, stale versions, binary resources, disable, import and deletion.');
} finally {
  await Promise.race([call?.('Browser.close').catch(() => {}), delay(2000)]); ws?.close(); browser.kill();
  if (browser.exitCode === null && browser.signalCode === null) await Promise.race([new Promise(resolve => browser.once('exit', resolve)), delay(3000)]);
  server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  assert.equal(path.dirname(profile), scratchRoot); assert.ok(path.basename(profile).startsWith('skills-browser-'));
  try { fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); }
  catch (error) { if (!['EPERM', 'EACCES', 'EBUSY', 'ENOTEMPTY'].includes(error.code)) throw error; }
}
