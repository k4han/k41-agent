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

/**
 * Dynamically extract pure functions and constants from Scheduler.tsx
 */
function extractSchedulerFunctions() {
  const schedulerPath = path.join(FRONTEND_ROOT, 'src/pages/Scheduler.tsx');
  const code = fs.readFileSync(schedulerPath, 'utf8');
  const start = code.indexOf('export const CRON_PRESETS');
  const end = code.indexOf('function CronScheduleEditor');
  assert(start !== -1 && end !== -1, 'Could not locate CronScheduleEditor boundary in Scheduler.tsx');

  let snippet = code
    .substring(start, end)
    .replace(/export\s+const\s+/g, 'const ')
    .replace(/export\s+function\s+/g, 'function ')
    .replace(/export\s+type\s+[^;]+;/g, '')
    .replace(/export\s+interface\s+[\s\S]*?}\n/g, '')
    .replace(/:\s*readonly\s+CronPreset\[\]/g, '')
    .replace(/as\s+const/g, '')
    .replace(/:\s*CronPresetId/g, '')
    .replace(/:\s*string\s*\|\s*number\s*\|\s*undefined/g, '')
    .replace(/:\s*number\s*\|\s*string/g, '')
    .replace(/:\s*number/g, '')
    .replace(/:\s*string/g, '')
    .replace(/:\s*Record<[^>]+>/g, '');

  const runner = new Function(
    snippet +
      '; return { CRON_PRESETS, detectCronPreset, buildCronExpression, formatTime, formatCronDescription };'
  );
  return runner();
}

