// @ts-check
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ViewportSimulator } from '../tests/responsive/engine/viewport-simulator.mjs';
import { CSSAnalyzer } from '../tests/responsive/engine/css-analyzer.mjs';
import { ASTAnalyzer } from '../tests/responsive/engine/ast-analyzer.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FRONTEND_ROOT = path.resolve(__dirname, '..');

console.log('================================================================');
console.log('🥊 ADVERSARIAL STRESS-TEST SUITE: Milestone 2 Edge Cases');
console.log('================================================================\n');

const stylesDir = path.join(FRONTEND_ROOT, 'src/styles');
const css = new CSSAnalyzer(stylesDir);
const ast = new ASTAnalyzer(FRONTEND_ROOT);
css.load();

const VIEWPORTS = [
  { name: 'Small Phone (iPhone SE 1st gen / Android Mini)', width: 320, height: 568 },
  { name: 'Standard Android (Galaxy S8/S9)', width: 360, height: 740 },
  { name: 'iPhone SE / 8 (Baseline Mobile)', width: 375, height: 667 },
  { name: 'iPhone 12/13/14 (Standard iOS)', width: 390, height: 844 },
  { name: 'iPhone Plus / Pro Max (Large Phone)', width: 414, height: 896 },
  { name: 'Breakpoint Max Mobile', width: 640, height: 900 },
  { name: 'Tablet Portrait (iPad)', width: 768, height: 1024 },
  { name: 'Breakpoint Max Drawer / Tablet', width: 980, height: 1200 },
];

let totalTests = 0;
let passedTests = 0;
let failedTests = 0;
const findings = [];

function recordPass(testName, details) {
  totalTests++;
  passedTests++;
  console.log(`  ✅ PASS: ${testName} ${details ? `(${details})` : ''}`);
}

function recordFail(testName, issue, details) {
  totalTests++;
  failedTests++;
  findings.push({ testName, issue, details });
  console.log(`  ❌ FAIL: ${testName}`);
  console.log(`     Issue: ${issue}`);
  if (details) console.log(`     Details: ${details}`);
}

// -----------------------------------------------------------------------------
// STRESS TEST 1: Long Unbroken Code Tokens (500-char string in inline <code>)
// -----------------------------------------------------------------------------
console.log('----------------------------------------------------------------');
console.log('🔬 STRESS TEST 1: Long Unbroken Code Tokens in Inline <code>');
console.log('----------------------------------------------------------------');

const token500 = 'A'.repeat(500); // 500-character unbroken string

for (const vp of VIEWPORTS) {
  const sim = new ViewportSimulator(vp);

  // Message bubble containing inline code with 500-char token
  const chatBubbleWith500CharInlineCode = {
    name: 'message-bubble',
    style: {
      widthStr: '100%',
      maxWidth: vp.width - 32,
      padding: { top: 10, right: 13, bottom: 10, left: 13 },
      wordBreak: 'break-word',
      overflowWrap: 'anywhere',
    },
    children: [
      {
        name: 'message-markdown',
        style: { widthStr: '100%' },
        children: [
          {
            name: 'inline-code-500',
            style: {
              widthStr: '100%',
              wordBreak: 'break-word',
              overflowWrap: 'anywhere',
            },
            text: token500,
          },
        ],
      },
    ],
  };

  const res = sim.simulatePage(chatBubbleWith500CharInlineCode);
  if (res.passes && res.documentScrollWidth <= vp.width) {
    recordPass(`500-char code token on ${vp.width}px (${vp.name})`, `docScrollWidth: ${res.documentScrollWidth}px <= ${vp.width}px`);
  } else {
    recordFail(`500-char code token on ${vp.width}px`, `Document overflowed: ${res.documentScrollWidth}px > ${vp.width}px`, res.overflowingNodes.join(', '));
  }
}

// -----------------------------------------------------------------------------
// STRESS TEST 2: Wide Multi-Column Markdown Tables
// -----------------------------------------------------------------------------
console.log('\n----------------------------------------------------------------');
console.log('🔬 STRESS TEST 2: Wide Multi-Column Markdown Tables');
console.log('----------------------------------------------------------------');

