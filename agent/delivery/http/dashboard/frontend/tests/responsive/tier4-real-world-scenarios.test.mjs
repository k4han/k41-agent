// @ts-check
import {
  assert,
  assertEqual,
  assertLessThanOrEqual,
} from './engine/harness.mjs';
import { ViewportSimulator } from './engine/viewport-simulator.mjs';

/**
 * Register Tier 4: Real-World Scenarios tests
 * @param {import('./engine/harness.mjs').TestHarness} harness
 * @param {{ css: import('./engine/css-analyzer.mjs').CSSAnalyzer, ast: import('./engine/ast-analyzer.mjs').ASTAnalyzer }} ctx
 */
export function registerTier4Tests(harness, ctx) {
  harness.setTier('Tier 4: Real-World Scenarios');

  const testRoutes = [
    {
      path: '/',
      name: 'Home Dashboard',
      buildTree: (vp) => ({
        name: 'route-home',
        style: { width: vp.width },
        children: [
          {
            name: 'app-topbar',
            style: { widthStr: '100%', padding: { top: 8, right: 12, bottom: 8, left: 12 } },
          },
          {
            name: 'home-content',
            style: { widthStr: '100%', padding: { top: 12, right: 12, bottom: 16, left: 12 } },
            children: [
              {
                name: 'metrics-grid',
                style: {
                  display: 'flex',
                  flexDirection: vp.width <= 640 ? 'column' : 'row',
                  flexWrap: 'wrap',
                  widthStr: '100%',
                  gap: 12,
                },
                children: [
                  { name: 'metric-card-1', style: { widthStr: vp.width <= 640 ? '100%' : '220px' } },
                  { name: 'metric-card-2', style: { widthStr: vp.width <= 640 ? '100%' : '220px' } },
                  { name: 'metric-card-3', style: { widthStr: vp.width <= 640 ? '100%' : '220px' } },
                ],
              },
              {
                name: 'onboarding-checklist',
                style: { widthStr: '100%', padding: { top: 12, right: 12, bottom: 12, left: 12 } },
                children: [
                  {
                    name: 'onboarding-item',
                    style: {
                      display: 'flex',
                      flexDirection: vp.width <= 640 ? 'column' : 'row',
                      flexWrap: 'wrap',
                      gap: 10,
                      widthStr: '100%',
                    },
                    children: [
                      { name: 'marker', style: { width: 22 } },
                      { name: 'text', style: { widthStr: vp.width <= 640 ? '100%' : 'calc(100% - 160px)' } },
                      { name: 'action-btn', style: { widthStr: vp.width <= 640 ? '100%' : '130px', minWidth: 40 } },
                    ],
                  },
                ],
              },
              {
                name: 'services-panel',
                style: { widthStr: '100%' },
              },
            ],
          },
        ],
      }),
    },
    {
      path: '/chat',
      name: 'Chat View & Stream',
      buildTree: (vp) => ({
        name: 'route-chat',
        style: { width: vp.width },
        children: [
          {
            name: 'chat-header',
            style: {
              display: 'flex',
              flexDirection: 'row',
              widthStr: '100%',
              padding: { top: 8, right: 12, bottom: 8, left: 12 },
              gap: 8,
            },
            children: [
              { name: 'menu-btn', style: { width: 40 } },
              { name: 'title', style: { width: Math.min(180, vp.width - 120), overflowX: 'hidden' } },
              { name: 'workspace-trigger', style: { width: 40 } },
            ],
          },
          {
            name: 'chat-transcript',
            style: { widthStr: '100%', isScrollContainer: true, padding: { top: 8, right: 12, bottom: 8, left: 12 } },
            children: [
              {
                name: 'user-bubble',
                style: { widthStr: '100%', maxWidth: vp.width - 48, wordBreak: 'break-word', overflowWrap: 'anywhere' },
                text: 'How do I optimize responsive layout for SolidJS dashboard?',
              },
              {
                name: 'assistant-bubble',
                style: { widthStr: '100%', maxWidth: vp.width - 48, wordBreak: 'break-word', overflowWrap: 'anywhere' },
                children: [
                  {
                    name: 'markdown-table-wrap',
                    style: { widthStr: '100%', isScrollContainer: true },
                    children: [{ name: 'table', style: { width: 680 } }],
                  },
                  {
                    name: 'markdown-code-frame',
                    style: { widthStr: '100%', isScrollContainer: true },
                    children: [{ name: 'pre-code', style: { width: 850 } }],
                  },
                ],
              },
            ],
          },
          {
            name: 'chat-composer',
            style: { widthStr: '100%', padding: { top: 4, right: 12, bottom: 12, left: 12 } },
            children: [
              { name: 'input-textarea', style: { widthStr: '100%' } },
              {
                name: 'toolbar-2tier',
                style: {
                  display: 'flex',
                  flexDirection: vp.width <= 640 ? 'column' : 'row',
                  widthStr: '100%',
                  gap: 8,
                },
                children: [
                  {
                    name: 'pickers-row',
                    style: {
                      display: 'flex',
                      flexDirection: 'row',
                      widthStr: vp.width <= 640 ? '100%' : 'calc(100% - 100px)',
                      gap: 8,
                    },
                    children: [
                      { name: 'agent-picker', style: { widthStr: '50%' } },
                      { name: 'model-picker', style: { widthStr: '50%' } },
                    ],
                  },
                  {
                    name: 'actions-row',
                    style: {
                      display: 'flex',
                      flexDirection: 'row',
                      widthStr: vp.width <= 640 ? '100%' : '90px',
                      gap: 6,
                    },
                    children: [
                      { name: 'attach-btn', style: { width: 40 } },
                      { name: 'send-btn', style: { width: 44 } },
                    ],
                  },
                ],
              },
            ],
          },
        ],
      }),
    },
    {
      path: '/repositories',
      name: 'Repositories Management',
      buildTree: (vp) => ({
        name: 'route-repositories',
        style: { width: vp.width },
        children: [
          { name: 'topbar', style: { widthStr: '100%', padding: { top: 8, right: 12, bottom: 8, left: 12 } } },
          {
            name: 'repo-list',
            style: { widthStr: '100%', padding: { top: 12, right: 12, bottom: 16, left: 12 } },
            children: [
              {
                name: 'repo-item',
                style: {
                  display: 'flex',
                  flexDirection: vp.width <= 640 ? 'column' : 'row',
                  gap: 12,
                  widthStr: '100%',
                  padding: { top: 12, right: 12, bottom: 12, left: 12 },
                },
                children: [
                  { name: 'repo-info', style: { widthStr: vp.width <= 640 ? '100%' : 'calc(100% - 200px)' } },
                  {
                    name: 'repo-actions',
                    style: {
                      display: 'flex',
                      flexDirection: 'row',
                      gap: 8,
                      widthStr: vp.width <= 640 ? '100%' : '180px',
                    },
                    children: [
                      { name: 'run-btn', style: { width: 80, minWidth: 40 } },
                      { name: 'edit-btn', style: { width: 80, minWidth: 40 } },
                    ],
                  },
                ],
              },
            ],
          },
        ],
      }),
    },
    {
      path: '/tasks',
      name: 'Task Execution History',
      buildTree: (vp) => ({
        name: 'route-tasks',
        style: { width: vp.width },
        children: [
          { name: 'topbar', style: { widthStr: '100%' } },
          {
            name: 'task-history-panel',
            style: { widthStr: '100%', padding: { top: 12, right: 12, bottom: 16, left: 12 } },
            children: [
              {
                name: 'task-history-header',
                style: {
                  display: 'flex',
                  flexDirection: vp.width <= 640 ? 'column' : 'row',
                  flexWrap: 'wrap',
                  gap: 8,
                  widthStr: '100%',
                },
                children: [
                  { name: 'title', style: { width: Math.min(180, vp.width - 24) } },
                  { name: 'workspace-filter', style: { widthStr: vp.width <= 640 ? '100%' : '280px' } },
                ],
              },
              {
                name: 'task-item',
                style: { widthStr: '100%', padding: { top: 12, right: 12, bottom: 12, left: 12 } },
                children: [
                  {
                    name: 'task-result-pre-wrap',
                    style: { widthStr: '100%', isScrollContainer: true },
                    children: [{ name: 'pre-output', style: { width: 750 } }],
                  },
                ],
              },
            ],
          },
        ],
      }),
    },
    {
      path: '/scheduler',
      name: 'Scheduled Jobs Dashboard',
      buildTree: (vp) => ({
        name: 'route-scheduler',
        style: { width: vp.width },
        children: [
          { name: 'topbar', style: { widthStr: '100%' } },
          {
            name: 'scheduler-panel',
            style: { widthStr: '100%', padding: { top: 12, right: 12, bottom: 16, left: 12 } },
            children: [
              {
                name: 'table-wrap',
                style: { widthStr: '100%', isScrollContainer: true },
                children: [
                  {
                    name: 'scheduler-jobs-table',
                    style: { width: 850, minWidth: 720 },
                  },
                ],
              },
            ],
          },
        ],
      }),
    },
    {
      path: '/settings/config',
      name: 'Settings: System Configuration',
      buildTree: (vp) => ({
        name: 'route-settings-config',
        style: { width: vp.width },
        children: [
          {
            name: 'settings-breadcrumbs',
            style: { widthStr: '100%', padding: { top: 8, right: 12, bottom: 8, left: 12 }, overflowX: 'hidden' },
          },
          {
            name: 'settings-form-panel',
            style: { widthStr: '100%', padding: { top: 12, right: 12, bottom: 16, left: 12 } },
            children: [
              {
                name: 'form-group',
                style: { display: 'flex', flexDirection: 'column', widthStr: '100%', gap: 12 },
                children: [
                  { name: 'field-1-input', style: { widthStr: '100%' } },
                  { name: 'field-2-select', style: { widthStr: '100%' } },
                  { name: 'field-3-textarea', style: { widthStr: '100%' } },
                ],
              },
            ],
          },
        ],
      }),
    },
    {
      path: '/settings/agents',
      name: 'Settings: Agent Prompt & Variables',
      buildTree: (vp) => ({
        name: 'route-settings-agents',
        style: { width: vp.width },
        children: [
          { name: 'breadcrumbs', style: { widthStr: '100%', overflowX: 'hidden' } },
          {
            name: 'agent-prompt-container',
            style: { widthStr: '100%', padding: { top: 12, right: 12, bottom: 16, left: 12 } },
            children: [
              {
                name: 'agent-prompt-grid',
                style: {
                  display: 'flex',
                  flexDirection: vp.width <= 980 ? 'column' : 'row',
                  gap: 16,
                  widthStr: '100%',
                },
                children: [
                  { name: 'prompt-editor-column', style: { widthStr: '100%' } },
                  { name: 'variables-sidebar-column', style: { widthStr: vp.width <= 980 ? '100%' : '280px' } },
                ],
              },
            ],
          },
        ],
      }),
    },
    {
      path: '/settings/sandboxes',
      name: 'Settings: Sandboxes Management',
      buildTree: (vp) => ({
        name: 'route-settings-sandboxes',
        style: { width: vp.width },
        children: [
          { name: 'breadcrumbs', style: { widthStr: '100%', overflowX: 'hidden' } },
          {
            name: 'sandboxes-container',
            style: { widthStr: '100%', padding: { top: 12, right: 12, bottom: 16, left: 12 } },
            children: [
              {
                name: 'sandboxes-toolbar',
                style: {
                  display: 'flex',
                  flexDirection: 'row',
                  flexWrap: 'wrap',
                  gap: 12,
                  widthStr: '100%',
                },
                children: [
                  {
                    name: 'sandboxes-filter-group',
                    style: { display: 'flex', overflowX: 'auto', isScrollContainer: true, maxWidth: '100%' },
                  },
                  {
                    name: 'sandboxes-search',
                    style: { widthStr: vp.width <= 640 ? '100%' : '240px' },
                  },
                ],
              },
              {
                name: 'sandboxes-table-wrap',
                style: { widthStr: '100%', isScrollContainer: true },
                children: [
                  {
                    name: 'sandboxes-table',
                    style: { width: 850, minWidth: 680 },
                  },
                ],
              },
            ],
          },
        ],
      }),
    },
    {
      path: '/settings/usage',
      name: 'Settings: Token Usage & Analytics',
      buildTree: (vp) => ({
        name: 'route-settings-usage',
        style: { width: vp.width },
        children: [
          { name: 'breadcrumbs', style: { widthStr: '100%', overflowX: 'hidden' } },
          {
            name: 'usage-container',
            style: { widthStr: '100%', padding: { top: 12, right: 12, bottom: 16, left: 12 } },
            children: [
              {
                name: 'usage-metrics-grid',
                style: {
                  display: 'flex',
                  flexDirection: vp.width <= 640 ? 'column' : 'row',
                  flexWrap: 'wrap',
                  gap: 12,
                  widthStr: '100%',
                },
                children: [
                  { name: 'metric-card-1', style: { widthStr: vp.width <= 640 ? '100%' : '220px' } },
                  { name: 'metric-card-2', style: { widthStr: vp.width <= 640 ? '100%' : '220px' } },
                ],
              },
              {
                name: 'usage-table-wrap',
                style: { widthStr: '100%', isScrollContainer: true },
                children: [
                  {
                    name: 'usage-table',
                    style: { width: 850, minWidth: 720 },
                  },
                ],
              },
            ],
          },
        ],
      }),
    },
  ];

  const viewports = [
    { name: 'iPhone SE (375px)', width: 375, height: 667 },
    { name: 'iPhone 14 (390px)', width: 390, height: 844 },
    { name: 'iPhone Plus (414px)', width: 414, height: 896 },
    { name: 'iPad Portrait (768px)', width: 768, height: 1024 },
  ];

  for (const route of testRoutes) {
    harness.setFeature(`Route ${route.path} (${route.name})`);

    for (const vp of viewports) {
      harness.test(`T4-${route.path.replace(/[/]/g, '_') || 'root'}-${vp.width}: Zero document-level horizontal overflow on ${vp.name}`, () => {
        const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
        const tree = route.buildTree(vp);
        const result = sim.simulatePage(tree);

        assert(
          result.passes,
          `Route ${route.path} on ${vp.name} failed: document.scrollWidth (${result.documentScrollWidth}px) > window.innerWidth (${vp.width}px). Overflowing nodes: ${result.overflowingNodes.join(', ')}`
        );
        assertEqual(
          result.documentScrollWidth,
          vp.width,
          `Route ${route.path} document.scrollWidth must equal ${vp.width}px`
        );
      });
    }
  }
}
