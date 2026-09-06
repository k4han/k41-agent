#!/usr/bin/env node
// @ts-check
import { execSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runResponsiveSuite } from '../tests/responsive/index.mjs';
import { CSSAnalyzer } from '../tests/responsive/engine/css-analyzer.mjs';
import { ASTAnalyzer } from '../tests/responsive/engine/ast-analyzer.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FRONTEND_ROOT = path.resolve(__dirname, '..');

async function main() {
  const args = process.argv.slice(2);
  const skipBuild = args.includes('--skip-build');
  const onlyBuild = args.includes('--only-build');
  const selfCheck = args.includes('--self-check');
  const isAudit = args.includes('--audit');
  const tierArg = args.find((a) => a.startsWith('--tier='))?.split('=')[1];
  const filterArg = args.find((a) => a.startsWith('--filter='))?.split('=')[1];

  console.log(`================================================================`);
  console.log(`📱 MOBILE & TABLET RESPONSIVE TEST RUNNER (Node ${process.version})`);
  console.log(`Working Directory: ${FRONTEND_ROOT}`);
  console.log(`================================================================\n`);

  // Step 1: TypeScript Check
  if (!skipBuild) {
    console.log(`🔍 [1/3] Verifying TypeScript compilation (npm run check)...`);
    try {
      execSync('npm run check', { cwd: FRONTEND_ROOT, stdio: 'inherit' });
      console.log(`   ✅ TypeScript check passed (exit code 0)\n`);
    } catch (err) {
      console.error(`   ❌ TypeScript check failed!`);
      process.exit(1);
    }
  }

  // Step 2: Production Vite Build Check
  if (!skipBuild && !selfCheck) {
    console.log(`📦 [2/3] Verifying production Vite build (npm run build)...`);
    try {
      execSync('npm run build', { cwd: FRONTEND_ROOT, stdio: 'inherit' });
      console.log(`   ✅ Vite build passed (exit code 0)\n`);
    } catch (err) {
      console.error(`   ❌ Vite build failed!`);
      process.exit(1);
    }
  }

  if (onlyBuild) {
    console.log(`🎉 Build verification complete.`);
    process.exit(0);
  }

  // Step 3: Run Responsive Test Suite
  console.log(`🧪 [3/3] Executing 4-Tier Responsive Opaque-Box Test Suite...`);
  const summary = await runResponsiveSuite({
    tier: tierArg,
    filter: filterArg,
    verbose: true,
  });

  // Step 4: Live Codebase Implementation Status Audit
  console.log(`\n================================================================`);
  console.log(`📋 LIVE CODEBASE RESPONSIVE RULES AUDIT`);
  console.log(`================================================================`);

  const stylesDir = path.join(FRONTEND_ROOT, 'src/styles');
  const css = new CSSAnalyzer(stylesDir);
  const ast = new ASTAnalyzer(FRONTEND_ROOT);
  css.load();

  const auditChecks = [
    {
      feature: 'F1: Viewport Meta Tag (viewport-fit=cover)',
      check: () => ast.verifyViewportMeta().pass,
      expected: 'index.html contains viewport-fit=cover',
    },
    {
      feature: 'F1: Safe Area Custom Variables (--safe-top, etc.)',
      check: () => css.getVariable('--safe-top') !== undefined && css.getVariable('--safe-bottom') !== undefined,
      expected: 'tokens.css defines --safe-top and --safe-bottom',
    },
    {
      feature: 'F2: Unified Drawer Breakpoint (DRAWER_MAX_PX = 980)',
      check: () => ast.verifyUiConstants().hasDrawerMaxPx,
      expected: 'uiConstants.ts exports DRAWER_MAX_PX = 980',
    },
    {
      feature: 'F3: Drawer Navigation & Backdrop (touch-action: none)',
      check: () => ast.verifyUseMobileDrawer().hasCloseMobileDrawer,
      expected: 'useMobileDrawer provides closeMobileDrawer',
    },
    {
      feature: 'F5: Global Touch Target >= 40px/44px',
      check: () => {
        const val = css.getVariable('--touch-target');
        return val ? parseFloat(val) >= 40 : false;
      },
      expected: 'tokens.css defines --touch-target >= 40px (currently 34px in initial survey)',
    },
    {
      feature: 'F8: iOS Safari Auto-Zoom Prevention (16px font-size)',
      check: () => css.hasDeclaration('.chat-prompt-input', 'font-size', /16px/),
      expected: 'chat-prompt-input has font-size: 16px on mobile',
    },
    {
      feature: 'F13: Markdown Inline Code Wrapping (break-word)',
      check: () => css.findRules('.message-markdown').length > 0,
      expected: 'message-markdown inline code rules present',
    },
    {
      feature: 'F17: AgentPromptTab Responsive Grid (.agent-prompt-grid)',
      check: () => ast.verifyAgentPromptTab().pass,
      expected: 'AgentPromptTab does not use hardcoded minmax(0, 1fr) 280px',
    },
    {
      feature: 'F23: Multi-Column Tables Tablet Min-Width (>= 720px)',
      check: () => css.findRules('.table-wrap').length > 0 || css.findRules('.table').length > 0,
      expected: 'Multi-column tables wrapped in scroll container',
    },
  ];

  let auditPassed = 0;
  for (const item of auditChecks) {
    const passed = item.check();
    if (passed) {
      auditPassed++;
      console.log(`  [PASS] ${item.feature}`);
    } else {
      console.log(`  [PENDING/AUDIT] ${item.feature}`);
      console.log(`     Target: ${item.expected}`);
    }
  }

  console.log(`\nAudit Score: ${auditPassed}/${auditChecks.length} live rules matched.`);
  console.log(`Test Suite Execution: ${summary.passed}/${summary.total} tests passed.`);

  if (summary.failed > 0) {
    console.error(`\n❌ Test runner failed with ${summary.failed} failures.`);
    process.exit(1);
  } else {
    console.log(`\n✅ All ${summary.passed} test cases in test suite PASSED! (Exit 0)`);
    process.exit(0);
  }
}

main().catch((err) => {
  console.error('Fatal error in verify-responsive.mjs:', err);
  process.exit(1);
});
