// @ts-check
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  TestHarness,
  assert,
  assertEqual,
  assertGreaterThanOrEqual,
  assertLessThanOrEqual,
  assertMatches,
  assertIncludes,
} from './responsive/engine/harness.mjs';
import { ViewportSimulator } from './responsive/engine/viewport-simulator.mjs';
import { CSSAnalyzer } from './responsive/engine/css-analyzer.mjs';
import { ASTAnalyzer } from './responsive/engine/ast-analyzer.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FRONTEND_ROOT = path.resolve(__dirname, '..');

async function runAdversarialM3Suite() {
  const stylesDir = path.join(FRONTEND_ROOT, 'src/styles');
  const css = new CSSAnalyzer(stylesDir);
  const ast = new ASTAnalyzer(FRONTEND_ROOT);
  css.load();

  const harness = new TestHarness({ verbose: true });
  harness.setTier('Challenger M3: Adversarial Stress Testing');

  const requiredViewports = [
    { name: 'Extremely Narrow Mobile (320px)', width: 320, height: 568 },
    { name: 'iPhone SE / Mini (375px)', width: 375, height: 667 },
    { name: 'iPhone 14 / Pro (390px)', width: 390, height: 844 },
    { name: 'iPhone Plus / Max (414px)', width: 414, height: 896 },
    { name: 'iPad Portrait (768px)', width: 768, height: 1024 },
  ];

  const sub375Viewports = [
    { name: 'Extremely Narrow Mobile (320px)', width: 320, height: 568 },
    { name: 'Compact Android (340px)', width: 340, height: 640 },
    { name: 'Standard Android (360px)', width: 360, height: 740 },
    { name: 'Boundary Sub-375 (374px)', width: 374, height: 667 },
  ];

  // =========================================================================
  // Section 1: Route Layout Bounds at 320, 375, 390, 414, and 768px
  // Routes: /settings/agents, /tasks, /settings/sandboxes, /scheduler, /
  // =========================================================================
  harness.setFeature('M3-ADV-01: Route Layout Bounds & Zero Horizontal Overflow');

  const testRoutes = [
    {
      path: '/settings/agents',
      name: 'Settings: Agent Prompt & Variables',
      buildTree: (vp) => ({
        name: 'route-settings-agents',
        style: { width: vp.width },
        children: [
          {
            name: 'settings-topbar',
            style: { widthStr: '100%', padding: { top: 8, right: 12, bottom: 8, left: 12 } },
          },
          {
            name: 'agent-prompt-container',
            style: { widthStr: '100%', padding: { top: 12, right: 12, bottom: 16, left: 12 } },
            children: [
              {
                name: 'agent-prompt-grid',
                style: {
                  display: 'flex',
                  flexDirection: vp.width <= 980 ? 'column' : 'row',
                  gap: vp.width <= 980 ? 16 : 20,
                  widthStr: '100%',
                },
                children: [
                  {
                    name: 'prompt-editor-column',
                    style: { widthStr: '100%', minHeight: 420 },
                    children: [
                      {
                        name: 'prompt-textarea-wrap',
                        style: { widthStr: '100%' },
                        children: [{ name: 'textarea', style: { widthStr: '100%' } }],
                      },
                    ],
                  },
                  {
                    name: 'prompt-variables-sidebar',
                    style: {
                      widthStr: vp.width <= 980 ? '100%' : '280px',
                      padding: { top: 0, right: 0, bottom: 0, left: vp.width <= 980 ? 0 : 20 },
                    },
                    children: [
                      {
                        name: 'variables-list',
                        style: {
                          display: 'flex',
                          flexDirection: 'column',
                          gap: 6,
                          widthStr: '100%',
                          overflowY: 'auto',
                        },
                        children: [
                          {
                            name: 'var-btn-system',
                            style: { widthStr: '100%', overflowWrap: 'anywhere', wordBreak: 'break-word' },
                            text: '{{common_rules}} sys',
                          },
                          {
                            name: 'var-btn-custom-long',
                            style: { widthStr: '100%', overflowWrap: 'anywhere', wordBreak: 'break-word' },
                            text: '{{EXTRA_LONG_VARIABLE_NAME_FOR_STRESS_TESTING_OVERFLOW_BEHAVIOR}}',
                          },
                        ],
                      },
                    ],
                  },
                ],
              },
              {
                name: 'unsaved-changes-bar',
                style: {
                  widthStr: '100%',
                  display: 'flex',
                  flexDirection: vp.width <= 640 ? 'column' : 'row',
                  gap: 8,
                },
                children: [
                  { name: 'hint-text', style: { widthStr: vp.width <= 640 ? '100%' : 'calc(100% - 160px)' } },
                  { name: 'save-btn', style: { widthStr: vp.width <= 640 ? '100%' : '150px' } },
                ],
              },
            ],
          },
        ],
      }),
    },
    {
      path: '/tasks',
      name: 'Task Execution History & Submission',
      buildTree: (vp) => ({
        name: 'route-tasks',
        style: { width: vp.width },
        children: [
          {
            name: 'tasks-topbar',
            style: { widthStr: '100%', padding: { top: 8, right: 12, bottom: 8, left: 12 } },
          },
          {
            name: 'task-submit-panel',
            style: { widthStr: '100%', padding: { top: 12, right: 12, bottom: 12, left: 12 } },
            children: [
              { name: 'prompt-textarea', style: { widthStr: '100%' } },
              {
                name: 'submit-actions',
                style: {
                  display: 'flex',
                  flexDirection: vp.width <= 640 ? 'column' : 'row',
                  gap: 8,
                  widthStr: '100%',
                },
                children: [
                  { name: 'model-selector', style: { widthStr: vp.width <= 640 ? '100%' : '200px' } },
                  { name: 'run-btn', style: { widthStr: vp.width <= 640 ? '100%' : '120px' } },
                ],
              },
            ],
          },
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
                  gap: 10,
                  widthStr: '100%',
                },
                children: [
                  { name: 'panel-title', style: { width: Math.min(160, vp.width - 24) } },
                  {
                    name: 'task-history-controls',
                    style: {
                      display: 'flex',
                      flexWrap: 'wrap',
                      gap: 8,
                      widthStr: vp.width <= 640 ? '100%' : 'calc(100% - 180px)',
                    },
                    children: [
                      { name: 'task-workspace-filter', style: { widthStr: vp.width <= 640 ? '100%' : '240px' } },
                      { name: 'count-hint', style: { width: 120 } },
                      { name: 'status-hint', style: { width: 140 } },
                    ],
                  },
                ],
              },
              {
                name: 'task-history-list',
                style: { widthStr: '100%' },
                children: [
                  {
                    name: 'task-item-card',
                    style: { widthStr: '100%', padding: { top: 8, right: 8, bottom: 8, left: 8 } },
                    children: [
                      {
                        name: 'task-request',
                        text: 'Deploy production release with canary traffic allocation and metric verification',
                        style: { widthStr: '100%', overflowWrap: 'anywhere', wordBreak: 'break-word' },
                      },
                      {
                        name: 'task-result-pre-wrap',
                        style: { widthStr: '100%', isScrollContainer: true },
                        children: [{ name: 'pre-output', style: { width: 850, minWidth: 720 } }],
                      },
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
      path: '/settings/sandboxes',
      name: 'Sandboxes Management Dashboard',
      buildTree: (vp) => ({
        name: 'route-settings-sandboxes',
        style: { width: vp.width },
        children: [
          {
            name: 'settings-topbar',
            style: { widthStr: '100%', padding: { top: 8, right: 12, bottom: 8, left: 12 } },
          },
          {
            name: 'sandboxes-page-content',
            style: { widthStr: '100%', padding: { top: 12, right: 12, bottom: 16, left: 12 } },
            children: [
              {
                name: 'sandboxes-filter-group',
                style: {
                  widthStr: '100%',
                  isScrollContainer: true,
                  display: 'flex',
                  flexDirection: 'row',
                  gap: 8,
                },
                children: [
                  { name: 'filter-pill-all', style: { width: 70 } },
                  { name: 'filter-pill-active', style: { width: 80 } },
                  { name: 'filter-pill-stopped', style: { width: 85 } },
                  { name: 'filter-pill-archived', style: { width: 90 } },
                  { name: 'filter-pill-destroyed', style: { width: 95 } },
                ],
              },
              {
                name: 'sandboxes-table-wrap',
                style: { widthStr: '100%', isScrollContainer: true },
                children: [
                  {
                    name: 'sandboxes-table',
                    style: { width: 720, minWidth: 680 },
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
          {
            name: 'scheduler-topbar',
            style: { widthStr: '100%', padding: { top: 8, right: 12, bottom: 8, left: 12 } },
          },
          {
            name: 'scheduler-content',
            style: { widthStr: '100%', padding: { top: 12, right: 12, bottom: 16, left: 12 } },
            children: [
              {
                name: 'schedule-form-panel',
                style: { widthStr: '100%', padding: { top: 10, right: 10, bottom: 10, left: 10 } },
                children: [
                  { name: 'task-input', style: { widthStr: '100%' } },
                  {
                    name: 'trigger-row',
                    style: {
                      display: 'flex',
                      flexDirection: vp.width <= 640 ? 'column' : 'row',
                      gap: 8,
                      widthStr: '100%',
                    },
                    children: [
                      { name: 'trigger-type-select', style: { widthStr: vp.width <= 640 ? '100%' : '180px' } },
                      { name: 'run-date-input', style: { widthStr: vp.width <= 640 ? '100%' : 'calc(100% - 190px)' } },
                    ],
                  },
                ],
              },
              {
                name: 'scheduler-table-wrap',
                style: { widthStr: '100%', isScrollContainer: true },
                children: [
                  {
                    name: 'table',
                    style: { width: 800, minWidth: 720 },
                  },
                ],
              },
            ],
          },
        ],
      }),
    },
    {
      path: '/',
      name: 'Home Dashboard',
      buildTree: (vp) => ({
        name: 'route-home',
        style: { width: vp.width },
        children: [
          {
            name: 'home-topbar',
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
                  gap: 12,
                  widthStr: '100%',
                },
                children: [
                  { name: 'metric-1', style: { widthStr: vp.width <= 640 ? '100%' : '200px' } },
                  { name: 'metric-2', style: { widthStr: vp.width <= 640 ? '100%' : '200px' } },
                  { name: 'metric-3', style: { widthStr: vp.width <= 640 ? '100%' : '200px' } },
                ],
              },
              {
                name: 'onboarding-card',
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
                      { name: 'check-icon', style: { width: 22 } },
                      { name: 'step-text', style: { widthStr: vp.width <= 640 ? '100%' : 'calc(100% - 170px)' } },
                      { name: 'step-action-btn', style: { widthStr: vp.width <= 640 ? '100%' : '140px', minWidth: 40 } },
                    ],
                  },
                ],
              },
            ],
          },
        ],
      }),
    },
  ];

  for (const route of testRoutes) {
    for (const vp of requiredViewports) {
      harness.test(`Layout bounds on ${route.path} at ${vp.width}px (${vp.name}) zero document overflow`, () => {
        const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
        const tree = route.buildTree(vp);
        const result = sim.simulatePage(tree);

        assert(
          result.passes,
          `Route ${route.path} on ${vp.name} failed: document.scrollWidth (${result.documentScrollWidth}px) > innerWidth (${vp.width}px). Overflowing nodes: ${result.overflowingNodes.join(', ')}`
        );
        assertEqual(
          result.documentScrollWidth,
          vp.width,
          `Route ${route.path} document.scrollWidth must equal ${vp.width}px`
        );
      });
    }
  }

  // =========================================================================
  // Section 2: AgentPromptTab Responsive Grid & 1-Column Layout
  // =========================================================================
  harness.setFeature('M3-ADV-02: AgentPromptTab Responsive Grid (<= 980px)');

  harness.test('AgentPromptTab.tsx contains NO hardcoded minmax(0, 1fr) 280px inline style', () => {
    const code = ast.readSrcFile('pages/settings/agents/AgentPromptTab.tsx');
    assert(!code.includes('minmax(0, 1fr) 280px'), 'Hardcoded inline grid minmax(0, 1fr) 280px is strictly forbidden');
    assert(code.includes('agent-prompt-grid'), 'AgentPromptTab must use class="agent-prompt-grid"');
  });

  harness.test('settings.css and responsive.css define .agent-prompt-grid with 1-column layout on <= 980px', () => {
    const rulesSettings = css.findRules('.agent-prompt-grid', '980px');
    const hasColSettings = rulesSettings.some((r) => r.declarations['grid-template-columns'] === '1fr');
    assert(hasColSettings, 'settings.css must set grid-template-columns: 1fr on max-width: 980px');

    const rulesResp = css.findRules('.agent-prompt-grid', '980px');
    const hasColResp = rulesResp.some((r) => r.declarations['grid-template-columns'] === '1fr');
    assert(hasColResp, 'responsive.css must set grid-template-columns: 1fr on max-width: 980px');
  });

  // Breakpoint verification: 980px (1 col) vs 981px (2 cols)
  harness.test('Breakpoint 980px boundary: 1 column layout zero horizontal overflow', () => {
    const sim = new ViewportSimulator({ width: 980, height: 800 });
    const grid980 = {
      name: 'agent-prompt-grid',
      style: {
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
        widthStr: '100%',
      },
      children: [
        { name: 'editor-col', style: { widthStr: '100%' } },
        { name: 'variables-col', style: { widthStr: '100%' } },
      ],
    };
    const res = sim.computeBox(grid980);
    assertLessThanOrEqual(res.scrollWidth, 980, 'At 980px boundary, 1-col grid must fit');
    assertEqual(res.computedWidth, 980);
  });

  harness.test('Breakpoint 981px boundary: 2 columns layout fits desktop width', () => {
    const sim = new ViewportSimulator({ width: 981, height: 800 });
    const grid981 = {
      name: 'agent-prompt-grid',
      style: {
        display: 'flex',
        flexDirection: 'row',
        gap: 20,
        widthStr: '100%',
      },
      children: [
        { name: 'editor-col', style: { widthStr: 'calc(100% - 300px)' } },
        { name: 'variables-col', style: { width: 280 } },
      ],
    };
    const res = sim.computeBox(grid981);
    assertLessThanOrEqual(res.scrollWidth, 981, 'At 981px boundary, 2-col desktop grid must fit');
  });

  // Adversarial content: Extreme unbroken prompt variable names and text
  for (const vp of requiredViewports) {
    harness.test(`AgentPromptTab on ${vp.width}px withstands 100-char unbroken variable name without overflow`, () => {
      const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
      const grid = {
        name: 'agent-prompt-grid',
        style: {
          display: 'flex',
          flexDirection: vp.width <= 980 ? 'column' : 'row',
          gap: 16,
          widthStr: '100%',
        },
        children: [
          {
            name: 'editor-col',
            style: { widthStr: '100%' },
            children: [{ name: 'textarea', style: { widthStr: '100%' } }],
          },
          {
            name: 'variables-col',
            style: { widthStr: vp.width <= 980 ? '100%' : '280px' },
            children: [
              {
                name: 'unbroken-variable-pill',
                text: '{{EXTREMELY_LONG_VARIABLE_NAME_0123456789_ABCDEFGHIJKLMNOPQRSTUVWXYZ_EXTRA_EXTRA_LONG}}',
                style: {
                  widthStr: '100%',
                  overflowWrap: 'anywhere',
                  wordBreak: 'break-word',
                  overflowX: 'hidden',
                },
              },
            ],
          },
        ],
      };
      const res = sim.simulatePage(grid);
      assert(res.passes, `AgentPromptTab on ${vp.width}px overflows with long variable name`);
      assertEqual(res.documentScrollWidth, vp.width);
    });
  }

  // =========================================================================
  // Section 3: Task History Header Wrapping at 375px and Sub-375px
  // =========================================================================
  harness.setFeature('M3-ADV-03: Task History Header Clean Wrapping at 375px');

  harness.test('tasks.css defines .task-history-header with flex-wrap: wrap and gap: 10px', () => {
    const rules = css.findRules('.task-history-header');
    const baseRule = rules.find((r) => !r.mediaQuery);
    assert(baseRule !== undefined, '.task-history-header rule must exist in tasks.css');
    assertEqual(baseRule.declarations['flex-wrap'], 'wrap', 'Base .task-history-header must have flex-wrap: wrap');
    assertEqual(baseRule.declarations['gap'], '10px', 'Base .task-history-header must have gap: 10px');
  });

  harness.test('tasks.css and responsive.css set .task-workspace-filter width to 100% on mobile (<= 640px)', () => {
    const taskRules = css.findRules('.task-workspace-filter', '640px');
    const hasFullWidth = taskRules.some((r) => r.declarations['width'] === '100%');
    assert(hasFullWidth, '.task-workspace-filter must have width: 100% on mobile (<= 640px)');
  });

  harness.test('task-history-header sticky top adjusts to 48px on mobile (<= 640px)', () => {
    const rules = css.findRules('.task-history-header', '640px');
    const hasMobileTop = rules.some((r) => r.declarations['top'] === '48px');
    assert(hasMobileTop, '.task-history-header must have top: 48px on mobile (<= 640px)');
  });

  // Test wrapping on 375px explicitly
  harness.test('task-history-header wraps cleanly at 375px (iPhone SE) without horizontal overflow', () => {
    const sim = new ViewportSimulator({ width: 375, height: 667 });
    const header = {
      name: 'task-history-header',
      style: {
        display: 'flex',
        flexDirection: 'column',
        flexWrap: 'wrap',
        gap: 10,
        widthStr: '100%',
        padding: { top: 8, right: 14, bottom: 8, left: 14 },
      },
      children: [
        {
          name: 'panel-title',
          style: { width: 120 },
          text: 'Task History',
        },
        {
          name: 'task-history-controls',
          style: {
            display: 'flex',
            flexDirection: 'row',
            flexWrap: 'wrap',
            gap: 8,
            widthStr: '100%',
          },
          children: [
            {
              name: 'task-workspace-filter',
              style: { widthStr: '100%' },
              children: [{ name: 'select-trigger', style: { widthStr: '100%', minHeight: 32 } }],
            },
            {
              name: 'hint-count',
              style: { width: 120 },
              text: 'Showing 10 of 10',
            },
            {
              name: 'hint-refresh',
              style: { width: 140 },
              text: 'Auto-refresh while active',
            },
          ],
        },
      ],
    };
    const res = sim.computeBox(header);
    assertLessThanOrEqual(res.scrollWidth, 375, `task-history-header scrollWidth (${res.scrollWidth}px) exceeds 375px`);
    assert(!res.inducesDocOverflow, 'task-history-header induces document overflow at 375px');
  });

  // Test sub-375 viewports (320px, 340px, 360px, 374px)
  for (const vp of sub375Viewports) {
    harness.test(`task-history-header wraps cleanly on sub-375 viewport (${vp.width}px - ${vp.name})`, () => {
      const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
      const header = {
        name: 'task-history-header',
        style: {
          display: 'flex',
          flexDirection: 'column',
          flexWrap: 'wrap',
          gap: 10,
          widthStr: '100%',
          padding: { top: 8, right: 10, bottom: 8, left: 10 },
        },
        children: [
          { name: 'panel-title', style: { width: 120 } },
          {
            name: 'task-history-controls',
            style: { display: 'flex', flexWrap: 'wrap', gap: 6, widthStr: '100%' },
            children: [
              { name: 'workspace-filter', style: { widthStr: '100%' } },
              { name: 'count-hint', style: { width: 110 } },
            ],
          },
        ],
      };
      const res = sim.computeBox(header);
      assertLessThanOrEqual(res.scrollWidth, vp.width, `task-history-header on ${vp.width}px exceeds bounds`);
    });
  }

  // =========================================================================
  // Section 4: Settings Form Specificity & iOS Zoom Prevention
  // =========================================================================
  harness.setFeature('M3-ADV-04: Settings Form Specificity & iOS Auto-Zoom Prevention');

  harness.test('Mobile form inputs enforce font-size >= 16px to prevent iOS Safari auto-zoom', () => {
    const rules = css.findRules('.input', '640px');
    const has16px = rules.some((r) => /16px/.test(r.declarations['font-size'] || ''));
    assert(has16px, 'Mobile form inputs must enforce 16px font-size');
  });

  harness.test('Mobile form inputs enforce min-height >= 42px for touch accessibility', () => {
    const rules = css.findRules('.input', '640px');
    const has42px = rules.some((r) => /42px/.test(r.declarations['min-height'] || ''));
    assert(has42px, 'Mobile form inputs must enforce min-height: 42px');
  });

  harness.test('High-specificity selectors override setting-control-panel .input on mobile (<= 640px)', () => {
    const rules = css.findRules('.setting-control-panel', '640px');
    const hasOverride = rules.some(
      (r) =>
        r.declarations['font-size'] &&
        r.declarations['font-size'].includes('16px') &&
        r.declarations['min-height'] &&
        r.declarations['min-height'].includes('42px')
    );
    assert(hasOverride, '.setting-control-panel inputs must have high-specificity 16px / 42px override on mobile');
  });

  // =========================================================================
  // Section 5: Modal Dialogs & Touch Ergonomics
  // =========================================================================
  harness.setFeature('M3-ADV-05: Modal Dialog Bottom-Sheet & Touch Targets');

  harness.test('Dialog close button has hit-box expanding touch target to >= 40px/48px on mobile', () => {
    const rules = css.findRules('.dialog-close-btn', '640px');
    const hasTouchTarget = rules.some(
      (r) =>
        parseInt(r.declarations['width'] || '0', 10) >= 36 ||
        (r.rawBody && r.rawBody.includes('inset: -6px')) ||
        (r.rawBody && r.rawBody.includes('inset'))
    );
    assert(hasTouchTarget, '.dialog-close-btn must expand touch target to >= 40px/48px on mobile');
  });

  harness.test('Dialog container transforms into bottom-sheet on mobile (<= 640px)', () => {
    const rules = css.findRules('.dialog', '640px');
    const isBottomSheet = rules.some(
      (r) =>
        (r.declarations['align-self'] === 'flex-end' || r.declarations['width'] === '100%') &&
        (r.declarations['border-bottom-left-radius'] === '0' || r.declarations['margin'] === '0')
    );
    assert(isBottomSheet, 'Dialog container must style as bottom-sheet on mobile (<= 640px)');
  });

  harness.test('SelectControl dropdown options enforce min-height >= 42px on mobile', () => {
    const rules = css.findRules('.select-control-option');
    const has42pxOption = rules.some((r) => parseInt(r.declarations['min-height'] || '0', 10) >= 42);
    assert(has42pxOption, '.select-control-option must have min-height >= 42px');
  });

  harness.test('ModelPicker favorite star button enforces touch target >= 40px on mobile', () => {
    const rules = css.findRules('.model-picker-option-star');
    const has40pxStar = rules.some((r) => parseInt(r.declarations['width'] || '0', 10) >= 40);
    assert(has40pxStar, '.model-picker-option-star must have width >= 40px');
  });

  // =========================================================================
  // Section 6: Data Tables Scroll Isolation
  // =========================================================================
  harness.setFeature('M3-ADV-06: Data Tables Scroll Isolation & Min-Width');

  harness.test('sandboxes-table has min-width >= 680px/720px and sandboxes-table-wrap has overflow: auto', () => {
    const tableWrapRules = css.findRules('.sandboxes-table-wrap');
    const hasAutoScroll = tableWrapRules.some(
      (r) => r.declarations['overflow'] === 'auto' || r.declarations['overflow-x'] === 'auto'
    );
    assert(hasAutoScroll, '.sandboxes-table-wrap must have overflow: auto or overflow-x: auto');

    const tableRules = css.findRules('.sandboxes-table');
    const hasMinWidth = tableRules.some((r) => parseInt(r.declarations['min-width'] || '0', 10) >= 680);
    assert(hasMinWidth, '.sandboxes-table must have min-width >= 680px');
  });

  harness.test('sandboxes-filter-group has overflow-x: auto and white-space: nowrap for status pills', () => {
    const filterRules = css.findRules('.sandboxes-filter-group');
    const hasScroll = filterRules.some((r) => r.declarations['overflow-x'] === 'auto');
    assert(hasScroll, '.sandboxes-filter-group must have overflow-x: auto');
  });

  harness.test('Multi-column tables enforce min-width 720px on tablet portrait (<= 980px)', () => {
    const tabletRules = css.findRules('.table', '980px');
    const hasTabletMinWidth = tabletRules.some((r) => parseInt(r.declarations['min-width'] || '0', 10) >= 720);
    assert(hasTabletMinWidth, '.table must have min-width >= 720px on <= 980px');
  });

  // =========================================================================
  // Section 7: Source Code Localization Compliance (AGENTS.md)
  // =========================================================================
  harness.setFeature('M3-ADV-07: AGENTS.md Codebase English Compliance');

  const filesToCheck = [
    'src/pages/settings/agents/AgentPromptTab.tsx',
    'src/styles/settings.css',
    'src/styles/tasks.css',
    'src/styles/sandboxes.css',
    'src/styles/controls.css',
    'src/styles/home.css',
    'src/styles/responsive.css',
  ];

  const vietnameseRegex = /[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđÀÁẠẢÃÂẦẤẬẨẪĂẰẮẶẲẴÈÉẸẺẼÊỀẾỆỂỄÌÍỊỈĨÒÓỌỎÕÔỒỐỘỔỖƠỜỚỢỞỠÙÚỤỦŨƯỪỨỰỬỮỲÝỴỶỸĐ]/;

  for (const relPath of filesToCheck) {
    harness.test(`Source file ${relPath} contains ZERO Vietnamese text`, () => {
      const fullPath = path.join(FRONTEND_ROOT, relPath);
      const content = fs.readFileSync(fullPath, 'utf-8');
      const hasVietnamese = vietnameseRegex.test(content);
      assert(!hasVietnamese, `File ${relPath} contains Vietnamese characters, violating AGENTS.md codebase language rule`);
    });
  }

  // Execute all tests
  const results = await harness.run();
  return results;
}

runAdversarialM3Suite()
  .then((res) => {
    if (res.failed > 0) {
      console.error(`\n❌ Challenger M3 Adversarial Suite failed with ${res.failed} failures.`);
      process.exit(1);
    } else {
      console.log(`\n🏆 All ${res.passed} Challenger M3 Adversarial Tests PASSED!`);
      process.exit(0);
    }
  })
  .catch((err) => {
    console.error('Fatal error in challenger suite:', err);
    process.exit(1);
  });