for (const vp of VIEWPORTS) {
  const sim = new ViewportSimulator(vp);

  // 10-column table totaling 1200px inside message-markdown
  const tableContentWidth = 1200;
  const markdownTableNode = {
    name: 'chat-page-with-table',
    style: { width: vp.width },
    children: [
      {
        name: 'chat-transcript',
        style: { widthStr: '100%', padding: { top: 12, right: 12, bottom: 12, left: 12 } },
        children: [
          {
            name: 'message-bubble-assistant',
            style: { widthStr: '100%' },
            children: [
              {
                name: 'message-markdown-table',
                style: {
                  widthStr: '100%',
                  maxWidth: '100%',
                  overflowX: 'auto',
                  isScrollContainer: true,
                },
                children: [
                  {
                    name: 'wide-table-10-cols',
                    style: { width: tableContentWidth },
                  },
                ],
              },
            ],
          },
        ],
      },
    ],
  };

  const res = sim.simulatePage(markdownTableNode);
  if (res.passes && res.documentScrollWidth <= vp.width) {
    recordPass(`1200px 10-col markdown table on ${vp.width}px`, `docScrollWidth: ${res.documentScrollWidth}px <= ${vp.width}px (scroll isolated)`);
  } else {
    recordFail(`1200px 10-col markdown table on ${vp.width}px`, `Document overflowed!`, res.overflowingNodes.join(', '));
  }
}

// -----------------------------------------------------------------------------
// STRESS TEST 3: Plan Review Markdown Table
// -----------------------------------------------------------------------------
console.log('\n----------------------------------------------------------------');
console.log('🔬 STRESS TEST 3: Plan Review Markdown Table (.plan-review-markdown table)');
console.log('----------------------------------------------------------------');

for (const vp of VIEWPORTS) {
  const sim = new ViewportSimulator(vp);

  const planReviewTable = {
    name: 'plan-review-card',
    style: { width: vp.width, padding: { top: 12, right: 12, bottom: 12, left: 12 } },
    children: [
      {
        name: 'plan-review-markdown-table',
        style: {
          widthStr: '100%',
          maxWidth: '100%',
          overflowX: 'auto',
          isScrollContainer: true,
        },
        children: [
          {
            name: 'plan-table-content',
            style: { width: 1400 },
          },
        ],
      },
    ],
  };

  const res = sim.simulatePage(planReviewTable);
  if (res.passes && res.documentScrollWidth <= vp.width) {
    recordPass(`1400px plan review table on ${vp.width}px`, `docScrollWidth: ${res.documentScrollWidth}px <= ${vp.width}px`);
  } else {
    recordFail(`1400px plan review table on ${vp.width}px`, `Plan review table overflowed document!`, res.overflowingNodes.join(', '));
  }
}

// -----------------------------------------------------------------------------
// STRESS TEST 4: Long Workspace Folder Paths (260-char paths)
// -----------------------------------------------------------------------------
console.log('\n----------------------------------------------------------------');
console.log('🔬 STRESS TEST 4: Long Workspace Folder Paths');
console.log('----------------------------------------------------------------');

const extremePath = 'D:\\CODE_C\\enterprise-organization\\very-long-project-subfolder-depth-level-1\\subfolder-level-2\\another-deeply-nested-folder-level-3\\kaka-agent-v2-production-microservices-checkout-with-extremely-long-directory-names-exceeding-standard-windows-max-path-260-characters\\src';

for (const vp of VIEWPORTS) {
  const sim = new ViewportSimulator(vp);

  // Test locked workspace status in selector
  const workspaceSelectorLocked = {
    name: 'workspace-selector-locked',
    style: {
      widthStr: '100%',
      maxWidth: Math.min(580, vp.width - 24),
      padding: { top: 10, right: 14, bottom: 10, left: 14 },
    },
    children: [
      {
        name: 'workspace-selector-locked-status',
        style: {
          display: 'flex',
          flexDirection: 'row',
          gap: 8,
          widthStr: '100%',
        },
        children: [
          { name: 'lock-icon', style: { width: 13 } },
          {
            name: 'path-span',
            style: {
              widthStr: 'flex',
              overflowX: 'hidden',
            },
            text: extremePath,
          },
        ],
      },
    ],
  };

  const res = sim.simulatePage(workspaceSelectorLocked);
  if (res.passes && res.documentScrollWidth <= vp.width) {
    recordPass(`260-char workspace path on ${vp.width}px`, `docScrollWidth: ${res.documentScrollWidth}px <= ${vp.width}px (ellipsis applied)`);
  } else {
    recordFail(`260-char workspace path on ${vp.width}px`, `Workspace path overflowed document!`, res.overflowingNodes.join(', '));
  }
}