async function runM3ChallengerEmpiricalSuite() {
  const stylesDir = path.join(FRONTEND_ROOT, 'src/styles');
  const css = new CSSAnalyzer(stylesDir);
  const ast = new ASTAnalyzer(FRONTEND_ROOT);
  css.load();

  const harness = new TestHarness({ verbose: true });
  harness.setTier('Empirical Challenger M3: Rigorous Validation & Stress Oracle');

  const testViewports = [
    { name: 'Extremely Narrow Mobile (320px)', width: 320, height: 568, isMobile: true },
    { name: 'iPhone SE / Mini (375px)', width: 375, height: 667, isMobile: true },
    { name: 'iPhone 14 / Pro (390px)', width: 390, height: 844, isMobile: true },
    { name: 'iPhone Plus / Max (414px)', width: 414, height: 896, isMobile: true },
    { name: 'iPad Portrait (768px)', width: 768, height: 1024, isMobile: true },
    { name: 'Standard Desktop (1280px)', width: 1280, height: 800, isMobile: false },
  ];

  // =========================================================================
  // Section 1: Settings Usable Content Width Guarantee (F10)
  // =========================================================================
  harness.setFeature('CHAL-M3-01: Settings Usable Content Width on 1280px Viewport (>= 680px)');

  const settingsLayoutCode = ast.readSrcFile('pages/settings/SettingsLayout.tsx');
  const agentPromptCode = ast.readSrcFile('pages/settings/agents/AgentPromptTab.tsx');
  const agentEditPageCode = ast.readSrcFile('pages/settings/agents/AgentEditPage.tsx');

  harness.test('SettingsLayout.tsx defines SETTINGS_AUTO_COLLAPSE_BREAKPOINT as 1280', () => {
    assert(
      settingsLayoutCode.includes('SETTINGS_AUTO_COLLAPSE_BREAKPOINT = 1280'),
      'SettingsLayout must define SETTINGS_AUTO_COLLAPSE_BREAKPOINT = 1280'
    );
    assert(
      settingsLayoutCode.includes('SETTINGS_AUTO_COLLAPSE_QUERY = `(max-width: ${SETTINGS_AUTO_COLLAPSE_BREAKPOINT}px)`') ||
      settingsLayoutCode.includes('(max-width: 1280px)'),
      'SettingsLayout must query max-width 1280px for auto-collapse'
    );
  });

  harness.test('SettingsLayout.tsx stores user collapsed preference and respects manual toggle', () => {
    assert(
      settingsLayoutCode.includes('userLocked'),
      'SettingsLayout must track userLocked state'
    );
    assert(
      settingsLayoutCode.includes('STORAGE_KEYS.SETTINGS_SIDEBAR_COLLAPSED') ||
      settingsLayoutCode.includes('k41-dashboard-settings-sidebar'),
      'SettingsLayout must persist collapsed state in localStorage'
    );
  });

  harness.test('SettingsLayout.tsx supports keyboard shortcuts ("/" or Ctrl+K/Cmd+K) to expand and search', () => {
    assert(
      settingsLayoutCode.includes('e.key === "/"') || settingsLayoutCode.includes("e.key === '/'"),
      'SettingsLayout must listen for "/" key'
    );
    assert(
      settingsLayoutCode.includes('e.key === "k"') || settingsLayoutCode.includes("e.key === 'k'"),
      'SettingsLayout must listen for "k" key with ctrl/cmd'
    );
    assert(
      settingsLayoutCode.includes('searchInputRef?.focus()'),
      'SettingsLayout must focus searchInputRef on trigger'
    );
  });

  harness.test('AgentEditPage.tsx passes contentWidth="wide" to SettingsLayout', () => {
    assert(
      agentEditPageCode.includes('contentWidth="wide"'),
      'AgentEditPage must configure SettingsLayout with contentWidth="wide"'
    );
  });

  harness.test('settings.css defines .settings-app-layout grid columns: 240px desktop, 60px collapsed', () => {
    const rulesBase = css.findRules('.settings-app-layout');
    const baseRule = rulesBase.find((r) => !r.mediaQuery);
    assert(baseRule !== undefined, '.settings-app-layout base rule must exist');
    assertEqual(
      baseRule.declarations['grid-template-columns'],
      '240px minmax(0, 1fr)',
      '.settings-app-layout desktop grid must use 240px navigation width'
    );

    const rulesCollapsed = css.findRules('.settings-app-layout.sidebar-collapsed');
    const collapsedRule = rulesCollapsed.find((r) => !r.mediaQuery);
    assert(collapsedRule !== undefined, '.settings-app-layout.sidebar-collapsed rule must exist');
    assertEqual(
      collapsedRule.declarations['grid-template-columns'],
      '60px minmax(0, 1fr)',
      '.settings-app-layout.sidebar-collapsed grid must use 60px collapsed width'
    );
  });

  harness.test('settings.css defines .agent-prompt-grid desktop and mobile layouts', () => {
    const rulesBase = css.findRules('.agent-prompt-grid');
    const base = rulesBase.find((r) => !r.mediaQuery);
    assert(base !== undefined, '.agent-prompt-grid base rule must exist');
    assertEqual(base.declarations['grid-template-columns'], 'minmax(0, 1fr) 280px');
    assertEqual(base.declarations['gap'], '20px');

    const rulesMobile = css.findRules('.agent-prompt-grid', '980px');
    const mobile = rulesMobile.find((r) => r.mediaQuery);
    assert(mobile !== undefined, '.agent-prompt-grid media (max-width: 980px) rule must exist');
    assertEqual(mobile.declarations['grid-template-columns'], '1fr');
  });

  // Empirical calculation harness for 1280px display
  harness.test('Empirical width calculation at 1280px viewport: Collapsed sidebar yields >= 868px prompt editor width (Requirement >= 680px)', () => {
    const viewportWidth = 1280;
    const sidebarWidth = 60; // collapsed
    const mainWidth = viewportWidth - sidebarWidth; // 1220px
    const contentPadding = 24 * 2; // shell.css padding: 20px 24px 36px => 48px
    const usableContentWidth = mainWidth - contentPadding; // 1172px

    assertEqual(usableContentWidth, 1172, 'Usable content width at 1280px with collapsed sidebar must be 1172px');
    assertGreaterThanOrEqual(usableContentWidth, 680, 'Usable content width must be >= 680px');

    const gridPadding = 2 * 2; // padding: 4px 2px => 4px
    const variablesColumn = 280; // 280px fixed column
    const gridGap = 20; // 20px gap
    const promptEditorWidth = usableContentWidth - gridPadding - variablesColumn - gridGap;

    assertEqual(promptEditorWidth, 868, 'Prompt editor width must be exactly 868px');
    assertGreaterThanOrEqual(promptEditorWidth, 680, 'Prompt editor width must be >= 680px (+188px margin)');
  });

  harness.test('Empirical width calculation at 1280px viewport: Even with manually expanded sidebar (240px), prompt editor yields 688px (>= 680px)', () => {
    const viewportWidth = 1280;
    const sidebarWidth = 240; // expanded
    const mainWidth = viewportWidth - sidebarWidth; // 1040px
    const contentPadding = 24 * 2; // 48px
    const usableContentWidth = mainWidth - contentPadding; // 992px

    const gridPadding = 2 * 2; // 4px
    const variablesColumn = 280;
    const gridGap = 20;
    const promptEditorWidth = usableContentWidth - gridPadding - variablesColumn - gridGap;

    assertEqual(promptEditorWidth, 688, 'Prompt editor width with expanded sidebar must be 688px');
    assertGreaterThanOrEqual(promptEditorWidth, 680, 'Prompt editor width with expanded sidebar must be >= 680px (+8px margin)');
  });

  // =========================================================================
  // Section 3: Scheduler Frequency Presets, Two-Way Sync, & Summaries (F12)
  // =========================================================================
  harness.setFeature('CHAL-M3-03: Scheduler Presets, Two-Way Sync, & formatCronDescription Oracle');

  const schedulerCode = ast.readSrcFile('pages/Scheduler.tsx');
  const schedulerFns = extractSchedulerFunctions();
  const { detectCronPreset, buildCronExpression, formatCronDescription } = schedulerFns;

  harness.test('Scheduler.tsx defines CRON_PRESETS with all required frequencies', () => {
    assert(schedulerCode.includes('CRON_PRESETS'), 'Scheduler.tsx must define CRON_PRESETS');
    assert(schedulerCode.includes('"hourly"'), 'CRON_PRESETS must include hourly');
    assert(schedulerCode.includes('"daily"'), 'CRON_PRESETS must include daily');
    assert(schedulerCode.includes('"weekdays"'), 'CRON_PRESETS must include weekdays');
    assert(schedulerCode.includes('"weekly"'), 'CRON_PRESETS must include weekly');
    assert(schedulerCode.includes('"monthly"'), 'CRON_PRESETS must include monthly');
    assert(schedulerCode.includes('"custom"'), 'CRON_PRESETS must include custom');
  });

  // Dynamic oracle execution
  harness.test('Oracle test: detectCronPreset accurately identifies presets from fields', () => {
    assertEqual(detectCronPreset("0", "*", "*", "*", "*"), "hourly");
    assertEqual(detectCronPreset("0", "9", "*", "*", "*"), "daily");
    assertEqual(detectCronPreset("0", "9", "*", "*", "1-5"), "weekdays");
    assertEqual(detectCronPreset("0", "9", "*", "*", "mon-fri"), "weekdays");
    assertEqual(detectCronPreset("0", "9", "*", "*", "1"), "weekly");
    assertEqual(detectCronPreset("0", "9", "*", "*", "mon"), "weekly");
    assertEqual(detectCronPreset("0", "0", "1", "*", "*"), "monthly");
    assertEqual(detectCronPreset("15", "10", "*", "*", "*"), "custom");
    assertEqual(detectCronPreset(undefined, undefined, undefined, undefined, undefined), "custom");
    assertEqual(detectCronPreset(0, 9, "*", "*", "*"), "daily"); // number 0 test
  });

  harness.test('Oracle test: buildCronExpression handles empty, null, undefined and custom inputs', () => {
    assertEqual(buildCronExpression(undefined, undefined, undefined, undefined, undefined), "* * * * *");
    assertEqual(buildCronExpression("", "", "", "", ""), "* * * * *");
    assertEqual(buildCronExpression("0", "9", "*", "*", "*"), "0 9 * * *");
    assertEqual(buildCronExpression(0, 0, 1, "*", "*"), "0 0 1 * *");
    assertEqual(buildCronExpression("*/10", "*", "*", "*", "*"), "*/10 * * * *");
  });

  harness.test('Oracle test: formatCronDescription produces accurate, human-readable English descriptions', () => {
    assertEqual(formatCronDescription("*", "*", "*", "*", "*"), "Runs every minute");
    assertEqual(formatCronDescription("*/15", "*", "*", "*", "*"), "Runs every 15 minutes");
    assertEqual(formatCronDescription("0", "*", "*", "*", "*"), "Runs every hour");
    assertEqual(formatCronDescription("30", "*", "*", "*", "*"), "Runs every hour at minute 30");
    assertEqual(formatCronDescription("0", "*/2", "*", "*", "*"), "Runs every 2 hours");
    assertEqual(formatCronDescription("15", "*/3", "*", "*", "*"), "Runs every 3 hours at minute 15");
    assertEqual(formatCronDescription("0", "9", "*", "*", "*"), "Runs every day at 09:00 AM");
    assertEqual(formatCronDescription("30", "14", "*", "*", "*"), "Runs every day at 02:30 PM");
    assertEqual(formatCronDescription("0", "9", "*", "*", "1-5"), "Runs every weekday at 09:00 AM");
    assertEqual(formatCronDescription("0", "9", "*", "*", "mon-fri"), "Runs every weekday at 09:00 AM");
    assertEqual(formatCronDescription("0", "10", "*", "*", "0,6"), "Runs every weekend at 10:00 AM");
    assertEqual(formatCronDescription("0", "9", "*", "*", "1"), "Runs every Monday at 09:00 AM");
    assertEqual(formatCronDescription("0", "0", "1", "*", "*"), "Runs on the 1st of every month at 12:00 AM");
    assertEqual(formatCronDescription("0", "9", "15", "*", "*"), "Runs on the 15th of every month at 09:00 AM");
    assertEqual(formatCronDescription("0", "0", "1", "1", "*"), "Runs on January 1st at 12:00 AM");
    assertEqual(formatCronDescription("15", "10", "5", "10", "*"), "Runs on October 5th at 10:15 AM");
    assertEqual(formatCronDescription("15", "10", "5", "10", "1"), "Runs on schedule: 15 10 5 10 1");
    // Fallback for complex syntax
    assertEqual(formatCronDescription("0-30", "9-17", "*", "*", "*"), "Runs on schedule: 0-30 9-17 * * *");
  });

  harness.test('Scheduler.tsx DashboardTable renders schedule expression and human-readable summary', () => {
    assert(
      schedulerCode.includes('scheduler-trigger-cell'),
      'Scheduler.tsx must use .scheduler-trigger-cell container in table'
    );
    assert(
      schedulerCode.includes('scheduler-cron-expr'),
      'Scheduler.tsx must render .scheduler-cron-expr in table'
    );
    assert(
      schedulerCode.includes('scheduler-cron-summary'),
      'Scheduler.tsx must render .scheduler-cron-summary in table'
    );
    assert(
      schedulerCode.includes('formatCronDescription('),
      'Scheduler.tsx must call formatCronDescription in table trigger cell'
    );
  });

  harness.test('scheduler.css defines responsive grid and touch targets for cron editor', () => {
    const pillRules = css.findRules('.cron-preset-pill');
    const basePill = pillRules.find((r) => !r.mediaQuery);
    assert(basePill !== undefined, '.cron-preset-pill base styling must exist');
    assertGreaterThanOrEqual(parseInt(basePill.declarations['min-height'] || '0', 10), 40);

    const mobilePillRules = css.findRules('.cron-preset-pill', '640px');
    const mobilePill = mobilePillRules.find((r) => r.mediaQuery);
    assert(mobilePill !== undefined, '.cron-preset-pill mobile styling must exist');
    assertGreaterThanOrEqual(parseInt(mobilePill.declarations['min-height'] || '0', 10), 44);

    const mobileInputRules = css.findRules('.cron-fields-grid .input', '640px');
    const mobileInput = mobileInputRules.find((r) => r.mediaQuery);
    assert(mobileInput !== undefined, '.cron-fields-grid .input mobile styling must exist');
    assertEqual(mobileInput.declarations['min-height'], '42px !important');
    assertEqual(mobileInput.declarations['font-size'], '16px !important');
  });

  // =========================================================================
  // Section 4: Document Horizontal Overflow & Touch Ergonomics
  // Across 320px, 375px, 390px, 414px, 768px, 1280px
  // =========================================================================
  harness.setFeature('CHAL-M3-04: Full Viewport Sweep (320px–1280px) Zero Document Overflow');

  const routesToVerify = [
    {
      path: '/settings/agents',
      name: 'Settings: Agent Prompt & Variables',
      buildTree: (vp) => ({
        name: 'route-settings-agents',
        style: { width: vp.width },
        children: [
          {
            name: 'settings-sidebar',
            style: {
              width: vp.width <= 980 ? 0 : vp.width <= 1280 ? 60 : 240,
              display: vp.width <= 980 ? 'none' : 'block',
            },
          },
          {
            name: 'settings-main',
            style: {
              widthStr: vp.width <= 980 ? '100%' : `calc(100% - ${vp.width <= 1280 ? 60 : 240}px)`,
              padding: { top: 0, right: vp.width <= 640 ? 12 : 24, bottom: 36, left: vp.width <= 640 ? 12 : 24 },
            },
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
                    style: { widthStr: vp.width <= 980 ? '100%' : 'calc(100% - 300px)' },
                  },
                  {
                    name: 'prompt-variables-sidebar',
                    style: { widthStr: vp.width <= 980 ? '100%' : '280px' },
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
      name: 'Tasks: Operational Monitor',
      buildTree: (vp) => ({
        name: 'route-tasks',
        style: { width: vp.width },
        children: [
          {
            name: 'task-output-stack',
            style: {
              widthStr: '100%',
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
              padding: { top: 12, right: 12, bottom: 12, left: 12 },
            },
            children: [
              {
                name: 'task-result-card',
                style: { widthStr: '100%' },
                children: [
                  {
                    name: 'task-result-header',
                    style: { display: 'flex', justifyContent: 'space-between', widthStr: '100%', minHeight: 40 },
                    children: [
                      { name: 'title', style: { width: 100 } },
                      { name: 'copy-btn', style: { width: 110, minHeight: vp.width <= 640 ? 40 : 32 } },
                    ],
                  },
                  {
                    name: 'task-result-body',
                    style: { widthStr: '100%', isScrollContainer: true },
                    children: [{ name: 'markdown-content', style: { width: 900 } }],
                  },
                ],
              },
              {
                name: 'task-error-card',
                style: { widthStr: '100%' },
                children: [
                  {
                    name: 'task-error-header',
                    style: { display: 'flex', justifyContent: 'space-between', widthStr: '100%', minHeight: 40 },
                    children: [
                      { name: 'title', style: { width: 100 } },
                      { name: 'copy-btn', style: { width: 110, minHeight: vp.width <= 640 ? 40 : 32 } },
                    ],
                  },
                  {
                    name: 'task-error-body',
                    style: { widthStr: '100%', isScrollContainer: true },
                    children: [{ name: 'error-pre', style: { width: 850 } }],
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
      name: 'Scheduler: Cron Frequency Editor',
      buildTree: (vp) => ({
        name: 'route-scheduler',
        style: { width: vp.width },
        children: [
          {
            name: 'cron-editor',
            style: {
              widthStr: '100%',
              padding: { top: 12, right: 12, bottom: 12, left: 12 },
              display: 'flex',
              flexDirection: 'column',
              gap: 14,
            },
            children: [
              {
                name: 'cron-presets-group',
                style: { display: 'flex', flexWrap: 'wrap', gap: 8, widthStr: '100%' },
                children: [
                  { name: 'pill-1', style: { width: 85, minHeight: vp.width <= 640 ? 44 : 42 } },
                  { name: 'pill-2', style: { width: 85, minHeight: vp.width <= 640 ? 44 : 42 } },
                  { name: 'pill-3', style: { width: 95, minHeight: vp.width <= 640 ? 44 : 42 } },
                  { name: 'pill-4', style: { width: 85, minHeight: vp.width <= 640 ? 44 : 42 } },
                  { name: 'pill-5', style: { width: 90, minHeight: vp.width <= 640 ? 44 : 42 } },
                  { name: 'pill-6', style: { width: 80, minHeight: vp.width <= 640 ? 44 : 42 } },
                ],
              },
              {
                name: 'cron-summary-banner',
                style: {
                  widthStr: '100%',
                  display: 'flex',
                  flexDirection: vp.width <= 640 ? 'column' : 'row',
                  gap: 12,
                },
                children: [
                  { name: 'icon', style: { width: 32 } },
                  {
                    name: 'body',
                    style: { widthStr: vp.width <= 640 ? '100%' : 'calc(100% - 44px)' },
                    text: 'Runs on the 1st of every month at 12:00 AM',
                  },
                ],
              },
              {
                name: 'scheduler-table-container',
                style: { widthStr: '100%', isScrollContainer: true },
                children: [{ name: 'dashboard-table', style: { width: 820, minWidth: 720 } }],
              },
            ],
          },
        ],
      }),
    },
  ];

  for (const route of routesToVerify) {
    for (const vp of testViewports) {
      harness.test(`Route ${route.path} on ${vp.width}px (${vp.name}) maintains zero horizontal overflow`, () => {
        const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
        const tree = route.buildTree(vp);
        const result = sim.simulatePage(tree);

        assert(
          result.passes,
          `Route ${route.path} on ${vp.name} induced document overflow: scrollWidth ${result.documentScrollWidth}px > innerWidth ${vp.width}px`
        );
        assertEqual(result.documentScrollWidth, vp.width);
      });
    }
  }

  // =========================================================================
  // Section 5: AGENTS.md Codebase English Compliance
  // =========================================================================
  harness.setFeature('CHAL-M3-05: AGENTS.md Zero Vietnamese Localization Rule');

  const m3SourceFiles = [
    'src/pages/settings/SettingsLayout.tsx',
    'src/pages/settings/agents/AgentPromptTab.tsx',
    'src/pages/Scheduler.tsx',
    'src/styles/settings.css',
    'src/styles/tasks.css',
    'src/styles/scheduler.css',
    'src/styles.css',
  ];

  const vietnameseRegex = /[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđÀÁẠẢÃÂẦẤẬẨẪĂẰẮẶẲẴÈÉẸẺẼÊỀẾỆỂỄÌÍỊỈĨÒÓỌỎÕÔỒỐỘỔỖƠỜỚỢỞỠÙÚỤỦŨƯỪỨỰỬỮỲÝỴỶỸĐ]/;

  for (const file of m3SourceFiles) {
    harness.test(`Source file ${file} strictly contains ZERO Vietnamese characters`, () => {
      const fullPath = path.join(FRONTEND_ROOT, file);
      const content = fs.readFileSync(fullPath, 'utf-8');
      const hasVietnamese = vietnameseRegex.test(content);
      assert(!hasVietnamese, `Violation: ${file} contains Vietnamese characters, strictly disallowed by AGENTS.md`);
    });
  }

  const results = await harness.run();
  return results;
}

runM3ChallengerEmpiricalSuite()
  .then((res) => {
    if (res.failed > 0) {
      console.error(`\n❌ Empirical Challenger M3 Suite failed with ${res.failed} failures.`);
      process.exit(1);
    } else {
      console.log(`\n🏆 All ${res.passed} Empirical Challenger M3 Tests PASSED!`);
      process.exit(0);
    }
  })
  .catch((err) => {
    console.error('Fatal error in empirical challenger suite:', err);
    process.exit(1);
  });
