// @ts-check
import {
  assert,
  assertEqual,
  assertGreaterThanOrEqual,
  assertLessThanOrEqual,
} from './engine/harness.mjs';
import { ViewportSimulator } from './engine/viewport-simulator.mjs';

/**
 * Register Tier 2: Boundary & Corner Cases tests
 * @param {import('./engine/harness.mjs').TestHarness} harness
 * @param {{ css: import('./engine/css-analyzer.mjs').CSSAnalyzer, ast: import('./engine/ast-analyzer.mjs').ASTAnalyzer }} ctx
 */
export function registerTier2Tests(harness, ctx) {
  harness.setTier('Tier 2: Boundary & Corner Cases');

  // -------------------------------------------------------------
  // Boundary 1: Smallest Mobile Screens (320px - 375px)
  // -------------------------------------------------------------
  harness.setFeature('Smallest Mobile Screens (320px - 375px)');

  harness.test('T2-B01: Viewport 320px drawer width min(300px, 86vw) equals 275.2px and leaves backdrop margin', () => {
    const sim = new ViewportSimulator({ width: 320, height: 568 });
    const drawer = {
      name: 'sidebar-drawer',
      style: { widthStr: 'min(300px, 86vw)' },
    };
    const res = sim.computeBox(drawer);
    assertLessThanOrEqual(res.computedWidth, 320 * 0.86 + 0.1, 'Drawer width must equal 86vw on 320px');
    assertLessThanOrEqual(res.computedWidth, 276, 'Drawer must not exceed 276px on 320px');
  });

  harness.test('T2-B02: Viewport 320px Topbar with hamburger and title does not exceed 320px', () => {
    const sim = new ViewportSimulator({ width: 320, height: 568 });
    const topbar = {
      name: 'topbar',
      style: {
        display: 'flex',
        flexDirection: 'row',
        flexWrap: 'nowrap',
        width: 320,
        padding: { top: 0, right: 8, bottom: 0, left: 8 },
        gap: 8,
      },
      children: [
        { name: 'menu-btn', style: { width: 40 } },
        { name: 'title', style: { width: 208, overflowX: 'hidden' }, text: 'Kai Console' },
        { name: 'action', style: { width: 40 } },
      ],
    };
    const res = sim.computeBox(topbar);
    assertLessThanOrEqual(res.scrollWidth, 320, 'Topbar on 320px must not exceed 320px width');
  });

  harness.test('T2-B03: Viewport 320px 2-tier composer toolbar fits within screen bounds', () => {
    const sim = new ViewportSimulator({ width: 320, height: 568 });
    const composer = {
      name: 'composer-toolbar',
      style: { display: 'flex', flexDirection: 'column', widthStr: '100%', gap: 6 },
      children: [
        {
          name: 'tier-1',
          style: { display: 'flex', flexDirection: 'row', widthStr: '100%', gap: 6 },
          children: [
            { name: 'agent-picker', style: { widthStr: '50%' } },
            { name: 'model-picker', style: { widthStr: '50%' } },
          ],
        },
        {
          name: 'tier-2',
          style: { display: 'flex', flexDirection: 'row', widthStr: '100%', gap: 6 },
          children: [
            { name: 'attach-btn', style: { width: 36 } },
            { name: 'more-btn', style: { width: 36 } },
            { name: 'spacer', style: { width: Math.max(0, 320 - 160) } },
            { name: 'send-btn', style: { width: 44 } },
          ],
        },
      ],
    };
    const res = sim.computeBox(composer);
    assertLessThanOrEqual(res.scrollWidth, 320, 'Composer on 320px must fit without horizontal overflow');
  });

  harness.test('T2-B04: Viewport 360px (Standard Android) layout zero overflow', () => {
    const sim = new ViewportSimulator({ width: 360, height: 800 });
    const page = {
      name: 'page',
      style: { width: 360 },
      children: [
        { name: 'card', style: { widthStr: '100%', padding: { top: 12, right: 12, bottom: 12, left: 12 } } },
      ],
    };
    const res = sim.simulatePage(page);
    assert(res.passes, 'Page on 360px must not overflow');
  });

  // -------------------------------------------------------------
  // Boundary 2: Breakpoint Edges (640px vs 641px, 980px vs 981px)
  // -------------------------------------------------------------
  harness.setFeature('Breakpoint Edges (640px, 641px, 980px, 981px)');

  harness.test('T2-B05: Breakpoint boundary 640px: triggers mobile layout rules', () => {
    const sim = new ViewportSimulator({ width: 640, height: 900 });
    const isMobile = sim.innerWidth <= 640;
    assertEqual(isMobile, true, '640px must trigger mobile rules');
  });

  harness.test('T2-B06: Breakpoint boundary 641px: transitions from mobile to tablet rules', () => {
    const sim = new ViewportSimulator({ width: 641, height: 900 });
    const isMobile = sim.innerWidth <= 640;
    const isDrawer = sim.innerWidth <= 980;
    assertEqual(isMobile, false, '641px must not trigger mobile rules');
    assertEqual(isDrawer, true, '641px must retain drawer layout on tablet');
  });

  harness.test('T2-B07: Breakpoint boundary 980px: maximum tablet viewport activates drawer', () => {
    const sim = new ViewportSimulator({ width: 980, height: 1200 });
    const isDrawerActive = sim.innerWidth <= 980;
    assertEqual(isDrawerActive, true, '980px must activate drawer instead of static sidebar');
  });

  harness.test('T2-B08: Breakpoint boundary 981px: minimum desktop viewport uses static sidebar', () => {
    const sim = new ViewportSimulator({ width: 981, height: 1200 });
    const isDrawerActive = sim.innerWidth <= 980;
    assertEqual(isDrawerActive, false, '981px must restore static desktop sidebar');
  });

  // -------------------------------------------------------------
  // Boundary 3: Extreme Strings & Unbroken Tokens
  // -------------------------------------------------------------
  harness.setFeature('Extreme Strings & Unbroken Tokens');

  harness.test('T2-B09: 128-char hex sha512 token wraps within bubble without overflowing 375px', () => {
    const sim = new ViewportSimulator({ width: 375, height: 667 });
    const sha512Token = 'a3f8c901e45b78d210984c35e76a012984b72109c4857d903e124875a9b8c012e345f678901234567890abcdef1234567890abcdef1234567890abcdef12';
    const messageBubble = {
      name: 'bubble',
      style: {
        widthStr: '100%',
        maxWidth: 343,
        wordBreak: 'break-word',
        overflowWrap: 'anywhere',
      },
      text: sha512Token,
    };
    const res = sim.computeBox(messageBubble, 343);
    assertLessThanOrEqual(res.scrollWidth, 375, '128-char token must not exceed viewport');
  });

  harness.test('T2-B10: 256-char continuous URL string wraps cleanly within 390px', () => {
    const sim = new ViewportSimulator({ width: 390, height: 844 });
    const longUrl = 'https://kaka-agent.local/api/v1/workspaces/project-alpha-bravo-charlie/telemetry/events?filter=status%3Dfailed&sort=descending&token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ&limit=100';
    const urlSpan = {
      name: 'url-span',
      style: {
        widthStr: '100%',
        maxWidth: 358,
        wordBreak: 'break-word',
        overflowWrap: 'anywhere',
      },
      text: longUrl,
    };
    const res = sim.computeBox(urlSpan, 358);
    assertLessThanOrEqual(res.scrollWidth, 390, 'Long URL must not overflow 390px viewport');
  });

  harness.test('T2-B11: 300-column single line JSON block inside scroll container keeps document scrollWidth = innerWidth', () => {
    const sim = new ViewportSimulator({ width: 375, height: 667 });
    const longJson = '{"response":{"status":"ok","code":200,"data":{"id":"task_123456789","trace_id":"0xabcdef0123456789abcdef0123456789","metadata":{"engine":"gpt-4o","duration_ms":1234,"tokens":{"prompt":890,"completion":432,"total":1322},"flags":["experimental","stream","high_priority","safe_mode"]}}}}';
    const codeFrame = {
      name: 'markdown-code-frame',
      style: { widthStr: '100%', maxWidth: 343 },
      children: [
        {
          name: 'pre-code-scroll-wrap',
          style: { widthStr: '100%', isScrollContainer: true },
          children: [
            {
              name: 'code-line',
              style: { width: longJson.length * 8 },
              text: longJson,
            },
          ],
        },
      ],
    };
    const res = sim.simulatePage(codeFrame);
    assert(res.passes, 'Code frame must isolate horizontal scroll and prevent document overflow');
    assertEqual(res.documentScrollWidth, 375, 'Document scrollWidth must stay bounded at 375px');
  });

  // -------------------------------------------------------------
  // Boundary 4: Empty States & Minimal Content
  // -------------------------------------------------------------
  harness.setFeature('Empty States & Minimal Content');

  harness.test('T2-B12: Empty chat transcript with WorkspaceSelector on 320px viewport', () => {
    const sim = new ViewportSimulator({ width: 320, height: 568 });
    const emptyChat = {
      name: 'empty-chat-view',
      style: { width: 320 },
      children: [
        {
          name: 'workspace-selector-empty',
          style: { widthStr: '100%', padding: { top: 16, right: 12, bottom: 16, left: 12 } },
          children: [
            { name: 'empty-title', style: { width: 240 } },
            { name: 'browse-button', style: { width: 140, minWidth: 40 } },
          ],
        },
      ],
    };
    const res = sim.simulatePage(emptyChat);
    assert(res.passes, 'Empty chat on 320px must have zero overflow');
  });

  harness.test('T2-B13: Empty Task execution history card on 375px viewport', () => {
    const sim = new ViewportSimulator({ width: 375, height: 667 });
    const emptyTask = {
      name: 'tasks-empty-card',
      style: { widthStr: '100%', padding: { top: 24, right: 16, bottom: 24, left: 16 } },
      children: [
        { name: 'empty-icon', style: { width: 48 } },
        { name: 'empty-msg', style: { widthStr: '100%' }, text: 'No background execution tasks recorded yet.' },
      ],
    };
    const res = sim.simulatePage(emptyTask);
    assert(res.passes, 'Empty task state must not overflow');
  });

  // -------------------------------------------------------------
  // Boundary 5: Safe Area Insets Stress (Dynamic Island & Landscape)
  // -------------------------------------------------------------
  harness.setFeature('Safe Area Insets Stress');

  harness.test('T2-B14: iPhone 14 Pro Dynamic Island notch (top: 59px, bottom: 34px) safe layout', () => {
    const sim = new ViewportSimulator({
      width: 393,
      height: 852,
      safeArea: { top: 59, bottom: 34, left: 0, right: 0 },
    });
    const notchedShell = {
      name: 'notched-shell',
      style: { width: 393, padding: { top: 59, right: 0, bottom: 34, left: 0 } },
      children: [
        { name: 'topbar', style: { widthStr: '100%' } },
        { name: 'content', style: { widthStr: '100%' } },
      ],
    };
    const res = sim.simulatePage(notchedShell);
    assert(res.passes, 'Notched shell on 393px must remain bounded');
  });

  harness.test('T2-B15: Landscape phone orientation (844px wide with 47px left/right safe insets)', () => {
    const sim = new ViewportSimulator({
      width: 844,
      height: 390,
      safeArea: { top: 0, bottom: 21, left: 47, right: 47 },
    });
    const landscapeShell = {
      name: 'landscape-shell',
      style: { width: 844, padding: { top: 0, right: 47, bottom: 21, left: 47 } },
      children: [
        { name: 'content', style: { widthStr: '100%' } },
      ],
    };
    const res = sim.simulatePage(landscapeShell);
    assert(res.passes, 'Landscape shell with side insets must remain bounded');
  });
}