// -----------------------------------------------------------------------------
// STRESS TEST 5: Long Thread Titles in Chat Header
// -----------------------------------------------------------------------------
console.log('\n----------------------------------------------------------------');
console.log('🔬 STRESS TEST 5: Long Thread Titles in Chat Header');
console.log('----------------------------------------------------------------');

const longTitle = 'Fix: Optimize mobile and tablet responsiveness across the entire SolidJS Web Dashboard application, ensuring intuitive navigation, touch ergonomics, fluid layouts, and zero viewport overflow on narrow screens';

for (const vp of VIEWPORTS) {
  const sim = new ViewportSimulator(vp);

  const chatHeaderNode = {
    name: 'chat-header',
    style: {
      display: 'flex',
      flexDirection: 'row',
      width: vp.width,
      padding: { top: 6, right: 14, bottom: 6, left: 14 },
      gap: 0,
    },
    children: [
      {
        name: 'chat-header-left',
        style: {
          display: 'flex',
          flexDirection: 'row',
          flex: 1,
          gap: 8,
        },
        children: [
          { name: 'chat-header-menu-toggle', style: { width: 40, minWidth: 40 } },
          {
            name: 'chat-header-title-wrap',
            style: {
              display: 'flex',
              flexDirection: 'row',
              flex: 1,
              gap: 8,
            },
            children: [
              {
                name: 'chat-header-title',
                style: {
                  flex: 1,
                  overflowX: 'hidden',
                },
                text: longTitle,
              },
            ],
          },
        ],
      },
      {
        name: 'chat-header-right',
        style: {
          display: 'flex',
          flexDirection: 'row',
          width: 40,
          gap: 6,
          margin: { top: 0, right: 0, bottom: 0, left: 12 },
        },
        children: [
          { name: 'chat-header-workspace-btn', style: { width: 40, minWidth: 40 } },
        ],
      },
    ],
  };

  const res = sim.simulatePage(chatHeaderNode);
  if (res.passes && res.documentScrollWidth <= vp.width) {
    recordPass(`Long thread title header on ${vp.width}px`, `docScrollWidth: ${res.documentScrollWidth}px <= ${vp.width}px`);
  } else {
    recordFail(`Long thread title header on ${vp.width}px`, `Chat header overflowed document!`, res.overflowingNodes.join(', '));
  }
}

// -----------------------------------------------------------------------------
// STRESS TEST 6: Context Window Popover Coordinates & Viewport Clamping
// -----------------------------------------------------------------------------
console.log('\n----------------------------------------------------------------');
console.log('🔬 STRESS TEST 6: Context Window Popover Clamping & Screen Coordinates');
console.log('----------------------------------------------------------------');

