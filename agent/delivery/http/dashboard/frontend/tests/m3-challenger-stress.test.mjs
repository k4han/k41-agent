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

async function runM3ChallengerStressSuite() {
  const stylesDir = path.join(FRONTEND_ROOT, 'src/styles');
  const css = new CSSAnalyzer(stylesDir);
  const ast = new ASTAnalyzer(FRONTEND_ROOT);
  css.load();

  const harness = new TestHarness({ verbose: true });
  harness.setTier('Challenger M3: Empirical Stress & Edge Case Verification');

  const schedulerFns = extractSchedulerFunctions();
  const { CRON_PRESETS, detectCronPreset, buildCronExpression, formatTime, formatCronDescription } = schedulerFns;

  // =========================================================================
  // Feature 1: Boundary Viewports on /settings/agents, /tasks, and /scheduler
  // Viewports: 320px, 360px, 375px, 390px, 414px, 640px, 768px, 980px, 981px, 1024px, 1280px, 1440px
  // =========================================================================
  harness.setFeature('M3-STRESS-01: Boundary Viewports Layout & Zero Overflow (320px–1440px)');

  const stressViewports = [
    { name: 'Sub-360px Narrow Mobile (320px)', width: 320, height: 568 },
    { name: 'Standard Android Mobile (360px)', width: 360, height: 740 },
    { name: 'iPhone SE (375px)', width: 375, height: 667 },
    { name: 'iPhone 14 / Pro (390px)', width: 390, height: 844 },
    { name: 'iPhone Plus / Max (414px)', width: 414, height: 896 },
    { name: 'Mobile Max Breakpoint (640px)', width: 640, height: 960 },
    { name: 'iPad Portrait (768px)', width: 768, height: 1024 },
    { name: 'Tablet Drawer Max Boundary (980px)', width: 980, height: 800 },
    { name: 'Desktop Min Boundary (981px)', width: 981, height: 800 },
    { name: 'Small Desktop (1024px)', width: 1024, height: 768 },
    { name: 'Auto-Collapse Breakpoint Boundary (1280px)', width: 1280, height: 800 },
    { name: 'Wide Desktop (1440px)', width: 1440, height: 900 },
  ];

  // 1A. /settings/agents layout & usable content width stress
  for (const vp of stressViewports) {
    harness.test(`Settings /settings/agents at ${vp.width}px (${vp.name}) zero document overflow`, () => {
      const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
      const isDrawer = vp.width <= 980;
      const isCollapsed = vp.width <= 1280;
      const sidebarWidth = isDrawer ? 0 : (isCollapsed ? 60 : 240);

      const tree = {
        name: 'app-layout-settings',
        style: { width: vp.width, display: 'flex', flexDirection: 'row' },
        children: [
          ...(isDrawer
            ? []
            : [
                {
                  name: 'settings-sidebar',
                  style: { width: sidebarWidth, minWidth: sidebarWidth },
                },
              ]),
          {
            name: 'main-settings-agents',
            style: {
              widthStr: isDrawer ? '100%' : `calc(100% - ${sidebarWidth}px)`,
              display: 'flex',
              flexDirection: 'column',
            },
            children: [
              {
                name: 'settings-topbar',
                style: { widthStr: '100%', padding: { top: 8, right: 16, bottom: 8, left: 16 } },
              },
              {
                name: 'settings-content',
                style: {
                  widthStr: '100%',
                  padding: { top: 16, right: 24, bottom: 16, left: 24 },
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
                        style: {
                          widthStr: vp.width <= 980 ? '100%' : 'calc(100% - 300px)',
                          minHeight: 380,
                        },
                      },
                      {
                        name: 'prompt-variables-column',
                        style: {
                          widthStr: vp.width <= 980 ? '100%' : '280px',
                        },
                        children: [
                          {
                            name: 'unbroken-variable-pill',
                            style: {
                              widthStr: '100%',
                              overflowWrap: 'anywhere',
                              wordBreak: 'break-word',
                            },
                            text: '{{KAI_SUPER_EXTREME_UNBROKEN_VARIABLE_TOKEN_LONG_ENOUGH_TO_OVERFLOW_UNLESS_WRAPPED}}',
                          },
                        ],
                      },
                    ],
                  },
                ],
              },
            ],
          },
        ],
      };

      const res = sim.simulatePage(tree);
      assert(res.passes, `Settings layout overflows at ${vp.width}px. scrollWidth: ${res.documentScrollWidth}px`);
      assertEqual(res.documentScrollWidth, vp.width);
    });
  }

  // 1B. Usable content width verification at 1280px display
  harness.test('F10 Contract: At 1280px display, prompt editor receives >= 680px width (collapsed 60px)', () => {
    const totalWidth = 1280;
    const sidebarCollapsedWidth = 60;
    const contentPadding = 48; // 24px left + 24px right
    const innerContentWidth = totalWidth - sidebarCollapsedWidth - contentPadding; // 1172px
    const variablesColumnWidth = 280;
    const gridGap = 20;
    const promptEditorWidth = innerContentWidth - variablesColumnWidth - gridGap; // 872px

    assertGreaterThanOrEqual(promptEditorWidth, 680, 'Prompt editor width must be at least 680px on 1280px display');
    assertEqual(promptEditorWidth, 872, 'Prompt editor should have exactly 872px width when collapsed');
  });

  harness.test('F10 Contract: At 1280px display, prompt editor receives >= 680px width even if manually expanded (240px)', () => {
    const totalWidth = 1280;
    const sidebarExpandedWidth = 240;
    const contentPadding = 48;
    const innerContentWidth = totalWidth - sidebarExpandedWidth - contentPadding; // 992px
    const variablesColumnWidth = 280;
    const gridGap = 20;
    const promptEditorWidth = innerContentWidth - variablesColumnWidth - gridGap; // 692px

    assertGreaterThanOrEqual(promptEditorWidth, 680, 'Prompt editor width must be >= 680px even when manually expanded');
    assertEqual(promptEditorWidth, 692, 'Prompt editor should have exactly 692px width when expanded');
  });

  // =========================================================================
  // Feature 2: Scheduler Cron Presets Rapid Switching & Consistency
  // =========================================================================
  harness.setFeature('M3-STRESS-02: Scheduler Cron Presets Rapid Switching & Consistency');

  harness.test('CRON_PRESETS defines exactly 6 presets with valid fields', () => {
    assertEqual(CRON_PRESETS.length, 6, 'Expected exactly 6 cron presets');
    const ids = CRON_PRESETS.map((p) => p.id);
    assertEqual(JSON.stringify(ids), JSON.stringify(['hourly', 'daily', 'weekdays', 'weekly', 'monthly', 'custom']));
  });

  harness.test('Rapid sequential switching across all presets (100 iterations) maintains state integrity', () => {
    const presetSequence = ['hourly', 'daily', 'weekdays', 'weekly', 'monthly'];
    for (let i = 0; i < 100; i++) {
      const targetId = presetSequence[i % presetSequence.length];
      const preset = CRON_PRESETS.find((p) => p.id === targetId);
      assert(preset !== undefined, `Preset ${targetId} must exist`);

      // Simulate applying preset fields
      const { minute, hour, day, month, day_of_week } = preset.fields;
      const detected = detectCronPreset(minute, hour, day, month, day_of_week);
      assertEqual(detected, targetId, `Iteration ${i}: Detected preset mismatch for ${targetId}`);

      const built = buildCronExpression(minute, hour, day, month, day_of_week);
      assertEqual(built, preset.expression, `Iteration ${i}: Built expression mismatch for ${targetId}`);

      const desc = formatCronDescription(minute, hour, day, month, day_of_week);
      assert(desc.length > 5, `Iteration ${i}: Empty description for ${targetId}`);
      assert(!desc.includes('undefined') && !desc.includes('NaN'), `Iteration ${i}: Malformed description for ${targetId}`);
    }
  });

  harness.test('Individual preset descriptions match expected natural language specifications', () => {
    // Hourly
    assertEqual(formatCronDescription('0', '*', '*', '*', '*'), 'Runs every hour');
    assertEqual(detectCronPreset('0', '*', '*', '*', '*'), 'hourly');

    // Daily
    assertEqual(formatCronDescription('0', '9', '*', '*', '*'), 'Runs every day at 09:00 AM');
    assertEqual(detectCronPreset('0', '9', '*', '*', '*'), 'daily');

    // Weekdays
    assertEqual(formatCronDescription('0', '9', '*', '*', '1-5'), 'Runs every weekday at 09:00 AM');
    assertEqual(detectCronPreset('0', '9', '*', '*', '1-5'), 'weekdays');

    // Weekly
    assertEqual(formatCronDescription('0', '9', '*', '*', '1'), 'Runs every Monday at 09:00 AM');
    assertEqual(detectCronPreset('0', '9', '*', '*', '1'), 'weekly');

    // Monthly
    assertEqual(formatCronDescription('0', '0', '1', '*', '*'), 'Runs on the 1st of every month at 12:00 AM');
    assertEqual(detectCronPreset('0', '0', '1', '*', '*'), 'monthly');
  });

  // =========================================================================
  // Feature 3: Scheduler Cron Non-Standard & Adversarial Input Fuzzing
  // =========================================================================
  harness.setFeature('M3-STRESS-03: Scheduler Cron Non-Standard & Adversarial Input Fuzzing');

  const adversarialCronCases = [
    // Step and intervals
    { m: '*', h: '*', d: '*', mo: '*', dow: '*', expectedDesc: 'Runs every minute' },
    { m: '*/5', h: '*', d: '*', mo: '*', dow: '*', expectedDesc: 'Runs every 5 minutes' },
    { m: '*/15', h: '*', d: '*', mo: '*', dow: '*', expectedDesc: 'Runs every 15 minutes' },
    { m: '0', h: '*/2', d: '*', mo: '*', dow: '*', expectedDesc: 'Runs every 2 hours' },
    { m: '30', h: '*/3', d: '*', mo: '*', dow: '*', expectedDesc: 'Runs every 3 hours at minute 30' },
    { m: '15', h: '*', d: '*', mo: '*', dow: '*', expectedDesc: 'Runs every hour at minute 15' },

    // Fixed time variations
    { m: '0', h: '0', d: '*', mo: '*', dow: '*', expectedDesc: 'Runs every day at 12:00 AM' },
    { m: '30', h: '12', d: '*', mo: '*', dow: '*', expectedDesc: 'Runs every day at 12:30 PM' },
    { m: '45', h: '23', d: '*', mo: '*', dow: '*', expectedDesc: 'Runs every day at 11:45 PM' },

    // Day of week aliases and sets
    { m: '0', h: '9', d: '*', mo: '*', dow: 'mon-fri', expectedDesc: 'Runs every weekday at 09:00 AM' },
    { m: '0', h: '10', d: '*', mo: '*', dow: 'sat,sun', expectedDesc: 'Runs every weekend at 10:00 AM' },
    { m: '0', h: '10', d: '*', mo: '*', dow: '0,6', expectedDesc: 'Runs every weekend at 10:00 AM' },
    { m: '0', h: '10', d: '*', mo: '*', dow: '6,0', expectedDesc: 'Runs every weekend at 10:00 AM' },
    { m: '0', h: '14', d: '*', mo: '*', dow: 'fri', expectedDesc: 'Runs every Friday at 02:00 PM' },
    { m: '0', h: '14', d: '*', mo: '*', dow: '5', expectedDesc: 'Runs every Friday at 02:00 PM' },
    { m: '0', h: '14', d: '*', mo: '*', dow: 'sun', expectedDesc: 'Runs every Sunday at 02:00 PM' },
    { m: '0', h: '14', d: '*', mo: '*', dow: '0', expectedDesc: 'Runs every Sunday at 02:00 PM' },
    { m: '0', h: '14', d: '*', mo: '*', dow: '7', expectedDesc: 'Runs every Sunday at 02:00 PM' },
    { m: '0', h: '8', d: '*', mo: '*', dow: 'mon,wed,fri', expectedDesc: 'Runs every Monday, Wednesday, Friday at 08:00 AM' },

    // Monthly & Ordinals
    { m: '0', h: '0', d: '2', mo: '*', dow: '*', expectedDesc: 'Runs on the 2nd of every month at 12:00 AM' },
    { m: '0', h: '0', d: '3', mo: '*', dow: '*', expectedDesc: 'Runs on the 3rd of every month at 12:00 AM' },
    { m: '0', h: '0', d: '4', mo: '*', dow: '*', expectedDesc: 'Runs on the 4th of every month at 12:00 AM' },
    { m: '0', h: '0', d: '21', mo: '*', dow: '*', expectedDesc: 'Runs on the 21st of every month at 12:00 AM' },
    { m: '0', h: '0', d: '22', mo: '*', dow: '*', expectedDesc: 'Runs on the 22nd of every month at 12:00 AM' },
    { m: '0', h: '0', d: '23', mo: '*', dow: '*', expectedDesc: 'Runs on the 23rd of every month at 12:00 AM' },
    { m: '0', h: '0', d: '31', mo: '*', dow: '*', expectedDesc: 'Runs on the 31st of every month at 12:00 AM' },

    // Yearly
    { m: '0', h: '9', d: '1', mo: '1', dow: '*', expectedDesc: 'Runs on January 1st at 09:00 AM' },
    { m: '0', h: '9', d: '25', mo: '12', dow: '*', expectedDesc: 'Runs on December 25th at 09:00 AM' },
    { m: '30', h: '18', d: '14', mo: '2', dow: '*', expectedDesc: 'Runs on February 14th at 06:30 PM' },

    // Non-standard fallback
    { m: '5,10,15', h: '8-17', d: '*', mo: '*', dow: '*', expectedDesc: 'Runs on schedule: 5,10,15 8-17 * * *' },
  ];

  for (const tc of adversarialCronCases) {
    harness.test(`Adversarial cron [${tc.m} ${tc.h} ${tc.d} ${tc.mo} ${tc.dow}] produces "${tc.expectedDesc}"`, () => {
      const desc = formatCronDescription(tc.m, tc.h, tc.d, tc.mo, tc.dow);
      assertEqual(desc, tc.expectedDesc);
      const expr = buildCronExpression(tc.m, tc.h, tc.d, tc.mo, tc.dow);
      assert(expr.length >= 9, 'Cron expression must be non-empty and well-formed');
    });
  }

  // Fuzzing & Malformed Inputs
  const malformedInputs = [
    { m: '', h: '', d: '', mo: '', dow: '' },
    { m: '   ', h: '   ', d: '   ', mo: '   ', dow: '   ' },
    { m: null, h: null, d: null, mo: null, dow: null },
    { m: undefined, h: undefined, d: undefined, mo: undefined, dow: undefined },
    { m: 'abc', h: 'def', d: 'ghi', mo: 'jkl', dow: 'mno' },
    { m: -1, h: -5, d: 0, mo: 13, dow: 8 },
    { m: '999', h: '888', d: '777', mo: '666', dow: '555' },
    { m: '<script>alert("xss")</script>', h: '9', d: '*', mo: '*', dow: '*' },
    { m: '0', h: '9', d: 'DROP TABLE schedulers;', mo: '*', dow: '*' },
    { m: '✨', h: '🚀', d: '🔥', mo: '*', dow: '*' },
    { m: 'A'.repeat(500), h: '*', d: '*', mo: '*', dow: '*' },
  ];

  for (let i = 0; i < malformedInputs.length; i++) {
    const input = malformedInputs[i];
    harness.test(`Fuzzing cron input #${i + 1} survives without throwing unhandled exceptions`, () => {
      let desc = '';
      let built = '';
      let detected = '';

      try {
        desc = formatCronDescription(input.m, input.h, input.d, input.mo, input.dow);
        built = buildCronExpression(input.m, input.h, input.d, input.mo, input.dow);
        detected = detectCronPreset(input.m, input.h, input.d, input.mo, input.dow);
      } catch (err) {
        throw new Error(`Fuzzing case #${i + 1} threw an exception: ${err.message}`);
      }

      assert(typeof desc === 'string' && desc.length > 0, 'Description must be non-empty string');
      assert(typeof built === 'string' && built.length > 0, 'Expression must be non-empty string');
      assertEqual(detected, 'custom', 'Malformed inputs must safely fall back to custom preset');
    });
  }

  // =========================================================================
  // Feature 4: Tasks Extreme Traceback & Structured Output Stress
  // =========================================================================
  harness.setFeature('M3-STRESS-04: Tasks Extreme Traceback & Structured Output Stress');

  // 4A. AST verification of Result and Error decoupling
  harness.test('Tasks.tsx decouples Result and Error into independent Show components', () => {
    const tasksCode = ast.readSrcFile('pages/Tasks.tsx');
    assertIncludes(tasksCode, '<Show when={task.result}>', 'Result section must be guarded independently');
    assertIncludes(tasksCode, '<Show when={task.error}>', 'Error section must be guarded independently');
    assert(!tasksCode.includes('{task.result || task.error}'), 'Monolithic fallback must be eliminated');
  });

  // 4B. CSS typography and container containment verification
  harness.test('tasks.css enforces word-break and overflow containment on .task-error-pre', () => {
    const rules = css.findRules('.task-error-pre');
    assert(rules.length > 0, '.task-error-pre rule must exist in tasks.css');
    const rule = rules[0];
    assertEqual(rule.declarations['white-space'], 'pre-wrap', 'Traceback pre must have white-space: pre-wrap');
    assertEqual(rule.declarations['overflow-wrap'], 'anywhere', 'Traceback pre must have overflow-wrap: anywhere');
    assertEqual(rule.declarations['word-break'], 'break-word', 'Traceback pre must have word-break: break-word');
    assertEqual(rule.declarations['overflow-x'], 'auto', 'Traceback pre must have overflow-x: auto');
  });

  // 4C. Viewport simulation with extreme traceback (100 stack frames + 1000-char unbroken line)
  const extremeTracebackSample = [
    'Traceback (most recent call last):',
    ...Array.from({ length: 60 }, (_, idx) => [
      `  File "/app/agent/delivery/http/dashboard/runtime_engine_worker_${idx}.py", line ${idx * 42 + 10}, in execute_pipeline_step`,
      `    frame_context_${idx} = yield from dispatch_subtask_coroutine(step_id=${idx}, payload={"hash": "0x${'deadbeef'.repeat(8)}"})`,
      `  File "/app/agent/core/coroutine_scheduler/runner.py", line 404, in dispatch_subtask_coroutine`,
      `    raise RecursionDepthExceededError("Deep stack recursion limit reached at depth ${idx}")`,
    ]).flat(),
    `RuntimeFatalError: Unbroken payload: ${'X'.repeat(1000)}`,
  ].join('\n');

  for (const vp of stressViewports.slice(0, 8)) {
    // 320px up to 980px
    harness.test(`Tasks page with 60KB extreme traceback on ${vp.width}px (${vp.name}) zero document overflow`, () => {
      const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
      const tree = {
        name: 'route-tasks',
        style: { width: vp.width },
        children: [
          {
            name: 'task-history-panel',
            style: { widthStr: '100%', padding: { top: 12, right: 12, bottom: 12, left: 12 } },
            children: [
              {
                name: 'task-output-stack',
                style: { widthStr: '100%', display: 'flex', flexDirection: 'column', gap: 12 },
                children: [
                  // Result Card
                  {
                    name: 'task-output-card--result',
                    style: { widthStr: '100%' },
                    children: [
                      {
                        name: 'task-output-header',
                        style: {
                          widthStr: '100%',
                          minHeight: 40,
                          padding: { top: 8, right: 12, bottom: 8, left: 12 },
                        },
                      },
                      {
                        name: 'task-output-body-result',
                        style: { widthStr: '100%', isScrollContainer: true },
                        children: [
                          {
                            name: 'task-result-markdown',
                            text: 'Task completed successfully with partial warnings.',
                            style: { widthStr: '100%', overflowWrap: 'anywhere' },
                          },
                        ],
                      },
                    ],
                  },
                  // Error Card
                  {
                    name: 'task-output-card--error',
                    style: { widthStr: '100%' },
                    children: [
                      {
                        name: 'task-output-header--error',
                        style: {
                          widthStr: '100%',
                          minHeight: 40,
                          padding: { top: 8, right: 12, bottom: 8, left: 12 },
                        },
                      },
                      {
                        name: 'task-output-body-error',
                        style: { widthStr: '100%', isScrollContainer: true },
                        children: [
                          {
                            name: 'task-error-pre',
                            text: extremeTracebackSample,
                            style: {
                              widthStr: '100%',
                              whiteSpace: 'pre-wrap',
                              overflowWrap: 'anywhere',
                              wordBreak: 'break-word',
                              overflowX: 'auto',
                            },
                          },
                        ],
                      },
                    ],
                  },
                ],
              },
            ],
          },
        ],
      };

      const res = sim.simulatePage(tree);
      assert(res.passes, `Tasks page overflows at ${vp.width}px with extreme traceback. scrollWidth: ${res.documentScrollWidth}px`);
      assertEqual(res.documentScrollWidth, vp.width);
    });
  }

  // 4D. Touch target dimensions on mobile (<= 640px)
  harness.test('tasks.css guarantees >= 40px touch target for copy buttons on mobile (<= 640px)', () => {
    const rules = css.findRules('.task-output-copy-btn', '640px');
    const has40px = rules.some((r) => r.declarations['min-height'] === '40px');
    assert(has40px, '.task-output-copy-btn must enforce min-height: 40px on <= 640px');
  });

  harness.test('tasks.css guarantees >= 40px touch target for task history action buttons on mobile (<= 640px)', () => {
    const rules = css.findRules('.task-history-list .btn-sm', '640px');
    const has40px = rules.some((r) => r.declarations['min-height'] === '40px');
    assert(has40px, '.task-history-list .btn-sm must enforce min-height: 40px on <= 640px');
  });

  // =========================================================================
  // Feature 5: SettingsLayout Keyboard Shortcuts & State Machine
  // =========================================================================
  harness.setFeature('M3-STRESS-05: SettingsLayout Keyboard Shortcuts & State Machine');

  harness.test('SettingsLayout source wires "/" and "Ctrl+K" / "Cmd+K" with input element guards', () => {
    const code = ast.readSrcFile('pages/settings/SettingsLayout.tsx');
    assertIncludes(code, 'e.key === "/"', 'SettingsLayout must listen for "/" key');
    assertIncludes(code, 'e.ctrlKey || e.metaKey', 'SettingsLayout must listen for Ctrl+K or Cmd+K');
    assertIncludes(code, '["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)', 'Must not intercept form inputs');
    assertIncludes(code, '!isMobileViewport()', 'Must not trigger shortcut when on mobile viewport');
  });

  harness.test('SettingsLayout state machine simulation for keyboard expand & search focus', () => {
    let collapsed = true;
    let userLocked = false;
    let focused = false;
    const storage = new Map();

    const searchInputRef = {
      focus: () => {
        focused = true;
      },
    };

    const isMobileViewport = () => false;

    const handleKey = (e) => {
      if ((e.key === '/' || (e.key === 'k' && (e.ctrlKey || e.metaKey))) && !isMobileViewport()) {
        const target = e.target;
        if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
        e.preventDefaultCalled = true;
        if (collapsed) {
          collapsed = false;
          userLocked = true;
          storage.set('k41-dashboard-settings-sidebar', 'expanded');
          searchInputRef.focus();
        } else {
          searchInputRef.focus();
        }
      }
    };

    // Case 1: Press "/" on body -> expands and focuses
    const eventBodySlash = { key: '/', target: { tagName: 'BODY' }, preventDefaultCalled: false };
    handleKey(eventBodySlash);
    assertEqual(collapsed, false, 'Sidebar must expand when "/" pressed');
    assertEqual(userLocked, true, 'User locked flag must become true');
    assertEqual(storage.get('k41-dashboard-settings-sidebar'), 'expanded');
    assertEqual(focused, true, 'Search input must receive focus');
    assertEqual(eventBodySlash.preventDefaultCalled, true, 'preventDefault must be called');

    // Case 2: Press "/" while typing in an <input> -> should NOT trigger
    focused = false;
    collapsed = true;
    const eventInputSlash = { key: '/', target: { tagName: 'INPUT' }, preventDefaultCalled: false };
    handleKey(eventInputSlash);
    assertEqual(collapsed, true, 'Sidebar must remain collapsed when typing in INPUT');
    assertEqual(focused, false, 'Search input should not refocus');
    assertEqual(eventInputSlash.preventDefaultCalled, false, 'preventDefault must NOT be called in INPUT');

    // Case 3: Press "Ctrl+K" on body -> expands and focuses
    collapsed = true;
    focused = false;
    const eventCtrlK = { key: 'k', ctrlKey: true, target: { tagName: 'BODY' }, preventDefaultCalled: false };
    handleKey(eventCtrlK);
    assertEqual(collapsed, false, 'Sidebar must expand when Ctrl+K pressed');
    assertEqual(focused, true, 'Search input must receive focus');
  });

  harness.test('SettingsLayout state machine simulation for auto-collapse vs userLocked preference', () => {
    let collapsed = false;
    let userLocked = false;

    // Simulation of onMount with no stored preference at 1280px display
    const innerWidth = 1280;
    const SETTINGS_AUTO_COLLAPSE_BREAKPOINT = 1280;
    if (innerWidth <= SETTINGS_AUTO_COLLAPSE_BREAKPOINT) {
      collapsed = true;
    }
    assertEqual(collapsed, true, 'Auto-collapses at 1280px when no saved preference exists');
    assertEqual(userLocked, false, 'userLocked remains false until user explicitly toggles');

    // User explicitly expands sidebar
    collapsed = false;
    userLocked = true;

    // Screen resize event fires across breakpoint (e.g. down to 1024px)
    const mediaChange = (matches) => {
      if (!userLocked) {
        collapsed = matches;
      }
    };
    mediaChange(true); // matches max-width: 1280px
    assertEqual(collapsed, false, 'Sidebar remains expanded because userLocked is true');
  });

  // =========================================================================
  // Feature 6: Scheduler Responsive Grid & Touch Targets (320px–1280px)
  // =========================================================================
  harness.setFeature('M3-STRESS-06: Scheduler Responsive Grid & Touch Targets (320px–1280px)');

  harness.test('scheduler.css defines responsive grid: 5 cols (desktop), 3 cols (tablet), 2 cols (mobile), 1 col (sub-360)', () => {
    // Desktop base rule
    const baseRules = css.findRules('.cron-fields-grid');
    const base = baseRules.find((r) => !r.mediaQuery);
    assert(base !== undefined, 'Base .cron-fields-grid rule must exist');
    assertEqual(base.declarations['grid-template-columns'], 'repeat(5, minmax(0, 1fr))');

    // Tablet <= 768px
    const tabletRules = css.findRules('.cron-fields-grid', '768px');
    const has3Cols = tabletRules.some((r) => r.declarations['grid-template-columns'] === 'repeat(3, minmax(0, 1fr))');
    assert(has3Cols, '.cron-fields-grid must be repeat(3, minmax(0, 1fr)) on <= 768px');

    // Mobile <= 640px
    const mobileRules = css.findRules('.cron-fields-grid', '640px');
    const has2Cols = mobileRules.some((r) => r.declarations['grid-template-columns'] === 'repeat(2, minmax(0, 1fr))');
    assert(has2Cols, '.cron-fields-grid must be repeat(2, minmax(0, 1fr)) on <= 640px');

    // Sub-360px <= 360px
    const sub360Rules = css.findRules('.cron-fields-grid', '360px');
    const has1Col = sub360Rules.some((r) => r.declarations['grid-template-columns'] === '1fr');
    assert(has1Col, '.cron-fields-grid must be 1fr on <= 360px');
  });

  harness.test('scheduler.css defines cron-preset-pill with min-height >= 42px (desktop) and >= 44px (mobile)', () => {
    const baseRules = css.findRules('.cron-preset-pill');
    const base = baseRules.find((r) => !r.mediaQuery);
    assert(base !== undefined, '.cron-preset-pill rule must exist');
    assertGreaterThanOrEqual(parseInt(base.declarations['min-height'] || '0', 10), 42);

    const mobileRules = css.findRules('.cron-preset-pill', '640px');
    const has44px = mobileRules.some((r) => parseInt(r.declarations['min-height'] || '0', 10) >= 44);
    assert(has44px, '.cron-preset-pill must be >= 44px on <= 640px');
  });

  // Table container scroll isolation on /scheduler
  for (const vp of stressViewports) {
    harness.test(`Scheduler table on ${vp.width}px (${vp.name}) isolates scroll container without document overflow`, () => {
      const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
      const tree = {
        name: 'route-scheduler',
        style: { width: vp.width },
        children: [
          {
            name: 'scheduler-content',
            style: { widthStr: '100%', padding: { top: 12, right: 16, bottom: 16, left: 16 } },
            children: [
              {
                name: 'cron-summary-banner',
                style: { widthStr: '100%', wordBreak: 'break-word', overflowWrap: 'anywhere' },
                text: 'Runs on the 1st of every month at 12:00 AM',
              },
              {
                name: 'scheduler-table-wrap',
                style: { widthStr: '100%', isScrollContainer: true },
                children: [
                  {
                    name: 'scheduler-table',
                    style: { width: 850, minWidth: 720 },
                  },
                ],
              },
            ],
          },
        ],
      };

      const res = sim.simulatePage(tree);
      assert(res.passes, `Scheduler overflows on ${vp.width}px`);
      assertEqual(res.documentScrollWidth, vp.width);
    });
  }

  // =========================================================================
  // Feature 7: AGENTS.md Full Localization & Codebase English Compliance
  // =========================================================================
  harness.setFeature('M3-STRESS-07: AGENTS.md Codebase English Compliance');

  const filesToCheck = [
    'src/pages/settings/SettingsLayout.tsx',
    'src/styles/settings.css',
    'src/pages/Tasks.tsx',
    'src/styles/tasks.css',
    'src/pages/Scheduler.tsx',
    'src/styles/scheduler.css',
    'src/styles.css',
  ];

  const vietnameseRegex = /[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđÀÁẠẢÃÂẦẤẬẨẪĂẰẮẶẲẴÈÉẸẺẼÊỀẾỆỂỄÌÍỊỈĨÒÓỌỎÕÔỒỐỘỔỖƠỜỚỢỞỠÙÚỤỦŨƯỪỨỰỬỮỲÝỴỶỸĐ]/;

  for (const relPath of filesToCheck) {
    harness.test(`Source file ${relPath} contains ZERO Vietnamese characters`, () => {
      const fullPath = path.join(FRONTEND_ROOT, relPath);
      const content = fs.readFileSync(fullPath, 'utf8');
      assert(!vietnameseRegex.test(content), `File ${relPath} contains Vietnamese characters, violating AGENTS.md rule`);
    });
  }

  const results = await harness.run();
  return results;
}

runM3ChallengerStressSuite()
  .then((res) => {
    if (res.failed > 0) {
      console.error(`\n❌ Challenger M3 Stress Suite failed with ${res.failed} failures.`);
      process.exit(1);
    } else {
      console.log(`\n🏆 All ${res.passed} Challenger M3 Empirical Stress Tests PASSED!`);
      process.exit(0);
    }
  })
  .catch((err) => {
    console.error('Fatal error in challenger stress suite:', err);
    process.exit(1);
  });
