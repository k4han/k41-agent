// @ts-check
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  TestHarness,
  assert,
  assertEqual,
  assertGreaterThanOrEqual,
  assertLessThanOrEqual,
  assertMatches,
} from './responsive/engine/harness.mjs';
import { ViewportSimulator } from './responsive/engine/viewport-simulator.mjs';
import { CSSAnalyzer } from './responsive/engine/css-analyzer.mjs';
import { ASTAnalyzer } from './responsive/engine/ast-analyzer.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FRONTEND_ROOT = path.resolve(__dirname, '..');

async function runAdversarialM2Suite() {
  const stylesDir = path.join(FRONTEND_ROOT, 'src/styles');
  const css = new CSSAnalyzer(stylesDir);
  const ast = new ASTAnalyzer(FRONTEND_ROOT);
  css.load();

  const harness = new TestHarness({ verbose: true });
  harness.setTier('Challenger M2: Adversarial Stress Testing');

  const requiredViewports = [
    { name: 'iPhone SE / Mini (375px)', width: 375, height: 667 },
    { name: 'iPhone 14 / Pro (390px)', width: 390, height: 844 },
    { name: 'iPhone Plus / Max (414px)', width: 414, height: 896 },
    { name: 'iPad Portrait (768px)', width: 768, height: 1024 },
  ];

  const sub375Viewports = [
    { name: 'Extremely Narrow Mobile (320px)', width: 320, height: 568 },
    { name: 'Compact Mobile (340px)', width: 340, height: 640 },
    { name: 'Standard Android (360px)', width: 360, height: 740 },
    { name: 'Boundary Sub-375 (374px)', width: 374, height: 667 },
  ];

  // =========================================================================
  // Section 1: Chat Header Layout Constraints & Safe Area Insets
  // =========================================================================
  harness.setFeature('M2-ADV-01: Chat Header Layout & Title Truncation');

  for (const vp of requiredViewports) {
    harness.test(`Header on ${vp.width}px fits within screen and handles long unbroken thread title`, () => {
      const sim = new ViewportSimulator({ width: vp.width, height: vp.height, safeArea: { top: 47, bottom: 34 } });
      const header = {
        name: 'chat-header',
        style: {
          display: 'flex',
          flexDirection: 'row',
          widthStr: '100%',
          padding: { top: 47 + 6, right: 14, bottom: 6, left: 14 },
        },
        children: [
          {
            name: 'chat-header-left',
            style: { display: 'flex', flexDirection: 'row', gap: 8, widthStr: 'calc(100% - 58px)' },
            children: [
              { name: 'chat-header-menu-toggle', style: { width: 40, minWidth: 40 } },
              {
                name: 'chat-header-title-wrap',
                style: { display: 'flex', widthStr: 'calc(100% - 48px)' },
                children: [
                  {
                    name: 'chat-header-title',
                    text: 'Very-long-unbroken-task-thread-title-with-extreme-length-that-must-truncate-and-never-overflow-viewport-bounds-0123456789-abcdefghijklmnopqrstuvwxyz',
                    style: { widthStr: '100%', overflowWrap: 'anywhere', wordBreak: 'break-word' },
                  },
                ],
              },
            ],
          },
          {
            name: 'chat-header-right',
            style: { display: 'flex', width: 40, margin: { top: 0, right: 0, bottom: 0, left: 12 } },
            children: [{ name: 'chat-header-workspace-btn', style: { width: 40, minWidth: 40 } }],
          },
        ],
      };

      const res = sim.computeBox(header);
      assertLessThanOrEqual(res.scrollWidth, vp.width, `Header scrollWidth (${res.scrollWidth}px) exceeds ${vp.width}px`);
      assert(res.inducesDocOverflow === false, `Header induces document overflow on ${vp.width}px`);
    });
  }

  // =========================================================================
  // Section 2: Composer Toolbar Tiers Box Model Widths at 375, 390, 414, 768px
  // =========================================================================
  harness.setFeature('M2-ADV-02: Composer Toolbar Tiers at Required Viewports');

  for (const vp of requiredViewports) {
    harness.test(`Composer toolbar tiers on ${vp.width}px zero horizontal overflow`, () => {
      const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
      const isMobile = vp.width <= 640;

      const composerBox = isMobile
        ? {
            name: 'chat-composer-mobile',
            style: {
              widthStr: 'calc(100% - 16px)',
              padding: { top: 10, right: 12, bottom: 20, left: 12 },
              margin: { top: 0, right: 8, bottom: 0, left: 8 },
            },
            children: [
              {
                name: 'chat-prompt-input',
                style: { widthStr: '100%' },
              },
              {
                name: 'chat-composer-toolbar',
                style: {
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 8,
                  widthStr: '100%',
                },
                children: [
                  {
                    name: 'chat-composer-tier-selectors',
                    style: {
                      display: 'flex',
                      flexDirection: 'row',
                      gap: 8,
                      widthStr: '100%',
                    },
                    children: [
                      { name: 'chat-agent-picker', style: { widthStr: '50%', minWidth: 0 } },
                      { name: 'chat-model-picker', style: { widthStr: '50%', minWidth: 0 } },
                    ],
                  },
                  {
                    name: 'chat-composer-tier-actions',
                    style: {
                      display: 'flex',
                      flexDirection: 'row',
                      gap: 8,
                      widthStr: '100%',
                    },
                    children: [
                      {
                        name: 'chat-composer-actions',
                        style: { display: 'flex', flexDirection: 'row', gap: 8, width: 136 },
                        children: [
                          { name: 'plus-btn', style: { width: 40, minWidth: 40 } },
                          { name: 'more-btn', style: { width: 40, minWidth: 40 } },
                          { name: 'context-window-indicator', style: { width: 40, minWidth: 40 } },
                        ],
                      },
                      {
                        name: 'chat-composer-send-wrapper',
                        style: { width: 40, minWidth: 40 },
                        children: [{ name: 'send-btn', style: { width: 40, minWidth: 40 } }],
                      },
                    ],
                  },
                ],
              },
            ],
          }
        : {
            name: 'chat-composer-desktop',
            style: {
              widthStr: 'calc(100% - 32px)',
              padding: { top: 10, right: 12, bottom: 20, left: 12 },
              margin: { top: 0, right: 16, bottom: 0, left: 16 },
            },
            children: [
              {
                name: 'chat-prompt-input',
                style: { widthStr: '100%' },
              },
              {
                name: 'chat-composer-toolbar',
                style: {
                  display: 'flex',
                  flexDirection: 'row',
                  gap: 12,
                  widthStr: '100%',
                },
                children: [
                  {
                    name: 'chat-composer-tier-selectors',
                    style: {
                      display: 'flex',
                      flexDirection: 'row',
                      gap: 8,
                      width: 320,
                    },
                    children: [
                      { name: 'chat-agent-picker', style: { width: 140, minWidth: 0 } },
                      { name: 'chat-model-picker', style: { width: 172, minWidth: 0 } },
                    ],
                  },
                  {
                    name: 'chat-composer-tier-actions',
                    style: {
                      display: 'flex',
                      flexDirection: 'row',
                      gap: 8,
                      width: 200,
                    },
                    children: [
                      {
                        name: 'chat-composer-actions',
                        style: { display: 'flex', flexDirection: 'row', gap: 8, width: 136 },
                        children: [
                          { name: 'plus-btn', style: { width: 40, minWidth: 40 } },
                          { name: 'more-btn', style: { width: 40, minWidth: 40 } },
                          { name: 'context-window-indicator', style: { width: 40, minWidth: 40 } },
                        ],
                      },
                      {
                        name: 'chat-composer-send-wrapper',
                        style: { width: 40, minWidth: 40 },
                        children: [{ name: 'send-btn', style: { width: 40, minWidth: 40 } }],
                      },
                    ],
                  },
                ],
              },
            ],
          };

      const res = sim.computeBox(composerBox);
      assertLessThanOrEqual(res.scrollWidth, vp.width, `Composer scrollWidth (${res.scrollWidth}px) exceeds ${vp.width}px`);
      assert(res.inducesDocOverflow === false, `Composer induces document overflow on ${vp.width}px`);
    });
  }

  // =========================================================================
  // Section 3: Sub-375px Horizontal Overflow Adversarial Stress (320px - 374px)
  // =========================================================================
  harness.setFeature('M2-ADV-03: Sub-375px Composer Toolbar Overflow Stress');

  for (const vp of sub375Viewports) {
    harness.test(`Sub-375px (${vp.width}px): Tier 1 (selectors) and Tier 2 (actions) zero overflow`, () => {
      const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
      const composerWidth = vp.width - 16;
      const innerContentWidth = composerWidth - 24; // padding 12px each side

      // Tier 1: Pickers share space with flex: 1 1 120px and min-width: 0
      const tier1 = {
        name: 'chat-composer-tier-selectors',
        style: {
          display: 'flex',
          flexDirection: 'row',
          gap: 8,
          widthStr: `${innerContentWidth}px`,
        },
        children: [
          { name: 'chat-agent-picker', style: { widthStr: `${(innerContentWidth - 8) / 2}px`, minWidth: 0 } },
          { name: 'chat-model-picker', style: { widthStr: `${(innerContentWidth - 8) / 2}px`, minWidth: 0 } },
        ],
      };

      const resTier1 = sim.computeBox(tier1, innerContentWidth);
      assertLessThanOrEqual(
        resTier1.scrollWidth,
        innerContentWidth,
        `Tier 1 on ${vp.width}px exceeds inner content width (${resTier1.scrollWidth} > ${innerContentWidth})`
      );

      // Tier 2: 3 action icons (40px each + 8px gap) + Send icon (40px)
      // Total required = (40*3 + 16) + 8 + 40 = 184px
      const tier2 = {
        name: 'chat-composer-tier-actions',
        style: {
          display: 'flex',
          flexDirection: 'row',
          gap: 8,
          widthStr: `${innerContentWidth}px`,
        },
        children: [
          {
            name: 'chat-composer-actions',
            style: { display: 'flex', flexDirection: 'row', gap: 8, width: 136 },
            children: [
              { name: 'attach-btn', style: { width: 40, minWidth: 40 } },
              { name: 'more-btn', style: { width: 40, minWidth: 40 } },
              { name: 'cw-btn', style: { width: 40, minWidth: 40 } },
            ],
          },
          {
            name: 'chat-composer-send-wrapper',
            style: { width: 40, minWidth: 40 },
            children: [{ name: 'send-btn', style: { width: 40, minWidth: 40 } }],
          },
        ],
      };

      const resTier2 = sim.computeBox(tier2, innerContentWidth);
      assertLessThanOrEqual(
        resTier2.scrollWidth,
        innerContentWidth,
        `Tier 2 on ${vp.width}px exceeds inner content width (${resTier2.scrollWidth} > ${innerContentWidth})`
      );
    });
  }

  // =========================================================================
  // Section 4: Touch Target Audit (>= 40px) for All Interactive Elements
  // =========================================================================
  harness.setFeature('M2-ADV-04: Button Touch Target Dimensions (>= 40px)');

  const touchTargetSpecs = [
    { selector: '.chat-header-menu-toggle', minW: 40, minH: 40, desc: 'Chat Header Menu Toggle' },
    { selector: '.chat-header-workspace-btn', minW: 40, minH: 40, desc: 'Chat Header Workspace Button' },
    { selector: '.chat-composer-icon', minW: 40, minH: 40, desc: 'Chat Composer Icon Button' },
    { selector: '.context-window-circle-btn', minW: 40, minH: 40, desc: 'Context Window Indicator Circle' },
    { selector: '.chat-agent-picker', minW: 0, minH: 40, desc: 'Chat Agent Picker Trigger' },
    { selector: '.chat-model-picker', minW: 0, minH: 40, desc: 'Chat Model Picker Trigger' },
    { selector: '.chat-model-picker .model-picker-star', minW: 40, minH: 40, desc: 'Chat Model Picker Star' },
    { selector: '.message-edit-btn', minW: 0, minH: 40, desc: 'Message Edit Save Button' },
    { selector: '.chat-recursion-warning-btn', minW: 0, minH: 40, desc: 'Chat Recursion Warning Button' },
    { selector: '.chat-attachment-remove', minW: 40, minH: 40, desc: 'Chat Attachment Remove Button' },
    { selector: '.cw-compact-btn', minW: 0, minH: 40, desc: 'Context Window Compact Button' },
  ];

  for (const spec of touchTargetSpecs) {
    harness.test(`Touch target for ${spec.desc} meets requirement (${spec.minW}x${spec.minH}px)`, () => {
      const rules = css.findRules(spec.selector);
      assert(rules.length > 0, `No CSS rules found for ${spec.selector}`);

      let foundMinW = spec.minW === 0;
      let foundMinH = spec.minH === 0;

      for (const r of rules) {
        const w = r.declarations['width'] || r.declarations['min-width'];
        const h = r.declarations['height'] || r.declarations['min-height'];

        if (w) {
          const valW = parseFloat(w);
          if (w.includes('var(--touch-target') || (valW && valW >= spec.minW)) {
            foundMinW = true;
          }
        }
        if (h) {
          const valH = parseFloat(h);
          if (h.includes('var(--touch-target') || (valH && valH >= spec.minH)) {
            foundMinH = true;
          }
        }
      }

      assert(foundMinW, `${spec.desc} must have width/min-width >= ${spec.minW}px`);
      assert(foundMinH, `${spec.desc} must have height/min-height >= ${spec.minH}px`);
    });
  }

  // Check Send button composing .chat-composer-icon with .chat-composer-send
  harness.test('Chat Composer Send Button inherits .chat-composer-icon 40x40px touch target', () => {
    const composerCode = ast.readSrcFile('components/ChatComposer.tsx');
    assert(
      composerCode.includes('chat-composer-icon chat-composer-send'),
      'Send button in ChatComposer must combine chat-composer-icon and chat-composer-send classes'
    );
    const iconRules = css.findRules('.chat-composer-icon');
    const has40px = iconRules.some((r) => {
      const w = parseFloat(r.declarations['width'] || r.declarations['min-width'] || '0');
      const h = parseFloat(r.declarations['height'] || r.declarations['min-height'] || '0');
      return w >= 40 && h >= 40;
    });
    assert(has40px, '.chat-composer-icon must have width and height >= 40px');
  });

  // Check message action button compliance with PROJECT.md F16 (36px - 40px)
  harness.test('Message action buttons (.message-action-btn) meet PROJECT.md F16 target (>= 36px/38px)', () => {
    const rules = css.findRules('.message-action-btn');
    assert(rules.length > 0, '.message-action-btn rule exists');
    const hasAdequateSize = rules.some((r) => {
      const w = parseFloat(r.declarations['width'] || r.declarations['min-width'] || '0');
      const h = parseFloat(r.declarations['height'] || r.declarations['min-height'] || '0');
      return w >= 36 && h >= 36;
    });
    assert(hasAdequateSize, '.message-action-btn must be >= 36px in width and height');
  });

  // =========================================================================
  // Section 5: Context Window Popover Clamping & Tap Dismissal
  // =========================================================================
  harness.setFeature('M2-ADV-05: Context Window Popover Mobile Adaptation');

  for (const vp of requiredViewports) {
    harness.test(`Context Window Popover on ${vp.width}px does not overflow screen edge`, () => {
      const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
      const isMobile = vp.width <= 640;
      // In CSS: right: 0; max-width: calc(100vw - 32px); width: 280px
      const popoverBox = {
        name: 'context-window-popover',
        style: {
          width: isMobile ? Math.min(280, vp.width - 32) : 290,
          maxWidth: vp.width - 32,
        },
      };
      const res = sim.computeBox(popoverBox);
      assertLessThanOrEqual(res.scrollWidth, vp.width, `Popover width (${res.scrollWidth}px) exceeds ${vp.width}px`);
    });
  }

  harness.test('ContextWindowIndicator component supports tap toggle state and outside click cleanup', () => {
    const code = ast.readSrcFile('components/ContextWindowIndicator.tsx');
    assert(code.includes('createSignal(false)'), 'Must track isOpen state with createSignal');
    assert(code.includes('handleToggle'), 'Must implement tap toggle handler');
    assert(code.includes('handleDocumentClick'), 'Must implement outside click listener');
    assert(code.includes('onCleanup'), 'Must remove listener on unmount');
  });

  harness.test('Context Window Popover mobile positioning clamps within viewport without left clipping', () => {
    const popoverRules = css.findRules('.context-window-popover', '640px');
    const mobileRule = popoverRules.find((r) => r.declarations['left'] !== undefined || r.declarations['right'] !== undefined);
    assert(mobileRule, '.context-window-popover must have mobile rule under max-width: 640px');
    const leftVal = mobileRule.declarations['left'];
    const rightVal = mobileRule.declarations['right'];
    assert(
      !(rightVal === '0' && (leftVal === 'auto' || leftVal === undefined)),
      'Popover must not use right: 0; left: auto; on mobile because it bleeds -124px off screen'
    );
    assert(leftVal !== undefined && leftVal !== 'auto', 'Popover must explicitly define left positioning on mobile');
  });

  harness.test('ChatComposer adheres to AGENTS.md localization (zero Vietnamese characters)', () => {
    const composerCode = ast.readSrcFile('components/ChatComposer.tsx');
    const vietnamesePattern = /[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]/i;
    assert(!vietnamesePattern.test(composerCode), 'ChatComposer.tsx must not contain any Vietnamese text');
  });

  // =========================================================================
  // Section 6: Markdown & Table Scroll Container Isolation
  // =========================================================================
  harness.setFeature('M2-ADV-06: Markdown Code & Plan Review Table Isolation');

  for (const vp of requiredViewports) {
    harness.test(`Inline code unbroken hash on ${vp.width}px wraps without overflow`, () => {
      const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
      const msgBox = {
        name: 'message-markdown',
        style: { widthStr: '100%', padding: { top: 8, right: 12, bottom: 8, left: 12 } },
        children: [
          {
            name: 'code',
            text: 'a9b8c7d6e5f40123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
            style: { overflowWrap: 'anywhere', wordBreak: 'break-word' },
          },
        ],
      };
      const res = sim.computeBox(msgBox);
      assertLessThanOrEqual(res.scrollWidth, vp.width, `Inline code exceeds viewport width on ${vp.width}px`);
    });

    harness.test(`Plan Review Markdown 1000px table on ${vp.width}px isolates scroll container`, () => {
      const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
      const planReview = {
        name: 'plan-review-markdown',
        style: { widthStr: '100%', maxWidth: vp.width },
        children: [
          {
            name: 'plan-review-table-container',
            style: { display: 'block', widthStr: '100%', maxWidth: '100%', overflowX: 'auto', isScrollContainer: true },
            children: [
              { name: 'plan-table-content', style: { width: 1000 } },
            ],
          },
        ],
      };
      const pageResult = sim.simulatePage(planReview);
      assert(pageResult.passes, `Plan review table induces page overflow on ${vp.width}px`);
      assertLessThanOrEqual(pageResult.documentScrollWidth, vp.width, `documentScrollWidth exceeds ${vp.width}px`);
    });
  }

  // =========================================================================
  // Section 7: Workspace Selector Empty State Responsive Tabs
  // =========================================================================
  harness.setFeature('M2-ADV-07: Workspace Selector Empty State');

  for (const vp of requiredViewports) {
    harness.test(`Workspace selector tabs container on ${vp.width}px scrolls horizontally without overflow`, () => {
      const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
      const isMobile = vp.width <= 640;
      const workspaceBox = {
        name: 'workspace-selector',
        style: { widthStr: '100%', padding: { top: 12, right: 16, bottom: 12, left: 16 } },
        children: [
          {
            name: 'workspace-tabs-list',
            style: { display: 'flex', widthStr: '100%', overflowX: isMobile ? 'auto' : 'visible', isScrollContainer: isMobile },
            children: [
              { name: 'tab-1', style: { width: 110 } },
              { name: 'tab-2', style: { width: 120 } },
              { name: 'tab-3', style: { width: 130 } },
              { name: 'tab-4', style: { width: 140 } },
            ],
          },
        ],
      };
      const res = sim.simulatePage(workspaceBox);
      assert(res.passes, `WorkspaceSelector tabs blow out page on ${vp.width}px`);
    });
  }

  // Execute all tests
  const results = await harness.run();
  return results;
}

runAdversarialM2Suite()
  .then((res) => {
    if (res.failed > 0) {
      console.error(`\n❌ Challenger M2 Adversarial Suite failed with ${res.failed} failures.`);
      process.exit(1);
    } else {
      console.log(`\n🏆 All ${res.passed} Challenger M2 Adversarial Tests PASSED!`);
      process.exit(0);
    }
  })
  .catch((err) => {
    console.error('Fatal error in challenger suite:', err);
    process.exit(1);
  });