// Let's compute the physical layout of the composer toolbar and find
// the EXACT bounding box coordinates of .context-window-popover on mobile & tablet screens!
for (const vp of VIEWPORTS) {
  const isMobile = vp.width <= 640;
  const composerMargin = isMobile ? 8 : 16;
  const composerPadX = isMobile ? 12 : 14;
  const composerWidth = vp.width - (composerMargin * 2);
  const composerContentWidth = composerWidth - (composerPadX * 2);

  const plusBtnWidth = 40;
  const moreBtnWidth = 40;
  const contextBtnWidth = 40;
  const actionsGap = 8;

  let buttonLeftViewport;
  let buttonRightViewport;

  if (isMobile) {
    // Mobile 2-tier toolbar: Tier 2 actions row starts at left edge of composer content
    buttonLeftViewport = composerMargin + composerPadX + plusBtnWidth + actionsGap + moreBtnWidth + actionsGap;
    buttonRightViewport = buttonLeftViewport + contextBtnWidth;
  } else {
    // Desktop/Tablet single-row toolbar: Tier 1 selectors (~320px) + gap (12px) before Tier 2 actions
    const tier1Width = 320;
    const tierGap = 12;
    buttonLeftViewport = composerMargin + composerPadX + tier1Width + tierGap + plusBtnWidth + actionsGap + moreBtnWidth + actionsGap;
    buttonRightViewport = buttonLeftViewport + contextBtnWidth;
  }

  let popoverLeftViewport;
  let popoverRightViewport;
  let popoverWidth;

  if (isMobile) {
    // Inspect parsed CSS rules from src/styles/context-window.css under @media (max-width: 640px)
    const popoverRules = css.findRules('.context-window-popover', '640px');
    const popoverRule = popoverRules.find((r) => r.declarations['left'] !== undefined || r.declarations['right'] !== undefined);
    const leftDecl = popoverRule?.declarations['left'] ?? 'auto';
    const rightDecl = popoverRule?.declarations['right'] ?? 'auto';
    const rawWidth = popoverRule?.declarations['width'] ? parseFloat(popoverRule.declarations['width']) : 280;
    popoverWidth = Math.min(rawWidth, vp.width - 32);

    if (leftDecl !== 'auto') {
      const leftOffset = parseFloat(leftDecl) || 0;
      popoverLeftViewport = buttonLeftViewport + leftOffset;
      popoverRightViewport = popoverLeftViewport + popoverWidth;
    } else if (rightDecl !== 'auto') {
      const rightOffset = parseFloat(rightDecl) || 0;
      popoverRightViewport = buttonRightViewport - rightOffset;
      popoverLeftViewport = popoverRightViewport - popoverWidth;
    } else {
      popoverLeftViewport = buttonLeftViewport;
      popoverRightViewport = popoverLeftViewport + popoverWidth;
    }
  } else {
    // Tablet & Desktop: Popover is centered above button (right: 50%; transform: translateX(50%))
    popoverWidth = 290;
    const buttonCenter = buttonLeftViewport + (contextBtnWidth / 2);
    popoverLeftViewport = buttonCenter - (popoverWidth / 2);
    popoverRightViewport = buttonCenter + (popoverWidth / 2);
  }

  console.log(`\n  📱 Viewport ${vp.width}px (${vp.name}):`);
  console.log(`     Context button screen bounds: [${buttonLeftViewport}px -> ${buttonRightViewport}px]`);
  console.log(`     Popover width: ${popoverWidth}px`);
  console.log(`     Popover screen bounds: [${popoverLeftViewport}px -> ${popoverRightViewport}px]`);

  if (popoverLeftViewport < 0) {
    const clippedPx = Math.abs(popoverLeftViewport);
    const clippedPercent = Math.round((clippedPx / popoverWidth) * 100);
    recordFail(
      `Context Window Popover on ${vp.width}px (${vp.name})`,
      `Popover hangs ${clippedPx}px off LEFT screen edge (left: ${popoverLeftViewport}px)! ${clippedPercent}% of popover content is clipped and inaccessible!`,
      `buttonRight: ${buttonRightViewport}px, popoverWidth: ${popoverWidth}px => popoverLeft: ${popoverLeftViewport}px`
    );
  } else if (popoverRightViewport > vp.width) {
    recordFail(
      `Context Window Popover on ${vp.width}px (${vp.name})`,
      `Popover overflows right screen edge by ${popoverRightViewport - vp.width}px!`,
      `popoverRight: ${popoverRightViewport}px > vp.width: ${vp.width}px`
    );
  } else {
    recordPass(`Context Window Popover fits on ${vp.width}px`, `left: ${popoverLeftViewport}px, right: ${popoverRightViewport}px within [0, ${vp.width}]`);
  }
}

// -----------------------------------------------------------------------------
// SUMMARY & VERDICT
// -----------------------------------------------------------------------------
console.log('\n================================================================');
console.log('📊 EMPIRICAL CHALLENGER RESULTS SUMMARY');
console.log('================================================================');
console.log(`Total Scenarios: ${totalTests}`);
console.log(`Passed:          ${passedTests}`);
console.log(`Failed:          ${failedTests}`);

if (failedTests > 0) {
  console.log('\n🚨 DETECTED FAILURE MODES & BUGS:');
  for (const f of findings) {
    console.log(`  - [${f.testName}]: ${f.issue}`);
    if (f.details) console.log(`    ${f.details}`);
  }
  console.log('\nVerdict: REQUEST_CHANGES');
} else {
  console.log('\nVerdict: APPROVE');
}
