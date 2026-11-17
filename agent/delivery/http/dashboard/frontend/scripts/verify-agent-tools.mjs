import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const frontendRoot = fileURLToPath(new URL('..', import.meta.url));
const executable =
  process.env.BROWSER_EXECUTABLE ||
  [
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ].find((candidate) => fs.existsSync(candidate));
assert.ok(executable, 'Set BROWSER_EXECUTABLE to run browser checks.');
const toolGroups = [
  {
    category: 'file',
    tools: ['read', 'list_dir', 'glob', 'grep', 'edit', 'write'],
  },
  {
    category: 'shell',
    tools: [
      'bash',
      'read_process_output',
      'write_process_input',
      'stop_process',
    ],
  },
  { category: 'web', tools: ['web_search', 'web_fetch'] },
  { category: 'image', tools: ['generate_image'] },
  { category: 'utility', tools: ['get_current_time'] },
];
const toolNames = toolGroups.flatMap((group) => group.tools);
const field = (name, input_type, extra = {}) => ({
  name,
  input_type,
  label: name.replaceAll('_', ' '),
  description: '',
  default: null,
  required: false,
  secret: false,
  options: [],
  ...extra,
});
const schemas = {
  web_search: {
    fields: [
      field('provider', 'select', {
        options: ['auto', 'tavily', 'duckduckgo'],
        default: 'auto',
      }),
      field('tavily_connection', 'select', {
        options: ['__default__', 'research'],
        show_when: { provider: ['auto', 'tavily'] },
      }),
      field('tavily_api_key', 'password', {
        secret: true,
        show_when: { provider: ['auto', 'tavily'] },
      }),
      field('include_raw', 'boolean', { default: true }),
      field('result_limit', 'number', { min: 1, max: 10, default: 5 }),
    ],
  },
  generate_image: {
    fields: [
      field('provider', 'text'),
      field('model', 'text'),
      field('size', 'select', {
        default: '1024x1024',
        options: ['1024x1024', '1536x1024'],
      }),
    ],
  },
};
const card = (name, extra = {}) => ({
  name,
  display_name: name === 'research-assistant' ? 'Research assistant' : name,
  description: 'Research and summarize useful sources.',
  graph_type: 'react_agent',
  provider: 'demo',
  model: 'text-model',
  permission_mode: null,
  tool_permissions: null,
  tools: ['read', 'list_dir', 'web_search', 'web_fetch'],
  tool_configs: {},
  sub_agents: ['backend'],
  plan_approval_targets: ['reviewer'],
  internal: false,
  context_compact_threshold: 75,
  system_prompt: '',
  source: 'user',
  path: `/agents/${name}.md`,
  editable: true,
  valid: true,
  overrides_builtin: false,
  error: '',
  ...extra,
});
const cards = [
  card('research-assistant'),
  card('backend'),
  card('reviewer'),
  card('builtin', { source: 'builtin', editable: false }),
  card('acp', {
    graph_type: 'acp_agent',
    acp: { agent: 'antigravity-cli', agent_name: null, use_default_tools: true, config_options: { model: 'legacy-model' } },
    permission_mode: 'ask',
  }),
  card('empty', { tools: [], sub_agents: [], plan_approval_targets: [] }),
];
const installs = Object.fromEntries(
  cards.map((item) => [
    item.name,
    [
      {
        id: 1,
        install_id: 1,
        agent_name: item.name,
        server_name: 'github',
        registry_name: 'github',
        registry_version: '1',
        title: 'GitHub',
        description: 'Issues and repository context.',
        verified: true,
        source_type: 'registry',
        transport: 'stdio',
        enabled: true,
        agent_enabled: false,
      },
    ],
  ]),
);
const writes = [];
let failMcp = false;
const server = await createServer({
  root: frontendRoot,
  configFile: path.join(frontendRoot, 'vite.config.ts'),
  base: '/',
  plugins: [
    {
      name: 'agent-tools-test-page',
      configureServer(vite) {
        vite.middlewares.use(async (req, res, next) => {
          const url = new URL(req.url, 'http://localhost');
          if (
            req.method === 'GET' &&
            (url.pathname.startsWith('/__agent-tools/') ||
              url.pathname === '/settings/agents' ||
              url.pathname === '/settings/providers')
          ) {
            res.setHeader('Content-Type', 'text/html');
            res.end(
              '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/fixtures/agent-tools.tsx"></script></body></html>',
            );
            return;
          }
          if (
            !url.pathname.startsWith('/dashboard-api/') &&
            !url.pathname.startsWith('/api/mcp/') &&
            !url.pathname.startsWith('/agents/cards/')
          )
            return next();
          res.setHeader('Content-Type', 'application/json');
          res.setHeader('X-CSRF-Token', 'test-token');
          const body =
            req.method === 'GET'
              ? {}
              : JSON.parse(
                  await new Promise((resolve) => {
                    let value = '';
                    req.on('data', (chunk) => (value += chunk));
                    req.on('end', () => resolve(value || '{}'));
                  }),
                );
          if (req.method !== 'GET')
            writes.push({ method: req.method, path: url.pathname, body });
          let payload = {};
          if (req.method === 'PUT' && url.pathname === '/agents/cards/acp') {
            const saved = cards.find(item => item.name === 'acp');
            Object.assign(saved, body);
            payload = { status: 'ok', card: saved };
          }
          if (url.pathname === '/dashboard-api/agents/cards')
            payload = { cards, agent_names: cards.map((item) => item.name) };
          if (url.pathname === '/dashboard-api/agents/tools')
            payload = {
              tools: toolNames,
              tool_groups: toolGroups,
              tool_descriptions: {
                web_search: 'Find current facts from reliable sources.',
                read: 'Read workspace file contents.',
              },
              tool_config_schemas: schemas,
              tool_config_effective: {
                web_search: { provider: 'tavily', include_raw: true },
              },
              acp_unavailable_tools: ['read'],
            };
          if (url.pathname === '/dashboard-api/agents/workflows')
            payload = { workflows: ['react_agent', 'acp_agent'] };
          if (url.pathname === '/dashboard-api/agents/providers')
            payload = {
              provider_names: ['demo'],
              default_provider: 'demo',
              default_model: 'text-model',
              model_catalogs: [
                {
                  provider: 'demo',
                  provider_type: 'demo',
                  default_model: 'text-model',
                  can_list_models: false,
                  error: null,
                  models: [
                    {
                      id: 'text-model',
                      label: 'Text model',
                      source: 'config',
                      output_types: ['text'],
                    },
                    {
                      id: 'image-model',
                      label: 'Image model',
                      source: 'config',
                      output_types: ['image'],
                    },
                  ],
                },
              ],
            };
          if (url.pathname === '/dashboard-api/agents/mcp')
            payload = {
              mcp_server_options: ['github', 'empty-server'],
              mcp_installs: installs,
              mcp_tools: {
                github: ['mcp__github__search'],
                'empty-server': [],
              },
            };
          if (url.pathname === '/dashboard-api/prompt-variables')
            payload = { variables: [] };
          if (url.pathname === '/dashboard-api/acp/agents')
            payload = { agents: [], definitions: [] };
          if (url.pathname.startsWith('/dashboard-api/acp/agents/'))
            payload = { auth: { signed_in: true, config_options: [
              { id: 'model', name: 'ACP model', category: 'model', current_value: 'fake-pro',
                options: [{ value: 'fake-pro', name: 'ACP Pro' }, { value: 'fake-lite', name: 'ACP Fast' }] },
              { id: 'thinking_level', name: 'Thinking level', category: 'thought_level', current_value: 'high',
                options: [{ value: 'high', name: 'High' }, { value: 'low', name: 'Low' }] },
            ] } };
          if (url.pathname.startsWith('/api/mcp/')) {
            if (failMcp) {
              res.statusCode = 500;
              res.end(JSON.stringify({ detail: 'Connection unavailable.' }));
              return;
            }
            const name = decodeURIComponent(url.pathname.split('/')[4]);
            if (url.pathname.endsWith('/toggle'))
              installs[name][0].agent_enabled = body.enabled;
            else
              installs[name].push({
                ...installs[name][0],
                install_id: 2,
                server_name: body.server_name,
                title: body.server_name,
                agent_enabled: body.enabled,
              });
            payload = { status: 'ok' };
          }
          res.end(JSON.stringify(payload));
        });
      },
    },
  ],
  server: { host: '127.0.0.1', port: 0, open: false },
});
await server.listen();
const base = `http://127.0.0.1:${server.httpServer.address().port}`;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'k41-agent-tools-'));
const browser = spawn(
  executable,
  [
    '--headless=new',
    '--no-first-run',
    '--disable-background-networking',
    '--remote-debugging-port=0',
    `--user-data-dir=${profile}`,
    'about:blank',
  ],
  { windowsHide: true, stdio: 'ignore' },
);
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(callback, label, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await callback()) return;
    await delay(60);
  }
  throw new Error(`Timeout: ${label}`);
}
let ws, call;
try {
  const portFile = path.join(profile, 'DevToolsActivePort');
  await until(() => fs.existsSync(portFile), 'Chrome startup');
  const port = Number(fs.readFileSync(portFile, 'utf8').split('\n')[0]);
  const targets = await (
    await fetch(`http://127.0.0.1:${port}/json/list`)
  ).json();
  ws = new WebSocket(
    targets.find((target) => target.type === 'page').webSocketDebuggerUrl,
  );
  await new Promise((resolve) =>
    ws.addEventListener('open', resolve, { once: true }),
  );
  const pending = new Map(),
    exceptions = [];
  let sequence = 0;
  call = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = ++sequence;
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params }));
    });
  ws.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (message.method === 'Runtime.exceptionThrown')
      exceptions.push(message.params.exceptionDetails);
    if (!message.id) return;
    const entry = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) entry?.reject(new Error(message.error.message));
    else entry?.resolve(message.result);
  });
  const js = async (expression) => {
    const result = await call('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    assert.ok(
      !result.exceptionDetails,
      JSON.stringify(result.exceptionDetails),
    );
    return result.result.value;
  };
  const clickNode = async (expression) => {
    const point = await js(
      `(async () => { const node = ${expression}; if (!node) throw new Error('Missing click target'); node.scrollIntoView({ block: 'center', behavior: 'instant' }); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); const bounds = node.getBoundingClientRect(); return { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 }; })()`,
    );
    await call('Input.dispatchMouseEvent', {
      type: 'mousePressed',
      button: 'left',
      clickCount: 1,
      ...point,
    });
    await call('Input.dispatchMouseEvent', {
      type: 'mouseReleased',
      button: 'left',
      clickCount: 1,
      ...point,
    });
  };
  const click = (selector) =>
    clickNode(`document.querySelector(${JSON.stringify(selector)})`);
  const input = (selector, value, event = 'input') =>
    js(
      `(() => { const node = document.querySelector(${JSON.stringify(selector)}); node.focus(); node.value = ${JSON.stringify(value)}; node.dispatchEvent(new Event(${JSON.stringify(event)}, { bubbles: true })); })()`,
    );
  const count = (selector) =>
    js(`document.querySelectorAll(${JSON.stringify(selector)}).length`);
  const property = (selector, name) =>
    js(
      `document.querySelector(${JSON.stringify(selector)})[${JSON.stringify(name)}]`,
    );
  const section = (label) =>
    clickNode(
      `Array.from(document.querySelectorAll('.agent-editor-navigation [role="tab"]')).find(button => button.querySelector('.agent-editor-tab-label')?.textContent === ${JSON.stringify(label)})`,
    );
  const buttonText = (selector, label) =>
    clickNode(
      `Array.from(document.querySelectorAll(${JSON.stringify(selector)})).find(button => button.textContent.trim() === ${JSON.stringify(label)})`,
    );
  const resize = async (width, height = 1000) => {
    await call('Emulation.setDeviceMetricsOverride', {
      width,
      height,
      deviceScaleFactor: 1,
      mobile: false,
    });
    await js(
      'new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))',
    );
  };
  let navigationSequence = 0;
  const navigate = async (name) => {
    const url = `${base}/__agent-tools/${name}?navigation=${++navigationSequence}`;
    await call('Page.navigate', { url });
    const ready = (selector) =>
      until(async () => {
        if (exceptions.length) throw new Error(JSON.stringify(exceptions));
        return (
          (await js(
            `location.href === ${JSON.stringify(url)} && document.readyState === 'complete'`,
          )) && (await count(selector)) > 0
        );
      }, selector).catch(async (error) => {
        error.message += '\n' + (await js('document.body.innerText'));
        throw error;
      });
    await ready('.agent-editor-navigation [role="tab"]:not(:disabled)');
    await click('#agent-editor-tab-tools');
    await ready('.agent-tools');
  };
  const screenshot = async (name) => {
    if (!process.env.AGENT_TOOLS_SCREENSHOT_DIR) return;
    fs.mkdirSync(process.env.AGENT_TOOLS_SCREENSHOT_DIR, { recursive: true });
    await js('window.scrollTo(0, 0)');
    const result = await call('Page.captureScreenshot', {
      format: 'png',
      captureBeyondViewport: true,
    });
    fs.writeFileSync(
      path.join(process.env.AGENT_TOOLS_SCREENSHOT_DIR, `${name}.png`),
      Buffer.from(result.data, 'base64'),
    );
  };
  const unexpectedScrollers = () =>
    js(`Array.from(document.querySelectorAll('.agent-editor *')).filter(node => {
    const style = getComputedStyle(node);
    return !node.matches('.agent-tools-catalog, .agent-tools-inspector-body') && /^(auto|scroll)$/.test(style.overflowY) && node.scrollHeight > node.clientHeight + 1 && node.clientHeight > 0;
  }).map(node => node.className)`);
  const toolLayout = () =>
    js(`Object.fromEntries(['.settings-topbar', '.agent-editor-navigation', '.agent-tools-search', '.agent-tools-navigation', '.agent-tools-inspector'].map(selector => [selector, document.querySelector(selector)?.getBoundingClientRect().toJSON()]))`);
  const assertToolScroll = async () => {
    await delay(250);
    const before = await toolLayout();
    const wheelPoint = await js(`(() => {
      const catalog = document.querySelector('.agent-tools-catalog');
      catalog.scrollTop = 0;
      const bounds = catalog.getBoundingClientRect();
      return { x: bounds.left + bounds.width / 2, y: Math.min(bounds.bottom - 10, bounds.top + 100), height: bounds.height };
    })()`);
    assert.ok(wheelPoint.height > 100, 'The tool list has usable viewport space');
    await call('Input.dispatchMouseEvent', {
      type: 'mouseWheel',
      x: wheelPoint.x,
      y: wheelPoint.y,
      deltaX: 0,
      deltaY: 450,
    });
    await until(
      () => property('.agent-tools-catalog', 'scrollTop').then(value => value > 100),
      'Scroll inside the tool list',
    );
    assert.deepEqual(await toolLayout(), before, 'Tabs, search, categories and details stay fixed while tools scroll');
    assert.equal(await property('.settings-content', 'scrollTop'), 0, 'The page content does not scroll');
    assert.equal(await js('document.scrollingElement.scrollTop'), 0, 'The document does not scroll');
    const viewport = await js(`({ height: innerHeight, pageHeight: document.documentElement.scrollHeight, catalogBottom: document.querySelector('.agent-tools-catalog').getBoundingClientRect().bottom, summaryBottom: document.querySelector('.agent-tools-summary').getBoundingClientRect().bottom })`);
    assert.ok(viewport.pageHeight <= viewport.height + 1 && viewport.catalogBottom <= viewport.height && viewport.summaryBottom <= viewport.height,
      'The tool list and summary fit the viewport: ' + JSON.stringify(viewport));
  };
  const key = async (value) => {
    await call('Input.dispatchKeyEvent', {
      type: 'keyDown',
      key: value,
      code: value,
    });
    await call('Input.dispatchKeyEvent', {
      type: 'keyUp',
      key: value,
      code: value,
    });
  };
  await call('Page.enable');
  await call('Runtime.enable');
  await resize(1440);
  await navigate('research-assistant');
  await until(
    () => count('.agent-tools-inspector').then((value) => value === 1),
    'Inline inspector',
  );
  assert.equal(await count('.agent-tools-row'), 14);
  assert.equal(await count('[role="tablist"]'), 1);
  assert.equal(await count('.agent-editor-navigation [role="tab"]'), 6);
  assert.equal(
    await count(
      '.agent-editor > .panel, .agent-editor .agent-tools-section-nav',
    ),
    0,
  );
  assert.deepEqual(
    await unexpectedScrollers(),
    [],
    'Only the tool list and long tool details can scroll',
  );
  assert.equal(await count('.settings-page-heading, .agent-editor-context'), 0, 'Agent name and description are not repeated above the tabs');
  await screenshot('desktop');
  await assertToolScroll();
  assert.equal(await property('.agent-tools-inspector', 'scrollTop'), 0);
  await input('.agent-tools-search input', 'read');
  await js('document.getElementById("agent-editor-tab-tools").focus()');
  await key('ArrowRight');
  assert.equal(await js('document.activeElement.id'), 'agent-editor-tab-mcp');
  assert.equal(await count('#agent-editor-panel-mcp'), 1);
  await key('ArrowLeft');
  assert.equal(await property('.agent-tools-search input', 'value'), 'read');
  await input('.agent-tools-search input', '');
  await js('document.querySelector(".agent-tools-filters button").focus()');
  await call('Input.dispatchKeyEvent', {
    type: 'keyDown',
    key: '/',
    code: 'Slash',
    text: '/',
  });
  await call('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key: '/',
    code: 'Slash',
  });
  assert.equal(
    await js('document.activeElement.matches(".agent-tools-search input")'),
    true,
    'Tool search owns its shortcut while this tab is active',
  );
  await input('.agent-tools-search input', 'reliable sources');
  assert.equal(
    await count('.agent-tools-row'),
    1,
    'Search uses backend descriptions',
  );
  assert.equal(
    await js('document.activeElement.matches(".agent-tools-search input")'),
    true,
  );
  await input('.agent-tools-search input', 'grep');
  await click('input[aria-label="Select all files tools"]');
  await input('.agent-tools-search input', '');
  assert.equal(
    await count('.agent-tools-row-check input:checked'),
    8,
    'Bulk selection includes hidden tools',
  );
  await click('input[aria-label="Enable write"]');
  assert.equal(
    await property(
      'input[aria-label="Select all files tools"]',
      'indeterminate',
    ),
    true,
  );
  await buttonText('.agent-tools-filters button', 'Selected 7');
  assert.equal(await count('.agent-tools-row'), 7);
  await buttonText('.agent-tools-filters button', 'All tools');
  await input('.agent-tools-search input', 'no-matching-tool');
  assert.equal(await count('.agent-tools-empty'), 1);
  await buttonText('.agent-tools-empty button', 'Clear filters');
  await click('button[aria-label="Configure web_search"]');
  await buttonText(
    '.agent-tool-config button',
    'Advanced credential overrides',
  );
  assert.equal(
    await js(
      'document.querySelector("#agent-tool-web_search-include_raw").checked',
    ),
    true,
    'Inherited boolean uses the effective value',
  );
  await click('#agent-tool-web_search-include_raw');
  await click('button[aria-label="Reset include raw override"]');
  assert.equal(
    await js(
      'document.querySelector("#agent-tool-web_search-include_raw").checked',
    ),
    true,
  );
  await input('#agent-tool-web_search-provider', 'duckduckgo', 'change');
  assert.equal(await count('#agent-tool-web_search-tavily_connection'), 0);
  await click('button[aria-label="Reset provider override"]');
  assert.equal(await count('#agent-tool-web_search-tavily_connection'), 1);
  await input('#agent-tool-web_search-tavily_api_key', 'demo-only-value');
  assert.equal(
    await js('document.activeElement.id'),
    'agent-tool-web_search-tavily_api_key',
  );
  await click('button[aria-label="Reset tavily api key override"]');
  await input('#agent-tool-web_search-result_limit', '7');
  assert.deepEqual(
    await unexpectedScrollers(),
    [],
    'Expanded configuration stays within its details pane',
  );
  await click('input[aria-label="Enable generate_image"]');
  await click('button[aria-label="Configure generate_image"]');
  assert.equal(await count('#agent-tool-generate_image-provider'), 0);
  await click('.agent-tool-details .model-picker-input');
  await until(
    () => count('.model-picker-option-main').then((value) => value > 0),
    'Image models',
  );
  assert.equal(
    await js(
      'document.querySelector(".model-picker-dropdown").textContent.includes("text-model")',
    ),
    false,
  );
  await click('.model-picker-option-main');
  await input('#agent-tool-generate_image-size', '1536x1024', 'change');
  await click('button[aria-label="Reset image model override"]');
  await click('input[aria-label="Enable generate_image"]');
  await click('button[aria-label="Configure web_search"]');
  await section('General');
  await input(
    'input[placeholder="Short description shown in the agent picker"]',
    'Find reliable sources and summarize the results.',
  );
  await section('System prompt');
  await input(
    '.prompt-variable-textarea textarea',
    'You are a concise research assistant.',
  );
  await section('Tools');
  assert.equal(
    await property('#agent-tool-web_search-result_limit', 'value'),
    '7',
  );
  console.log(
    'PASS: real editor search, full-category selection, tri-state, filters, schema conditions, inherited booleans and configuration resets.',
  );

  await section('MCP');
  await click('input[aria-label="Enable github MCP server"]');
  await until(
    () =>
      count('input[aria-label="Enable mcp__github__search"]').then(
        (value) => value === 1,
      ),
    'MCP refresh',
  );
  await click('input[aria-label="Enable mcp__github__search"]');
  await section('Tools');
  assert.equal(
    await count('.agent-tools-row-check input:checked'),
    7,
    'MCP refresh preserves unsaved tools',
  );
  assert.equal(
    await js(
      'document.querySelector("#agent-tool-web_search-result_limit").value',
    ),
    '7',
    'MCP refresh preserves unsaved config',
  );
  await section('MCP');
  failMcp = true;
  await click('input[aria-label="Enable github MCP server"]');
  await until(
    () =>
      js(
        'document.querySelector(".toast.error")?.textContent.includes("Connection unavailable")',
      ),
    'MCP failure',
  );
  failMcp = false;
  assert.equal(
    await property('input[aria-label="Enable github MCP server"]', 'checked'),
    true,
    'Failed toggle keeps server state',
  );
  await click('input[aria-label="Enable empty-server MCP server"]');
  await until(
    () =>
      js(
        'document.querySelector(".agent-tools-main").textContent.includes("No tools loaded")',
      ),
    'Empty MCP state',
  );
  await section('Delegation');
  await click('input[aria-label="Select reviewer as sub-agent"]');
  await section('Permissions');
  await click('input[aria-label="Override global permissions"]');
  await buttonText('.agent-tools-permissions button', 'Add permission rule');
  await input('.agent-tools-permission-rule input', 'shell.execute');
  assert.equal(
    await js(
      'document.activeElement === document.querySelector(".agent-tools-permission-rule input")',
    ),
    true,
    'Rule editing keeps focus',
  );
  await call('Input.insertText', { text: '.extra' });
  assert.equal(
    await js(
      'document.querySelector(".agent-tools-permission-rule input").value',
    ),
    'shell.execute.extra',
  );
  await input('.agent-tools-rule-resource input', '/workspace/*');
  await input('.agent-tools-permission-rule select', 'deny', 'change');
  await buttonText('.settings-topbar-actions button', 'Save');
  await until(
    () => js('location.pathname === "/settings/agents"'),
    'Agent save',
  );
  const saved = writes.find(
    (write) => write.path === '/agents/cards/research-assistant',
  ).body;
  assert.ok(saved.tools.includes('mcp__github__search'));
  assert.ok(saved.tools.includes('grep') && !saved.tools.includes('write'));
  assert.equal(saved.tool_configs.web_search.result_limit, '7');
  assert.equal(
    saved.description,
    'Find reliable sources and summarize the results.',
  );
  assert.equal(saved.system_prompt, 'You are a concise research assistant.');
  assert.ok(!('tavily_api_key' in saved.tool_configs.web_search));
  assert.deepEqual(saved.tool_permissions, [
    { action: 'shell.execute.extra', resource: '/workspace/*', effect: 'deny' },
  ]);
  assert.ok(saved.sub_agents.includes('reviewer'));
  console.log(
    'PASS: MCP success/failure/empty states, draft preservation, stable permission editing, delegation and exact save payload.',
  );

  await navigate('builtin');
  assert.equal(await count('.agent-tools-row-check input:not(:disabled)'), 0);
  await click('button[aria-label="Configure web_search"]');
  assert.equal(
    await count(
      '.agent-tool-details input:not(:disabled), .agent-tool-details select:not(:disabled)',
    ),
    0,
  );
  await section('MCP');
  assert.equal(await count('.agent-tools-main input:not(:disabled)'), 0);
  await section('Permissions');
  assert.equal(
    await count(
      '.agent-tools-main input:enabled, .agent-tools-main select:enabled',
    ),
    0,
  );
  await navigate('acp');
  assert.equal(await count('input[aria-label="Enable read"]'), 0);
  assert.equal(
    await js(
      'document.querySelector(".agent-editor-navigation").textContent.includes("Delegation")',
    ),
    false,
  );
  await section('Permissions');
  assert.ok(
    await js(
      'document.querySelector(".permission-mode-picker").textContent.includes("ACP")',
    ),
  );
  await section('General');
  assert.equal(await count('button[aria-label="Antigravity runtime"]'), 0);
  assert.equal(await count('#acp-agent-name:not(:disabled)'), 1);
  assert.equal(await count('button[aria-label="Base agent card"]'), 0);
  assert.equal(await property('#acp-default-tools', 'checked'), true);
  await input('#acp-agent-name', 'reviewer');
  await section('Tools');
  assert.ok(await count('.agent-tools-row-check input:not(:disabled)'));
  await section('MCP');
  assert.ok(await count('.agent-tools-main input:not(:disabled)'));
  await section('Permissions');
  assert.ok(await count('.agent-tools-main input:enabled, .agent-tools-main select:enabled'));
  await section('System prompt');
  assert.ok(await count('textarea:not(:disabled):not([readonly])'));
  await input('textarea:not(:disabled):not([readonly])', 'Direct ACP card instructions.');
  await section('General');
  await until(() => count('button[aria-label="ACP model"]').then(value => value === 1), 'ACP model choices');
  assert.equal(await count('button[aria-label="Thinking level"]'), 1);
  await click('button[aria-label="ACP model"]');
  await click('[role="option"][data-value="fake-lite"]');
  await click('button[aria-label="Thinking level"]');
  await click('[role="option"][data-value="low"]');
  await buttonText('.settings-topbar-actions button', 'Save');
  await until(() => js('location.pathname === "/settings/agents"'), 'ACP runtime save');
  const acpWrite = writes.find(write => write.path === '/agents/cards/acp');
  assert.deepEqual(acpWrite.body.acp, {
    agent: 'antigravity-cli', agent_name: 'reviewer', use_default_tools: true,
    config_options: { model: 'fake-lite', thinking_level: 'low' },
  });
  assert.equal(acpWrite.body.system_prompt, 'Direct ACP card instructions.');
  await navigate('acp');
  await section('System prompt');
  assert.equal(await property('textarea', 'value'), 'Direct ACP card instructions.');
  await section('General');
  assert.equal(await property('#acp-default-tools', 'checked'), true);
  await click('#acp-default-tools');
  assert.equal(await property('#acp-agent-name', 'disabled'), true);
  assert.equal(await property('#acp-agent-name', 'value'), '');
  await buttonText('.settings-topbar-actions button', 'Save');
  await until(() => js('location.pathname === "/settings/agents"'), 'ACP tools-only save');
  const toolsOnlyWrite = writes.filter(write => write.path === '/agents/cards/acp').at(-1);
  assert.equal(toolsOnlyWrite.body.acp.use_default_tools, false);
  assert.equal(toolsOnlyWrite.body.acp.agent_name, null);
  assert.equal(toolsOnlyWrite.body.system_prompt, 'Direct ACP card instructions.');
  await navigate('acp');
  await section('General');
  assert.equal(await property('#acp-default-tools', 'checked'), false);
  await click('#acp-default-tools');
  assert.equal(await property('#acp-agent-name', 'disabled'), false);
  await click('#acp-default-tools');
  console.log('PASS: ACP cards save their prompt, K41 tools, AGY profile, default-tools toggle and session options.');
  await navigate('new');
  await section('MCP');
  assert.equal(await count('.agent-tools-main input:not(:disabled)'), 0);
  await navigate('empty');
  await buttonText('.agent-tools-filters button', 'Selected 0');
  assert.equal(await count('.agent-tools-empty'), 1);
  console.log(
    'PASS: read-only tools/MCP/permissions, ACP restrictions, unsaved-agent binding guard and empty selection.',
  );

  for (const width of [1440, 1024, 390]) {
    await resize(width);
    await navigate('research-assistant');
    for (const name of ['Tools', 'MCP', 'Delegation', 'Permissions']) {
      await section(name);
      assert.deepEqual(
        await unexpectedScrollers(),
        [],
        `${width}/${name}: no unexpected page scrollers`,
      );
      const overflow = await js(
        `({ page: document.documentElement.scrollWidth, width: innerWidth, controls: [...document.querySelectorAll('.agent-tools button, .agent-tools input, .agent-tools select')].filter(node => { if (node.closest('.agent-tools-category-nav')) return false; const rect = node.getBoundingClientRect(); return rect.width && (rect.left < -1 || rect.right > innerWidth + 1); }).map(node => node.outerHTML.slice(0, 160)) })`,
      );
      assert.ok(
        overflow.page <= overflow.width && !overflow.controls.length,
        `${width}/${name}: ${JSON.stringify(overflow)}`,
      );
    }
    await section('Tools');
    await assertToolScroll();
    assert.ok(
      await js(
        'parseFloat(getComputedStyle(document.querySelector(".agent-tools-search input")).paddingLeft) >= 32',
      ),
      'Search text clears its leading icon',
    );
    await screenshot(`tools-${width}`);
    if (!(await count('.agent-tools-inspector'))) {
      await click('button[aria-label="Configure web_search"]');
      await until(
        () => count('.agent-tools-details-dialog').then((value) => value === 1),
        'Responsive drawer',
      );
      await delay(80);
      await until(
        () =>
          js(
            '(() => { const bounds = document.querySelector(".agent-tools-details-dialog").getBoundingClientRect(); return Math.abs(bounds.top) < 1 && Math.abs(bounds.height - innerHeight) < 1; })()',
          ),
        'Full-height tool drawer',
      );
      assert.equal(
        await js(
          'document.querySelector(".agent-tools-details-dialog").contains(document.activeElement)',
        ),
        true,
      );
      assert.equal(
        await js('document.documentElement.style.overflow'),
        'hidden',
      );
      await screenshot(`drawer-${width}`);
      await call('Input.dispatchKeyEvent', {
        type: 'keyDown',
        key: 'Escape',
        code: 'Escape',
        windowsVirtualKeyCode: 27,
      });
      await call('Input.dispatchKeyEvent', {
        type: 'keyUp',
        key: 'Escape',
        code: 'Escape',
        windowsVirtualKeyCode: 27,
      });
      await until(
        () => count('.agent-tools-details-dialog').then((value) => value === 0),
        'Close drawer',
      );
      assert.equal(
        await js('document.activeElement.getAttribute("aria-label")'),
        'Configure web_search',
      );
      assert.equal(await js('document.documentElement.style.overflow'), '');
    }
  }
  for (const [width, height] of [[1440, 720], [1024, 768], [800, 600], [390, 844], [390, 667]]) {
    await resize(width, height);
    await navigate('research-assistant');
    await assertToolScroll();
    await screenshot(`tools-${width}-${height}`);
  }
  await resize(1440);
  await js('document.documentElement.classList.add("dark")');
  await screenshot('desktop-dark');
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log(
    'PASS: 1440/1024/390 px, all capability sections without overflow, drawer keyboard/focus/scroll restoration, dark mode and no browser exceptions.',
  );
} finally {
  if (call && ws?.readyState === WebSocket.OPEN)
    await Promise.race([call('Browser.close'), delay(2000)]).catch(() => {});
  ws?.close();
  browser.kill();
  await server.close();
  const resolvedProfile = path.resolve(profile);
  assert.equal(path.dirname(resolvedProfile), path.resolve(os.tmpdir()));
  assert.ok(path.basename(resolvedProfile).startsWith('k41-agent-tools-'));
  fs.rmSync(resolvedProfile, {
    recursive: true,
    force: true,
    maxRetries: 20,
    retryDelay: 100,
  });
}
