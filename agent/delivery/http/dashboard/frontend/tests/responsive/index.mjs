// @ts-check
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { TestHarness } from './engine/harness.mjs';
import { CSSAnalyzer } from './engine/css-analyzer.mjs';
import { ASTAnalyzer } from './engine/ast-analyzer.mjs';
import { registerTier1Tests } from './tier1-feature-coverage.test.mjs';
import { registerTier2Tests } from './tier2-boundary-corner.test.mjs';
import { registerTier3Tests } from './tier3-cross-combinations.test.mjs';
import { registerTier4Tests } from './tier4-real-world-scenarios.test.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FRONTEND_ROOT = path.resolve(__dirname, '../..');

/**
 * Execute the full responsive test suite
 * @param {Object} [options]
 * @param {string} [options.tier]
 * @param {string} [options.filter]
 * @param {boolean} [options.verbose]
 */
export async function runResponsiveSuite(options = {}) {
  const stylesDir = path.join(FRONTEND_ROOT, 'src/styles');
  const css = new CSSAnalyzer(stylesDir);
  const ast = new ASTAnalyzer(FRONTEND_ROOT);

  css.load();

  const harness = new TestHarness({
    tierFilter: options.tier,
    filter: options.filter,
    verbose: options.verbose ?? true,
  });

  const ctx = { css, ast, frontendRoot: FRONTEND_ROOT };

  // Register all 4 Tiers
  registerTier1Tests(harness, ctx);
  registerTier2Tests(harness, ctx);
  registerTier3Tests(harness, ctx);
  registerTier4Tests(harness, ctx);

  const results = await harness.run();
  return results;
}

// Allow direct execution
if (process.argv[1] && process.argv[1].endsWith('index.mjs')) {
  const args = process.argv.slice(2);
  const tierArg = args.find((a) => a.startsWith('--tier='))?.split('=')[1];
  const filterArg = args.find((a) => a.startsWith('--filter='))?.split('=')[1];

  runResponsiveSuite({ tier: tierArg, filter: filterArg })
    .then((summary) => {
      process.exit(summary.failed > 0 ? 1 : 0);
    })
    .catch((err) => {
      console.error('Fatal error running responsive test suite:', err);
      process.exit(1);
    });
}
