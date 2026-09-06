// @ts-check
import { performance } from 'node:perf_hooks';

/**
 * @typedef {Object} TestCase
 * @property {string} id
 * @property {string} tier
 * @property {string} feature
 * @property {string} name
 * @property {() => Promise<void> | void} fn
 * @property {'pass' | 'fail' | 'skip'} [status]
 * @property {Error} [error]
 * @property {number} [durationMs]
 */

export class TestHarness {
  constructor(options = {}) {
    /** @type {TestCase[]} */
    this.tests = [];
    this.currentTier = 'Tier 1';
    this.currentFeature = 'General';
    this.options = {
      verbose: options.verbose ?? true,
      stopOnFail: options.stopOnFail ?? false,
      filter: options.filter ?? null,
      tierFilter: options.tierFilter ?? null,
    };
  }

  setTier(tier) {
    this.currentTier = tier;
  }

  setFeature(feature) {
    this.currentFeature = feature;
  }

  /**
   * Register a test case
   * @param {string} name
   * @param {() => Promise<void> | void} fn
   * @param {Object} [meta]
   */
  test(name, fn, meta = {}) {
    const id = `T${this.tests.length + 1}`;
    this.tests.push({
      id,
      tier: meta.tier || this.currentTier,
      feature: meta.feature || this.currentFeature,
      name,
      fn,
    });
  }

  /**
   * Run registered tests
   * @returns {Promise<{ passed: number, failed: number, skipped: number, total: number, results: TestCase[] }>}
   */
  async run() {
    let passed = 0;
    let failed = 0;
    let skipped = 0;

    const filteredTests = this.tests.filter((t) => {
      if (this.options.tierFilter && !t.tier.toLowerCase().includes(this.options.tierFilter.toLowerCase())) {
        return false;
      }
      if (this.options.filter && !t.name.toLowerCase().includes(this.options.filter.toLowerCase())) {
        return false;
      }
      return true;
    });

    console.log(`\n===============================================================`);
    console.log(`🚀 RUNNING RESPONSIVE TEST SUITE (${filteredTests.length} tests)`);
    console.log(`===============================================================\n`);

    let lastTier = '';
    let lastFeature = '';

    for (const t of filteredTests) {
      if (t.tier !== lastTier) {
        lastTier = t.tier;
        lastFeature = '';
        console.log(`\n--- [${t.tier}] ----------------------------------------------`);
      }
      if (t.feature !== lastFeature) {
        lastFeature = t.feature;
        console.log(`  📁 Feature: ${t.feature}`);
      }

      const start = performance.now();
      try {
        await t.fn();
        t.durationMs = performance.now() - start;
        t.status = 'pass';
        passed++;
        if (this.options.verbose) {
          console.log(`    ✅ PASS [${t.id}] ${t.name} (${t.durationMs.toFixed(1)}ms)`);
        }
      } catch (err) {
        t.durationMs = performance.now() - start;
        t.status = 'fail';
        t.error = err instanceof Error ? err : new Error(String(err));
        failed++;
        console.log(`    ❌ FAIL [${t.id}] ${t.name} (${t.durationMs.toFixed(1)}ms)`);
        console.log(`       Reason: ${t.error.message}`);
        if (this.options.stopOnFail) {
          break;
        }
      }
    }

    console.log(`\n===============================================================`);
    console.log(`📊 TEST SUITE SUMMARY`);
    console.log(`===============================================================`);
    console.log(`Total:   ${filteredTests.length}`);
    console.log(`Passed:  ${passed}`);
    console.log(`Failed:  ${failed}`);
    console.log(`Skipped: ${skipped}`);
    console.log(`Status:  ${failed === 0 ? 'SUCCESS (Exit 0)' : 'FAILED (Exit 1)'}`);
    console.log(`===============================================================\n`);

    return {
      passed,
      failed,
      skipped,
      total: filteredTests.length,
      results: filteredTests,
    };
  }
}

// Concrete Assertion Helpers
export function assert(condition, message) {
  if (!condition) {
    throw new Error(message || 'Assertion failed: condition is false');
  }
}

export function assertEqual(actual, expected, message) {
  if (actual !== expected) {
    throw new Error(
      `${message ? message + ' - ' : ''}Expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`
    );
  }
}

export function assertGreaterThanOrEqual(actual, minimum, message) {
  if (actual < minimum) {
    throw new Error(
      `${message ? message + ' - ' : ''}Expected value >= ${minimum}, got ${actual}`
    );
  }
}

export function assertLessThanOrEqual(actual, maximum, message) {
  if (actual > maximum) {
    throw new Error(
      `${message ? message + ' - ' : ''}Expected value <= ${maximum}, got ${actual}`
    );
  }
}

export function assertMatches(text, regex, message) {
  if (!regex.test(text)) {
    throw new Error(
      `${message ? message + ' - ' : ''}Text does not match pattern ${regex}. Content sample: ${text.slice(0, 100)}`
    );
  }
}

export function assertIncludes(actual, substring, message) {
  if (typeof actual === 'string' && !actual.includes(substring)) {
    throw new Error(
      `${message ? message + ' - ' : ''}Expected string to include "${substring}". Found: ${actual.slice(0, 120)}`
    );
  }
  if (Array.isArray(actual) && !actual.includes(substring)) {
    throw new Error(
      `${message ? message + ' - ' : ''}Expected array to include "${substring}". Array: ${JSON.stringify(actual)}`
    );
  }
}
