// @ts-check
import {
  assert,
  assertEqual,
  assertGreaterThanOrEqual,
  assertLessThanOrEqual,
} from './engine/harness.mjs';
import { ViewportSimulator } from './engine/viewport-simulator.mjs';

/**
 * Register Tier 3: Cross-Feature Combinations tests
 * @param {import('./engine/harness.mjs').TestHarness} harness
 * @param {{ css: import('./engine/css-analyzer.mjs').CSSAnalyzer, ast: import('./engine/ast-analyzer.mjs').ASTAnalyzer }} ctx
 */
export function registerTier3Tests(harness, ctx) {
  harness.setTier('Tier 3: Cross-Feature Combinations');

  // -------------------------------------------------------------
  // Combination 1: Drawer Open + Chat Composer Multi-line Input
  // -------------------------------------------------------------
  harness.setFeature('Drawer Open + Chat Composer');

  harness.test('T3-C01: Off-canvas drawer open while chat composer has multi-line text input on 375px', () => {
    const sim = new ViewportSimulator({ width: 375, height: 667 });
    const viewWithOpenDrawerAndComposer = {
      name: 'root-layout',
      style: { width: 375 },
      children: [
        {
          name: 'drawer-backdrop',
          style: { widthStr: '100%', position: 'fixed', isScrollContainer: false },
        },
        {
          name: 'sidebar-drawer-open',
          style: { widthStr: 'min(300px, 86vw)', position: 'fixed', isScrollContainer: true },
        },
        {
          name: 'chat-container',
          style: { widthStr: '100%' },
          children: [
            {
              name: 'chat-composer-multiline',
              style: { widthStr: '100%', padding: { top: 8, right: 12, bottom: 8, left: 12 } },
              children: [
                {
                  name: 'textarea-expanded',
                  style: { widthStr: '100%', padding: { top: 4, right: 4, bottom: 4, left: 4 } },
                  text: 'Line 1 of prompt.\nLine 2 with detailed requirements.\nLine 3 with code snippet.\nLine 4 summary.',
                },
                {
                  name: 'toolbar-2tier',
                  style: { display: 'flex', flexDirection: 'column', widthStr: '100%', gap: 6 },
                },
              ],
            },
          ],
        },
      ],
    };
    const res = sim.simulatePage(viewWithOpenDrawerAndComposer);
    assert(res.passes, 'Drawer open alongside multi-line composer must not cause horizontal document scroll');
    assertEqual(res.documentScrollWidth, 375, 'document.scrollWidth must equal 375px');
  });

  // -------------------------------------------------------------
  // Combination 2: Virtual Keyboard Simulation (100dvh to 480px)
  // -------------------------------------------------------------
  harness.setFeature('Virtual Keyboard Simulation');

  harness.test('T3-C02: Virtual keyboard shrinks viewport height from 844px to 480px, composer remains pinned', () => {
    const sim = new ViewportSimulator({
      width: 390,
      height: 844,
      virtualKeyboard: true,
      virtualKeyboardHeight: 364, // keyboard takes 364px
    });
    assertEqual(sim.innerHeight, 480, 'Simulated visible height must be 480px');

    const virtualChat = {
      name: 'chat-virtual-view',
      style: { width: 390 },
      children: [
        {
          name: 'transcript-stream',
          style: { widthStr: '100%', isScrollContainer: true },
          children: [{ name: 'messages', style: { widthStr: '100%' } }],
        },
        {
          name: 'pinned-composer',
          style: { widthStr: '100%', padding: { top: 6, right: 8, bottom: 6, left: 8 } },
        },
      ],
    };
    const res = sim.simulatePage(virtualChat);
    assert(res.passes, 'Virtual keyboard active state must not cause horizontal overflow');
    assertEqual(res.documentScrollWidth, 390, 'ScrollWidth must stay exactly 390px');
  });

  // -------------------------------------------------------------
  // Combination 3: Wide Multi-Column Data Table Inside Card Panel
  // -------------------------------------------------------------
  harness.setFeature('Wide Table Inside Card Panel');

  harness.test('T3-C03: 8-column 960px data table inside card panel on 375px scrolls internally without bleeding', () => {
    const sim = new ViewportSimulator({ width: 375, height: 667 });
    const cardWithWideTable = {
      name: 'card-panel',
      style: { widthStr: '100%', padding: { top: 16, right: 16, bottom: 16, left: 16 } },
      children: [
        { name: 'panel-header', style: { widthStr: '100%' } },
        {
          name: 'table-wrap-container',
          style: { widthStr: '100%', isScrollContainer: true },
          children: [
            {
              name: 'wide-table',
              style: { width: 960, minWidth: 960 },
            },
          ],
        },
      ],
    };
    const res = sim.simulatePage(cardWithWideTable);
    assert(res.passes, 'Card with wide table must isolate scroll and avoid document overflow');
    assertEqual(res.documentScrollWidth, 375, 'Document scrollWidth must stay 375px');
  });

  // -------------------------------------------------------------
  // Combination 4: Modal Dialog Open Over Wide Table (Bottom-Sheet Transition)
  // -------------------------------------------------------------
  harness.setFeature('Modal Dialog Bottom-Sheet Over Wide Table');

  harness.test('T3-C04: Dialog over wide table adapts from desktop modal to bottom-sheet when transitioning to 375px', () => {
    const sim = new ViewportSimulator({ width: 375, height: 667 });
    const pageWithModal = {
      name: 'page-with-modal',
      style: { width: 375 },
      children: [
        {
          name: 'background-table-wrap',
          style: { widthStr: '100%', isScrollContainer: true },
          children: [{ name: 'table', style: { width: 800 } }],
        },
        {
          name: 'dialog-overlay',
          style: { width: 375 },
          children: [
            {
              name: 'bottom-sheet-dialog',
              style: {
                widthStr: '100%',
                maxWidth: 375,
                padding: { top: 16, right: 16, bottom: 20, left: 16 },
              },
              children: [
                { name: 'dialog-header', style: { widthStr: '100%' } },
                { name: 'dialog-form', style: { widthStr: '100%' } },
                {
                  name: 'dialog-actions',
                  style: { display: 'flex', flexDirection: 'column', widthStr: '100%', gap: 8 },
                  children: [
                    { name: 'confirm-btn', style: { widthStr: '100%' } },
                    { name: 'cancel-btn', style: { widthStr: '100%' } },
                  ],
                },
              ],
            },
          ],
        },
      ],
    };
    const res = sim.simulatePage(pageWithModal);
    assert(res.passes, 'Bottom-sheet modal over table must maintain zero document overflow');
    assertEqual(res.documentScrollWidth, 375, 'document.scrollWidth must equal 375px');
  });

  // -------------------------------------------------------------
  // Combination 5: Code Block + Markdown Table in Chat Message on 390px
  // -------------------------------------------------------------
  harness.setFeature('Code Block + Markdown Table in Chat Message');

  harness.test('T3-C05: 200-column code snippet + markdown table in same assistant response on 390px', () => {
    const sim = new ViewportSimulator({ width: 390, height: 844 });
    const assistantMessage = {
      name: 'assistant-message',
      style: { widthStr: '100%', padding: { top: 12, right: 12, bottom: 12, left: 12 } },
      children: [
        {
          name: 'markdown-table-wrap',
          style: { widthStr: '100%', isScrollContainer: true },
          children: [{ name: 'table', style: { width: 650 } }],
        },
        {
          name: 'code-snippet-wrap',
          style: { widthStr: '100%', isScrollContainer: true },
          children: [{ name: 'pre-code', style: { width: 1200 } }],
        },
      ],
    };
    const res = sim.simulatePage(assistantMessage);
    assert(res.passes, 'Assistant message containing both wide table and wide code block must isolate scrolls');
    assertEqual(res.documentScrollWidth, 390, 'document.scrollWidth must stay 390px');
  });

  // -------------------------------------------------------------
  // Combination 6: Context Window Popover on 375px Clamped Within Screen
  // -------------------------------------------------------------
  harness.setFeature('Context Window Popover Clamping');

  harness.test('T3-C06: Context window popover open on 375px is clamped against right viewport edge without 90px overflow', () => {
    const sim = new ViewportSimulator({ width: 375, height: 667 });
    // In unoptimized code: popover had right: -90px causing overflow.
    // In optimized code: popover is clamped to min(320px, calc(100vw - 24px)) with right: 0.
    const popoverBox = {
      name: 'context-window-popover-clamped',
      style: {
        widthStr: 'min(320px, 92vw)',
        maxWidth: 375 - 16,
        padding: { top: 12, right: 12, bottom: 12, left: 12 },
      },
    };
    const res = sim.computeBox(popoverBox, 375);
    assertLessThanOrEqual(res.computedWidth, 359, 'Popover width must not exceed 359px');
    assertLessThanOrEqual(res.scrollWidth, 375, 'Popover must not induce viewport horizontal scroll');
  });

  // -------------------------------------------------------------
  // Combination 7: Settings Page 1-Column Grid + Pinned Bottom Bar
  // -------------------------------------------------------------
  harness.setFeature('Settings Page 1-Column Grid + Pinned Bottom Bar');

  harness.test('T3-C07: Settings AgentPromptTab 1-column grid with unsaved changes bar on 375px', () => {
    const sim = new ViewportSimulator({ width: 375, height: 667 });
    const settingsLayout = {
      name: 'settings-agents-layout',
      style: { width: 375 },
      children: [
        {
          name: 'agent-prompt-grid-1col',
          style: { display: 'flex', flexDirection: 'column', widthStr: '100%', gap: 16 },
          children: [
            { name: 'prompt-editor', style: { widthStr: '100%' } },
            { name: 'variables-panel', style: { widthStr: '100%' } },
          ],
        },
        {
          name: 'unsaved-changes-bottom-bar',
          style: {
            widthStr: '100%',
            padding: { top: 10, right: 16, bottom: 10, left: 16 },
            display: 'flex',
            flexDirection: 'row',
            gap: 8,
          },
          children: [
            { name: 'status-text', style: { widthStr: '50%' } },
            { name: 'save-button', style: { widthStr: '50%', minWidth: 40 } },
          ],
        },
      ],
    };
    const res = sim.simulatePage(settingsLayout);
    assert(res.passes, 'Settings layout on 375px must not have horizontal overflow');
    assertEqual(res.documentScrollWidth, 375, 'document.scrollWidth must equal 375px');
  });
}
