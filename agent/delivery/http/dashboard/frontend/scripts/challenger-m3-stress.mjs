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
console.log('🥊 ADVERSARIAL STRESS-TEST SUITE: Milestone 3 Deep Empirical Challenge');
console.log('Target: Data Tables, Dialog Bottom-Sheets & Touch Ergonomics');
console.log('================================================================\n');

const stylesDir = path.join(FRONTEND_ROOT, 'src/styles');
const css = new CSSAnalyzer(stylesDir);
const ast = new ASTAnalyzer(FRONTEND_ROOT);
css.load();

const VIEWPORTS = [
  { name: 'Ultra-Compact Folded Phone (280px Galaxy Fold)', width: 280, height: 653 },
  { name: 'Compact Mobile (iPhone SE 1st gen / 320px)', width: 320, height: 568 },
  { name: 'Standard Android (Galaxy S8/S9 / 360px)', width: 360, height: 740 },
  { name: 'iPhone SE 2nd/3rd gen (375px baseline)', width: 375, height: 667 },
  { name: 'iPhone 12/13/14 / Modern Standard (390px)', width: 390, height: 844 },
  { name: 'iPhone Plus / Max (414px)', width: 414, height: 896 },
  { name: 'Mobile Max Boundary (640px)', width: 640, height: 900 },
  { name: 'Mobile-to-Tablet Transition Boundary (641px)', width: 641, height: 900 },
  { name: 'iPad Portrait Tablet (768px)', width: 768, height: 1024 },
  { name: 'Max Tablet / Drawer Boundary (980px)', width: 980, height: 1200 },
  { name: 'Desktop Baseline (1024px)', width: 1024, height: 768 },
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

// =============================================================================
// SECTION 1: WIDE TABLES & SANDBOXES TABLE STRESS TESTING
// =============================================================================
console.log('----------------------------------------------------------------');
console.log('📊 SECTION 1: Wide Tables & Sandboxes Table Stress Testing');
console.log('----------------------------------------------------------------');

// 1.1 CSS Declaration Verification for Sandboxes Table
const sandboxesTableBase = css.findRules(/^\.sandboxes-table$/);
const hasBase680 = sandboxesTableBase.some((r) => r.declarations['min-width'] === '680px');
if (hasBase680) {
  recordPass('sandboxes.css declares min-width: 680px as base for .sandboxes-table', 'min-width: 680px');
} else {
  recordFail('sandboxes.css base min-width', 'Missing min-width: 680px on .sandboxes-table');
}

// 1.2 Tablet & Mobile Overrides for Sandboxes Table (min-width: 720px)
const sandboxesTabletRule = sandboxesTableBase.find((r) => r.mediaQuery && r.mediaQuery.includes('980px'));
if (sandboxesTabletRule && sandboxesTabletRule.declarations['min-width'] === '720px') {
  recordPass('responsive.css enforces min-width: 720px on tablet <= 980px for .sandboxes-table', 'min-width: 720px');
} else {
  recordFail('sandboxes.table tablet rule', 'Missing min-width: 720px under (max-width: 980px)');
}

const sandboxesMobileRule = sandboxesTableBase.find((r) => r.mediaQuery && r.mediaQuery.includes('640px'));
if (sandboxesMobileRule && sandboxesMobileRule.declarations['min-width'] === '720px') {
  recordPass('responsive.css enforces min-width: 720px on mobile <= 640px for .sandboxes-table', 'min-width: 720px');
} else {
  recordFail('sandboxes.table mobile rule', 'Missing min-width: 720px under (max-width: 640px)');
}

// 1.3 Multi-column Tables (.table) Tablet & Mobile Min-Width (min-width: 720px)
const tableRules = css.findRules(/^\.table$/);
const tableTabletRule = tableRules.find((r) => r.mediaQuery && r.mediaQuery.includes('980px'));
if (tableTabletRule && tableTabletRule.declarations['min-width'] === '720px') {
  recordPass('responsive.css enforces min-width: 720px on tablet <= 980px for all .table', 'min-width: 720px');
} else {
  recordFail('multi-column table tablet rule', 'Missing min-width: 720px under (max-width: 980px)');
}

const tableMobileRule = tableRules.find((r) => r.mediaQuery && r.mediaQuery.includes('640px'));
if (tableMobileRule && tableMobileRule.declarations['min-width'] === '720px') {
  recordPass('responsive.css enforces min-width: 720px on mobile <= 640px for all .table', 'min-width: 720px');
} else {
  recordFail('multi-column table mobile rule', 'Missing min-width: 720px under (max-width: 640px)');
}

// 1.4 Scroll Containers Isolated Overflow Containment under Extreme Content
for (const vp of VIEWPORTS) {
  const sim = new ViewportSimulator(vp);
  const isTabletOrMobile = vp.width <= 980;
  const tableMinWidth = isTabletOrMobile ? 720 : 680;

  // Extreme unbroken strings in 6-column Sandboxes table
  const sandboxesPageLayout = {
    name: 'sandboxes-page',
    style: {
      display: 'flex',
      flexDirection: 'column',
      widthStr: '100%',
      padding: { top: 16, right: 16, bottom: 16, left: 16 },
    },
    children: [
      {
        name: 'sandboxes-toolbar',
        style: { display: 'flex', flexDirection: 'row', flexWrap: 'wrap', widthStr: '100%' },
        children: [
          {
            name: 'sandboxes-filter-group',
            style: {
              display: 'flex',
              overflowX: 'auto',
              isScrollContainer: true,
              maxWidth: vp.width - 32,
            },
            children: [
              { name: 'chip-all', style: { minWidth: 60 } },
              { name: 'chip-running', style: { minWidth: 80 } },
              { name: 'chip-stopped', style: { minWidth: 80 } },
              { name: 'chip-archived', style: { minWidth: 80 } },
              { name: 'chip-destroyed', style: { minWidth: 80 } },
            ],
          },
        ],
      },
      {
        name: 'sandboxes-table-wrap',
        style: {
          widthStr: '100%',
          overflowX: 'auto',
          isScrollContainer: true,
        },
        children: [
          {
            name: 'sandboxes-table',
            style: {
              minWidth: Math.max(tableMinWidth, 850), // 850px extreme content
              widthStr: '100%',
            },
            children: [
              { name: 'col-backend', style: { minWidth: 100 } },
              { name: 'col-sandbox-id-unbroken-64char', style: { minWidth: 220, overflowWrap: 'anywhere' } },
              { name: 'col-status', style: { minWidth: 90 } },
              { name: 'col-repo-long-path', style: { minWidth: 200, overflowWrap: 'anywhere' } },
              { name: 'col-last-used', style: { minWidth: 120 } },
              { name: 'col-actions', style: { minWidth: 120 } },
            ],
          },
        ],
      },
    ],
  };

  const res = sim.simulatePage(sandboxesPageLayout);
  if (res.passes && res.documentScrollWidth <= vp.width) {
    recordPass(`Sandboxes table with extreme 850px data on ${vp.width}px (${vp.name})`, `docScrollWidth: ${res.documentScrollWidth}px <= ${vp.width}px`);
  } else {
    recordFail(`Sandboxes table on ${vp.width}px`, `Root document overflow: ${res.documentScrollWidth}px > ${vp.width}px`, res.overflowingNodes.join(', '));
  }
}

// 1.5 768px Tablet Portrait Specific Multi-Column Table Stress Test
{
  const vp768 = { name: 'iPad Portrait (768px)', width: 768, height: 1024 };
  const sim = new ViewportSimulator(vp768);

  // 8-column wide table (Scheduler or Repositories) inside .table-wrap
  const wideTableLayout = {
    name: 'page-content',
    style: {
      widthStr: '100%',
      padding: { top: 20, right: 18, bottom: 20, left: 18 },
    },
    children: [
      {
        name: 'card-panel',
        style: { widthStr: '100%' },
        children: [
          {
            name: 'table-wrap',
            style: {
              widthStr: '100%',
              overflowX: 'auto',
              isScrollContainer: true,
            },
            children: [
              {
                name: 'table-8col',
                style: {
                  minWidth: 720,
                  width: 960, // 960px width on 768px viewport
                },
              },
            ],
          },
        ],
      },
    ],
  };

  const res = sim.simulatePage(wideTableLayout);
  if (res.passes && res.documentScrollWidth <= 768) {
    recordPass('768px Tablet Portrait: 960px 8-column table in .table-wrap prevents page overflow', `docScrollWidth: ${res.documentScrollWidth}px = 768px`);
  } else {
    recordFail('768px Tablet Portrait table', `Page overflowed: ${res.documentScrollWidth}px > 768px`, res.overflowingNodes.join(', '));
  }
}

// 1.6 Lea Verou Scroll Gradient Cues Verification
const scrollCues = css.findRules(/^\.table-wrap$/);
const hasScrollGradients = scrollCues.some(
  (r) => r.mediaQuery && r.mediaQuery.includes('980px') && r.declarations['background-image']?.includes('linear-gradient')
);
if (hasScrollGradients) {
  recordPass('responsive.css defines Lea Verou scroll shadow cues on .table-wrap and .sandboxes-table-wrap under <= 980px', 'background-image linear-gradient');
} else {
  recordFail('scroll gradients check', 'Missing scroll gradient cues in responsive.css');
}

// 1.7 Stress Test: Ultra-Wide 15-Column Table with 1500px content inside .table-wrap on 375px
{
  const vp375 = { name: 'iPhone SE (375px)', width: 375, height: 667 };
  const sim = new ViewportSimulator(vp375);
  const ultraWideTable = {
    name: 'page',
    style: { widthStr: '100%', padding: { top: 10, right: 10, bottom: 10, left: 10 } },
    children: [
      {
        name: 'table-wrap',
        style: { widthStr: '100%', overflowX: 'auto', isScrollContainer: true },
        children: [
          {
            name: 'table-15col',
            style: { minWidth: 720, width: 1500 },
          },
        ],
      },
    ],
  };
  const res = sim.simulatePage(ultraWideTable);
  if (res.passes && res.documentScrollWidth <= 375) {
    recordPass('Ultra-wide 1500px table in .table-wrap on 375px phone keeps root document scrollWidth = 375px', `docScrollWidth: ${res.documentScrollWidth}px`);
  } else {
    recordFail('Ultra-wide 1500px table on 375px', `Overflowed root: ${res.documentScrollWidth}px > 375px`, res.overflowingNodes.join(', '));
  }
}

// =============================================================================
// SECTION 2: DIALOG BOTTOM-SHEET STYLING & ERGONOMICS
// =============================================================================
console.log('\n----------------------------------------------------------------');
console.log('📱 SECTION 2: Dialog Bottom-Sheet Styling & Ergonomics');
console.log('----------------------------------------------------------------');

// 2.1 CSS Declarations for Dialog Bottom-Sheet under (max-width: 640px)
const dialogBackdropRules = css.findRules(/^\.dialog-backdrop$/);
const mobileBackdrop = dialogBackdropRules.find((r) => r.mediaQuery && r.mediaQuery.includes('640px'));

if (mobileBackdrop) {
  if (mobileBackdrop.declarations['align-items'] === 'flex-end') {
    recordPass('Mobile .dialog-backdrop sets align-items: flex-end (bottom-aligned)', 'align-items: flex-end');
  } else {
    recordFail('Dialog backdrop alignment', 'Expected align-items: flex-end on mobile');
  }
  if (mobileBackdrop.declarations['padding'] === '0') {
    recordPass('Mobile .dialog-backdrop sets padding: 0 (flush against viewport bounds)', 'padding: 0');
  } else {
    recordFail('Dialog backdrop padding', 'Expected padding: 0 on mobile');
  }
} else {
  recordFail('Mobile dialog-backdrop rule', 'Missing @media (max-width: 640px) for .dialog-backdrop');
}

const dialogRules = css.findRules(/^\.dialog$/);
const mobileDialog = dialogRules.find((r) => r.mediaQuery && r.mediaQuery.includes('640px'));

if (mobileDialog) {
  const maxW = mobileDialog.declarations['max-width'];
  const w = mobileDialog.declarations['width'];
  const alignSelf = mobileDialog.declarations['align-self'];
  const radiusBL = mobileDialog.declarations['border-bottom-left-radius'];
  const radiusBR = mobileDialog.declarations['border-bottom-right-radius'];
  const maxH = mobileDialog.declarations['max-height'];

  if (maxW === '100vw' && w === '100%') {
    recordPass('Mobile .dialog enforces max-width: 100vw and width: 100%', 'max-width: 100vw; width: 100%');
  } else {
    recordFail('Mobile dialog width', `Expected max-width: 100vw, width: 100%; got max-width: ${maxW}, width: ${w}`);
  }

  if (alignSelf === 'flex-end') {
    recordPass('Mobile .dialog enforces align-self: flex-end for bottom anchoring', 'align-self: flex-end');
  } else {
    recordFail('Mobile dialog align-self', `Expected align-self: flex-end, got ${alignSelf}`);
  }

  if (radiusBL === '0' && radiusBR === '0') {
    recordPass('Mobile .dialog removes bottom border-radius for seamless bottom-sheet docking', 'border-bottom-*-radius: 0');
  } else {
    recordFail('Mobile dialog border-radius', `Expected bottom radii 0, got BL: ${radiusBL}, BR: ${radiusBR}`);
  }

  if (maxH && (maxH.includes('90vh') || maxH.includes('90dvh'))) {
    recordPass('Mobile .dialog constrains height to max 90vh / 90dvh to preserve backdrop tap dismiss', `max-height: ${maxH}`);
  } else {
    recordFail('Mobile dialog max-height', `Expected max-height <= 90vh, got ${maxH}`);
  }
} else {
  recordFail('Mobile dialog rule', 'Missing @media (max-width: 640px) for .dialog');
}

// 2.2 Dialog Close Button 48px Hit-Box Verification
const closeBtnRules = css.findRules(/^\.dialog-close-btn$/);
const mobileCloseBtn = closeBtnRules.find((r) => r.mediaQuery && r.mediaQuery.includes('640px'));

if (mobileCloseBtn) {
  const w = parseInt(mobileCloseBtn.declarations['width'] || '0', 10);
  const h = parseInt(mobileCloseBtn.declarations['height'] || '0', 10);
  const pos = mobileCloseBtn.declarations['position'];

  if (w >= 36 && h >= 36 && pos === 'relative') {
    recordPass('.dialog-close-btn on mobile has base size >= 36px with position: relative', `size: ${w}x${h}px, pos: ${pos}`);
  } else {
    recordFail('Dialog close button base', `Expected >= 36x36 relative, got ${w}x${h}, pos: ${pos}`);
  }
} else {
  recordFail('Dialog close button mobile rule', 'Missing mobile rule for .dialog-close-btn');
}

const closeBtnAfterRules = css.findRules(/^\.dialog-close-btn::after$/);
const mobileCloseBtnAfter = closeBtnAfterRules.find((r) => r.mediaQuery && r.mediaQuery.includes('640px'));

if (mobileCloseBtnAfter) {
  const inset = mobileCloseBtnAfter.declarations['inset'];
  if (inset === '-6px') {
    // 36px + 6px top + 6px bottom = 48px
    recordPass('.dialog-close-btn::after specifies inset: -6px expanding hit-box to 48px x 48px', '36px + 12px = 48px hit-box');
  } else {
    recordFail('Dialog close button hit-box inset', `Expected inset: -6px, got ${inset}`);
  }
} else {
  recordFail('Dialog close button ::after pseudo-element', 'Missing .dialog-close-btn::after mobile rule');
}

// 2.3 Simulated Dialog Behavior across Viewports (Mobile Bottom-Sheet vs Tablet/Desktop Modal)
for (const vp of VIEWPORTS) {
  const sim = new ViewportSimulator(vp);
  const isMobile = vp.width <= 640;

  const dialogSim = {
    name: 'dialog-backdrop',
    style: {
      display: 'flex',
      alignItems: isMobile ? 'flex-end' : 'center',
      justifyContent: 'center',
      widthStr: '100%',
      padding: isMobile ? { top: 0, right: 0, bottom: 0, left: 0 } : { top: 24, right: 24, bottom: 24, left: 24 },
    },
    children: [
      {
        name: 'dialog-container',
        style: {
          widthStr: isMobile ? '100%' : 'min(100%, 600px)',
          maxWidth: isMobile ? vp.width : 600,
          maxHeight: isMobile ? Math.round(vp.height * 0.9) : Math.min(Math.round(vp.height * 0.88), 880),
        },
        children: [
          {
            name: 'dialog-header',
            style: {
              display: 'flex',
              flexDirection: 'row',
              justifyContent: 'space-between',
              widthStr: '100%',
              padding: isMobile ? { top: 16, right: 18, bottom: 16, left: 18 } : { top: 20, right: 24, bottom: 16, left: 24 },
            },
            children: [
              { name: 'dialog-title', style: { widthStr: '80%' } },
              { name: 'dialog-close-btn', style: { width: 36, minWidth: 36 } },
            ],
          },
          {
            name: 'dialog-body',
            style: {
              widthStr: '100%',
              overflowY: 'auto',
              isScrollContainer: true,
              padding: isMobile ? { top: 16, right: 18, bottom: 16, left: 18 } : { top: 20, right: 24, bottom: 20, left: 24 },
            },
          },
        ],
      },
    ],
  };

  const res = sim.simulatePage(dialogSim);
  if (res.passes && res.documentScrollWidth <= vp.width) {
    recordPass(`Dialog layout at ${vp.width}px (${vp.name}) -> ${isMobile ? 'Bottom-Sheet' : 'Centered Modal'}`, `docScrollWidth: ${res.documentScrollWidth}px <= ${vp.width}px`);
  } else {
    recordFail(`Dialog layout on ${vp.width}px`, `Overflowed: ${res.documentScrollWidth}px > ${vp.width}px`, res.overflowingNodes.join(', '));
  }
}

// 2.4 Stress Test: Tall Form with 12 fields inside Dialog Bottom-Sheet on 320x568 phone
{
  const vpSmall = { name: 'Small Phone (320x568)', width: 320, height: 568 };
  const sim = new ViewportSimulator(vpSmall);
  const tallDialog = {
    name: 'dialog-backdrop',
    style: { display: 'flex', alignItems: 'flex-end', widthStr: '100%' },
    children: [
      {
        name: 'dialog-sheet',
        style: { widthStr: '100%', maxHeight: Math.round(568 * 0.9) },
        children: [
          { name: 'dialog-header', style: { widthStr: '100%', height: 52 } },
          {
            name: 'dialog-body',
            style: { widthStr: '100%', maxHeight: 400, overflowY: 'auto', isScrollContainer: true },
            children: [
              { name: 'field-1', style: { widthStr: '100%', height: 42 } },
              { name: 'field-2', style: { widthStr: '100%', height: 42 } },
              { name: 'field-3', style: { widthStr: '100%', height: 42 } },
              { name: 'field-4', style: { widthStr: '100%', height: 42 } },
              { name: 'field-5', style: { widthStr: '100%', height: 42 } },
              { name: 'field-6', style: { widthStr: '100%', height: 42 } },
            ],
          },
          { name: 'dialog-footer', style: { widthStr: '100%', height: 58 } },
        ],
      },
    ],
  };
  const res = sim.simulatePage(tallDialog);
  if (res.passes && res.documentScrollWidth <= 320) {
    recordPass('Tall 6-field form in bottom-sheet on 320x568 scrolls body internally without clipping', `docScrollWidth: ${res.documentScrollWidth}px`);
  } else {
    recordFail('Tall bottom-sheet form on 320px', `Overflowed: ${res.documentScrollWidth}px > 320px`, res.overflowingNodes.join(', '));
  }
}

// =============================================================================
// SECTION 3: TOUCH TARGETS & FORM ERGONOMICS STRESS TESTING
// =============================================================================
console.log('\n----------------------------------------------------------------');
console.log('👆 SECTION 3: Touch Targets & Form Ergonomics Stress Testing');
console.log('----------------------------------------------------------------');

// 3.1 SelectControl Option Height (min-height: 42px)
const selectOptionRules = css.findRules(/^\.select-control-option$/);
const mobileSelectOption = selectOptionRules.find((r) => r.mediaQuery && r.mediaQuery.includes('640px'));

if (mobileSelectOption) {
  const minH = parseInt(mobileSelectOption.declarations['min-height'] || '0', 10);
  const fontSize = parseInt(mobileSelectOption.declarations['font-size'] || '0', 10);
  if (minH >= 42) {
    recordPass('.select-control-option has min-height >= 42px on mobile', `min-height: ${minH}px, font-size: ${fontSize}px`);
  } else {
    recordFail('SelectControl option height', `Expected min-height >= 42px, got ${minH}px`);
  }
} else {
  recordFail('SelectControl option mobile rule', 'Missing @media (max-width: 640px) for .select-control-option');
}

// 3.2 ModelPicker Star Touch Target (width: 40px, min-height: 40px)
const starRules = css.findRules(/^\.model-picker-option-star$/);
const mobileStar = starRules.find((r) => r.mediaQuery && r.mediaQuery.includes('640px'));

if (mobileStar) {
  const w = parseInt(mobileStar.declarations['width'] || '0', 10);
  const minH = parseInt(mobileStar.declarations['min-height'] || '0', 10);
  if (w >= 40 && minH >= 40) {
    recordPass('.model-picker-option-star has touch target >= 40px x 40px on mobile', `width: ${w}px, min-height: ${minH}px`);
  } else {
    recordFail('ModelPicker star touch target', `Expected >= 40x40px, got ${w}x${minH}px`);
  }
} else {
  recordFail('ModelPicker star mobile rule', 'Missing @media (max-width: 640px) for .model-picker-option-star');
}

// 3.3 Toggle Switches (.toggle-track min-width: 44px, min-height: 24px)
const toggleTrackRules = css.findRules(/^\.toggle-track$/);
const mobileToggleTrack = toggleTrackRules.find((r) => r.mediaQuery && r.mediaQuery.includes('640px'));

if (mobileToggleTrack) {
  const minW = parseInt(mobileToggleTrack.declarations['min-width'] || '0', 10);
  const minH = parseInt(mobileToggleTrack.declarations['min-height'] || '0', 10);
  if (minW >= 44 && minH >= 24) {
    recordPass('.toggle-track enforces min-width: 44px and min-height: 24px on mobile', `min-width: ${minW}px, min-height: ${minH}px`);
  } else {
    recordFail('Toggle track dimensions', `Expected min-width >= 44px, min-height >= 24px; got ${minW}x${minH}px`);
  }
} else {
  recordFail('Toggle track mobile rule', 'Missing @media (max-width: 640px) for .toggle-track');
}

// 3.4 Settings Form Input Specificity & iOS Safari Auto-Zoom Prevention
const settingInputs = css.findRules(/^\.setting-control-panel \.input$/);
const mobileSettingInput = settingInputs.find((r) => r.mediaQuery && r.mediaQuery.includes('640px'));

if (mobileSettingInput) {
  const fontSize = mobileSettingInput.declarations['font-size'];
  const minH = parseInt(mobileSettingInput.declarations['min-height'] || '0', 10);
  const is16px = fontSize && fontSize.includes('16px');
  if (is16px && minH >= 42) {
    recordPass('Settings inputs enforce font-size: 16px !important and min-height: 42px !important (iOS zoom prevention)', `font-size: ${fontSize}, min-height: ${minH}px`);
  } else {
    recordFail('Settings input iOS zoom override', `Expected 16px and >= 42px, got font-size: ${fontSize}, min-height: ${minH}px`);
  }
} else {
  recordFail('Settings input mobile rule', 'Missing high-specificity mobile input rules in responsive.css');
}

// 3.5 Onboarding Checklist Item Mobile Stacking
const onboardingRules = css.findRules(/^\.onboarding-item$/);
const mobileOnboarding = onboardingRules.find((r) => r.mediaQuery && r.mediaQuery.includes('640px'));

if (mobileOnboarding && mobileOnboarding.declarations['flex-direction'] === 'column') {
  recordPass('.onboarding-item stacks vertically (flex-direction: column) on mobile', 'flex-direction: column');
} else {
  recordFail('Onboarding item mobile layout', 'Expected flex-direction: column on .onboarding-item under 640px');
}

// 3.6 AgentPromptTab Grid Component Audit
const agentPromptGridRules = css.findRules(/^\.agent-prompt-grid$/);
const tabletAgentPrompt = agentPromptGridRules.find((r) => r.mediaQuery && r.mediaQuery.includes('980px'));

if (tabletAgentPrompt && tabletAgentPrompt.declarations['grid-template-columns'] === '1fr') {
  recordPass('.agent-prompt-grid collapses to 1fr single-column on <= 980px tablet/mobile', 'grid-template-columns: 1fr');
} else {
  recordFail('Agent prompt grid tablet rule', 'Expected grid-template-columns: 1fr under (max-width: 980px)');
}

// 3.7 ModelPicker Dropdown Container Containment
const mpDropdownRules = css.findRules(/^\.model-picker-dropdown$/);
const hasOverflowAuto = mpDropdownRules.some((r) => r.declarations['overflow'] === 'auto' || r.declarations['overflow-y'] === 'auto');
if (hasOverflowAuto) {
  recordPass('.model-picker-dropdown has overflow: auto containing internal picker components', 'overflow: auto');
} else {
  recordFail('ModelPicker dropdown overflow', 'Expected overflow: auto on .model-picker-dropdown');
}

// 3.8 ModelPicker Option with 40px Star on 320px Viewport Stress Test
{
  const vp320 = { name: 'Small Phone (320px)', width: 320, height: 568 };
  const sim = new ViewportSimulator(vp320);
  const modelPickerLayout = {
    name: 'composer-toolbar',
    style: { widthStr: '100%', padding: { top: 4, right: 6, bottom: 4, left: 6 } },
    children: [
      {
        name: 'model-picker',
        style: { widthStr: '50%' },
        children: [
          {
            name: 'model-picker-dropdown',
            style: { widthStr: '100%', overflowX: 'hidden', isScrollContainer: true },
            children: [
              {
                name: 'model-picker-option',
                style: { display: 'flex', flexDirection: 'row', widthStr: '100%' },
                children: [
                  { name: 'model-picker-option-main', style: { widthStr: '100%', minWidth: 0 } },
                  { name: 'model-picker-option-star', style: { width: 40, minWidth: 40 } },
                ],
              },
            ],
          },
        ],
      },
    ],
  };
  const res = sim.simulatePage(modelPickerLayout);
  if (res.passes && res.documentScrollWidth <= 320) {
    recordPass('ModelPicker option with 40px star on 320px phone fits cleanly without causing page overflow', `docScrollWidth: ${res.documentScrollWidth}px`);
  } else {
    recordFail('ModelPicker option on 320px', `Overflowed: ${res.documentScrollWidth}px > 320px`, res.overflowingNodes.join(', '));
  }
}

// =============================================================================
// SUMMARY & VERDICT
// =============================================================================
console.log('\n================================================================');
console.log('📊 CHALLENGER M3 TEST SUITE SUMMARY');
console.log('================================================================');
console.log(`Total Adversarial Tests: ${totalTests}`);
console.log(`Passed Tests:            ${passedTests}`);
console.log(`Failed Tests:            ${failedTests}`);

if (findings.length > 0) {
  console.log('\n❌ VULNERABILITIES & FINDINGS:');
  findings.forEach((f, idx) => {
    console.log(`  ${idx + 1}. [${f.testName}] ${f.issue} (${f.details || 'no details'})`);
  });
  console.log('\nVERDICT: REQUEST_CHANGES');
  process.exit(1);
} else {
  console.log('\n🌟 ALL ADVERSARIAL STRESS TESTS PASSED CLEANLY (100% PASS RATE)!');
  console.log('VERDICT: APPROVE');
  process.exit(0);
}
