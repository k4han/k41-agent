// @ts-check
import fs from 'node:fs';
import path from 'node:path';

/**
 * Lightweight AST-like CSS rule representation
 * @typedef {Object} CSSDeclaration
 * @property {string} property
 * @property {string} value
 * @property {boolean} important
 */

/**
 * @typedef {Object} CSSRule
 * @property {string} selector
 * @property {Record<string, string>} declarations
 * @property {string} rawBody
 * @property {string} file
 * @property {string | null} mediaQuery
 */

export class CSSAnalyzer {
  /**
   * @param {string} stylesDir
   */
  constructor(stylesDir) {
    this.stylesDir = stylesDir;
    /** @type {CSSRule[]} */
    this.rules = [];
    /** @type {Map<string, string>} */
    this.variables = new Map();
    this.loaded = false;
  }

  load() {
    if (this.loaded) return;
    const files = fs.readdirSync(this.stylesDir).filter((f) => f.endsWith('.css'));
    for (const file of files) {
      const filePath = path.join(this.stylesDir, file);
      const content = fs.readFileSync(filePath, 'utf-8');
      this.parseCSS(content, file);
    }
    this.loaded = true;
  }

  /**
   * Parse a CSS file content into structured rules
   * @param {string} css
   * @param {string} fileName
   */
  parseCSS(css, fileName) {
    // Strip comments
    const stripped = css.replace(/\/\*[\s\S]*?\*\//g, '');

    // Track media queries
    let pos = 0;
    const len = stripped.length;

    while (pos < len) {
      const mediaMatch = stripped.slice(pos).match(/^\s*@media\s*([^{]+)\{/);
      if (mediaMatch) {
        const mediaQuery = mediaMatch[1].trim();
        const startBlock = pos + mediaMatch[0].length;
        // Find matching closing brace
        let depth = 1;
        let endBlock = startBlock;
        while (endBlock < len && depth > 0) {
          if (stripped[endBlock] === '{') depth++;
          else if (stripped[endBlock] === '}') depth--;
          endBlock++;
        }
        const mediaContent = stripped.slice(startBlock, endBlock - 1);
        this.parseRuleBlock(mediaContent, fileName, mediaQuery);
        pos = endBlock;
      } else {
        // Regular rule or at-rule
        const nextAt = stripped.indexOf('@', pos);
        const nextBrace = stripped.indexOf('{', pos);

        if (nextBrace === -1) break;

        if (nextAt !== -1 && nextAt < nextBrace) {
          // Some at-rule like @keyframes or @import
          if (stripped.slice(nextAt).startsWith('@keyframes')) {
            // skip keyframe block
            const start = stripped.indexOf('{', nextAt);
            let depth = 1;
            let cur = start + 1;
            while (cur < len && depth > 0) {
              if (stripped[cur] === '{') depth++;
              else if (stripped[cur] === '}') depth--;
              cur++;
            }
            pos = cur;
            continue;
          }
        }

        const selector = stripped.slice(pos, nextBrace).trim();
        let depth = 1;
        let cur = nextBrace + 1;
        while (cur < len && depth > 0) {
          if (stripped[cur] === '{') depth++;
          else if (stripped[cur] === '}') depth--;
          cur++;
        }
        const body = stripped.slice(nextBrace + 1, cur - 1);
        if (selector && !selector.startsWith('@')) {
          this.parseSingleRule(selector, body, fileName, null);
        }
        pos = cur;
      }
    }
  }

  /**
   * @param {string} block
   * @param {string} fileName
   * @param {string} mediaQuery
   */
  parseRuleBlock(block, fileName, mediaQuery) {
    let pos = 0;
    const len = block.length;
    while (pos < len) {
      const brace = block.indexOf('{', pos);
      if (brace === -1) break;
      const selector = block.slice(pos, brace).trim();
      let depth = 1;
      let cur = brace + 1;
      while (cur < len && depth > 0) {
        if (block[cur] === '{') depth++;
        else if (block[cur] === '}') depth--;
        cur++;
      }
      const body = block.slice(brace + 1, cur - 1);
      if (selector && !selector.startsWith('@')) {
        this.parseSingleRule(selector, body, fileName, mediaQuery);
      }
      pos = cur;
    }
  }

  /**
   * @param {string} selector
   * @param {string} body
   * @param {string} fileName
   * @param {string | null} mediaQuery
   */
  parseSingleRule(selector, body, fileName, mediaQuery) {
    const declarations = {};
    const decls = body.split(';');
    for (const d of decls) {
      const trimmed = d.trim();
      if (!trimmed) continue;
      const colon = trimmed.indexOf(':');
      if (colon === -1) continue;
      const prop = trimmed.slice(0, colon).trim().toLowerCase();
      const val = trimmed.slice(colon + 1).trim();
      declarations[prop] = val;

      if (prop.startsWith('--')) {
        this.variables.set(prop, val);
      }
    }

    // Split multiple selectors separated by comma
    const selectors = selector.split(',').map((s) => s.trim()).filter(Boolean);
    for (const s of selectors) {
      this.rules.push({
        selector: s,
        declarations,
        rawBody: body,
        file: fileName,
        mediaQuery,
      });
    }
  }

  /**
   * Find rules matching a selector pattern and optional media query
   * @param {string | RegExp} selectorPattern
   * @param {string | RegExp} [mediaPattern]
   * @returns {CSSRule[]}
   */
  findRules(selectorPattern, mediaPattern) {
    this.load();
    return this.rules.filter((r) => {
      const selMatches =
        typeof selectorPattern === 'string'
          ? r.selector.includes(selectorPattern)
          : selectorPattern.test(r.selector);
      if (!selMatches) return false;

      if (mediaPattern !== undefined) {
        if (!r.mediaQuery) return false;
        return typeof mediaPattern === 'string'
          ? r.mediaQuery.includes(mediaPattern)
          : mediaPattern.test(r.mediaQuery);
      }
      return true;
    });
  }

  /**
   * Get effective CSS variable value
   * @param {string} name
   * @returns {string | undefined}
   */
  getVariable(name) {
    this.load();
    return this.variables.get(name);
  }

  /**
   * Check if any matching rule has a specific property value matching regex
   * @param {string | RegExp} selector
   * @param {string} property
   * @param {RegExp} valueRegex
   * @param {string | RegExp} [mediaPattern]
   * @returns {boolean}
   */
  hasDeclaration(selector, property, valueRegex, mediaPattern) {
    const rules = this.findRules(selector, mediaPattern);
    const propLower = property.toLowerCase();
    for (const r of rules) {
      const val = r.declarations[propLower];
      if (val && valueRegex.test(val)) {
        return true;
      }
    }
    return false;
  }

  /**
   * Check if a touch target meets minimum px size (e.g. >= 40px)
   * @param {string | RegExp} selector
   * @param {number} minPx
   * @param {string | RegExp} [mediaPattern]
   * @returns {{ pass: boolean, reasons: string[] }}
   */
  verifyTouchTarget(selector, minPx = 40, mediaPattern) {
    const rules = this.findRules(selector, mediaPattern);
    const reasons = [];
    if (rules.length === 0) {
      return { pass: false, reasons: [`No CSS rule found matching selector: ${selector}`] };
    }

    let hasMinHeight = false;
    let hasMinWidth = false;

    for (const r of rules) {
      const h = r.declarations['height'] || r.declarations['min-height'];
      const w = r.declarations['width'] || r.declarations['min-width'];

      if (h) {
        const numH = parseFloat(h);
        if (h.includes('var(--touch-target') || (numH && numH >= minPx)) {
          hasMinHeight = true;
        }
      }
      if (w) {
        const numW = parseFloat(w);
        if (w.includes('var(--touch-target') || (numW && numW >= minPx)) {
          hasMinWidth = true;
        }
      }
    }

    return {
      pass: hasMinHeight && hasMinWidth,
      reasons: [
        `Height check: ${hasMinHeight ? 'PASS' : 'FAIL'}`,
        `Width check: ${hasMinWidth ? 'PASS' : 'FAIL'}`,
      ],
    };
  }
}
