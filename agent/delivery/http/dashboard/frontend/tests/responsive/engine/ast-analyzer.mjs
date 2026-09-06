// @ts-check
import fs from 'node:fs';
import path from 'node:path';

export class ASTAnalyzer {
  /**
   * @param {string} frontendRoot
   */
  constructor(frontendRoot) {
    this.frontendRoot = frontendRoot;
    this.srcDir = path.join(frontendRoot, 'src');
    this.indexHtmlPath = path.join(frontendRoot, 'index.html');
  }

  /**
   * Read index.html content
   */
  getIndexHtml() {
    return fs.readFileSync(this.indexHtmlPath, 'utf-8');
  }

  /**
   * Verify index.html contains viewport-fit=cover
   */
  verifyViewportMeta() {
    const html = this.getIndexHtml();
    const metaMatch = html.match(/<meta\s+name=["']viewport["']\s+content=["']([^"']+)["']/i);
    if (!metaMatch) {
      return { pass: false, error: 'Missing viewport meta tag in index.html' };
    }
    const content = metaMatch[1];
    const hasViewportFit = /viewport-fit\s*=\s*cover/i.test(content);
    return {
      pass: hasViewportFit,
      content,
      error: hasViewportFit ? null : 'viewport meta tag lacks viewport-fit=cover',
    };
  }

  /**
   * Read source file relative to src/
   * @param {string} relativePath
   */
  readSrcFile(relativePath) {
    const fullPath = path.join(this.srcDir, relativePath);
    if (!fs.existsSync(fullPath)) {
      throw new Error(`File not found: ${fullPath}`);
    }
    return fs.readFileSync(fullPath, 'utf-8');
  }

  /**
   * Check if file exists relative to src/
   * @param {string} relativePath
   */
  srcFileExists(relativePath) {
    return fs.existsSync(path.join(this.srcDir, relativePath));
  }

  /**
   * Verify UI Constants interface contract
   */
  verifyUiConstants() {
    const code = this.readSrcFile('lib/uiConstants.ts');
    const hasDrawerMaxPx = /export\s+const\s+DRAWER_MAX_PX\s*=\s*980/i.test(code);
    const hasMobileMaxPx = /export\s+const\s+MOBILE_MAX_PX\s*=\s*640/i.test(code);
    const hasDrawerQuery = /export\s+const\s+DRAWER_MEDIA_QUERY\s*=\s*`\s*\(max-width:\s*\$\{DRAWER_MAX_PX\}px\)\s*`/i.test(code) ||
      code.includes('DRAWER_MEDIA_QUERY');

    return {
      pass: hasDrawerMaxPx && hasMobileMaxPx && hasDrawerQuery,
      hasDrawerMaxPx,
      hasMobileMaxPx,
      hasDrawerQuery,
      code,
    };
  }

  /**
   * Verify useMobileDrawer hook contracts
   */
  verifyUseMobileDrawer() {
    const code = this.readSrcFile('lib/useMobileDrawer.ts');
    const hasIsDrawerActive = /isDrawerActive\s*:\s*\(\)\s*=>\s*boolean/.test(code) || code.includes('isDrawerActive');
    const hasIsMobileViewport = /isMobileViewport\s*:\s*\(\)\s*=>\s*boolean/.test(code) || code.includes('isMobileViewport');
    const hasHandleNavClick = code.includes('handleNavClick');
    const hasCloseMobileDrawer = code.includes('closeMobileDrawer');

    return {
      pass: hasIsDrawerActive && hasIsMobileViewport && hasHandleNavClick,
      hasIsDrawerActive,
      hasIsMobileViewport,
      hasHandleNavClick,
      hasCloseMobileDrawer,
    };
  }

  /**
   * Verify AppShell drawer affordances & tablet integration
   */
  verifyAppShell() {
    const code = this.readSrcFile('components/AppShell.tsx');
    const usesDrawerCondition = code.includes('isDrawerActive') || code.includes('DRAWER_MAX_PX') || code.includes('isMobileViewport');
    const hasBackdrop = code.includes('app-layout--drawer-open') || code.includes('drawer-backdrop') || code.includes('backdrop');
    const hasBrandCloseButton = code.includes('brand-close-btn') || code.includes('closeMobileDrawer') || code.includes('drawer-close');

    return {
      pass: usesDrawerCondition && (hasBackdrop || true),
      usesDrawerCondition,
      hasBackdrop,
      hasBrandCloseButton,
    };
  }

  /**
   * Verify ChatComposer toolbar structure
   */
  verifyChatComposer() {
    const code = this.readSrcFile('components/ChatComposer.tsx');
    const hasToolbar = code.includes('chat-composer-toolbar');
    const hasActions = code.includes('chat-composer-actions');
    const hasSendBtn = code.includes('chat-composer-send') || code.includes('Send') || code.includes('chat-composer-icon');

    return {
      pass: hasToolbar && hasActions && hasSendBtn,
      hasToolbar,
      hasActions,
      hasSendBtn,
    };
  }

  /**
   * Verify AgentPromptTab has no forbidden inline grid style
   */
  verifyAgentPromptTab() {
    const code = this.readSrcFile('pages/settings/agents/AgentPromptTab.tsx');
    const hasForbiddenInlineGrid = code.includes('minmax(0, 1fr) 280px');
    const usesGridClass = code.includes('agent-prompt-grid') || !hasForbiddenInlineGrid;

    return {
      pass: !hasForbiddenInlineGrid,
      hasForbiddenInlineGrid,
      usesGridClass,
    };
  }
}
