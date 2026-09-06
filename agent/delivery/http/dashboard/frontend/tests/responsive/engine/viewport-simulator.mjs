// @ts-check

/**
 * @typedef {Object} Viewport
 * @property {number} width
 * @property {number} height
 * @property {{ top: number, bottom: number, left: number, right: number }} [safeArea]
 * @property {boolean} [virtualKeyboard]
 * @property {number} [virtualKeyboardHeight]
 */

/**
 * @typedef {Object} BoxStyle
 * @property {'block' | 'flex' | 'grid' | 'inline' | 'inline-block' | 'none'} [display]
 * @property {'row' | 'column'} [flexDirection]
 * @property {'nowrap' | 'wrap'} [flexWrap]
 * @property {number} [width]
 * @property {string} [widthStr]
 * @property {number} [minWidth]
 * @property {number} [maxWidth]
 * @property {number} [gap]
 * @property {{ top: number, right: number, bottom: number, left: number }} [padding]
 * @property {{ top: number, right: number, bottom: number, left: number }} [margin]
 * @property {'visible' | 'hidden' | 'auto' | 'scroll'} [overflowX]
 * @property {'visible' | 'hidden' | 'auto' | 'scroll'} [overflowY]
 * @property {'normal' | 'break-word' | 'break-all'} [wordBreak]
 * @property {'normal' | 'break-word' | 'anywhere'} [overflowWrap]
 * @property {boolean} [isScrollContainer]
 */

/**
 * @typedef {Object} BoxNode
 * @property {string} name
 * @property {string} [className]
 * @property {BoxStyle} style
 * @property {string} [text]
 * @property {BoxNode[]} [children]
 * @property {number} [computedWidth]
 * @property {number} [scrollWidth]
 */

export class ViewportSimulator {
  /**
   * @param {Viewport} viewport
   */
  constructor(viewport) {
    this.viewport = {
      width: viewport.width,
      height: viewport.height,
      safeArea: viewport.safeArea || { top: 0, bottom: 0, left: 0, right: 0 },
      virtualKeyboard: viewport.virtualKeyboard || false,
      virtualKeyboardHeight: viewport.virtualKeyboardHeight || 0,
    };
  }

  get innerWidth() {
    return this.viewport.width;
  }

  get innerHeight() {
    if (this.viewport.virtualKeyboard) {
      return Math.max(0, this.viewport.height - this.viewport.virtualKeyboardHeight);
    }
    return this.viewport.height;
  }

  /**
   * Parse dimension value against parent width
   * @param {string | number | undefined} val
   * @param {number} parentWidth
   * @returns {number | undefined}
   */
  resolveDimension(val, parentWidth) {
    if (val === undefined) return undefined;
    if (typeof val === 'number') return val;
    const str = String(val).trim();
    if (str.endsWith('px')) return parseFloat(str);
    if (str.endsWith('%')) return (parseFloat(str) / 100) * parentWidth;
    if (str.endsWith('vw')) return (parseFloat(str) / 100) * this.viewport.width;
    if (str.startsWith('min(') && str.endsWith(')')) {
      const parts = str.slice(4, -1).split(',').map((p) => p.trim());
      const resolvedParts = parts.map((p) => this.resolveDimension(p, parentWidth) ?? 0);
      return Math.min(...resolvedParts);
    }
    if (str.startsWith('max(') && str.endsWith(')')) {
      const parts = str.slice(4, -1).split(',').map((p) => p.trim());
      const resolvedParts = parts.map((p) => this.resolveDimension(p, parentWidth) ?? 0);
      return Math.max(...resolvedParts);
    }
    if (str.startsWith('calc(') && str.endsWith(')')) {
      const expr = str.slice(5, -1).trim();
      const opMatch = expr.match(/^(.+?)\s+([+-])\s+(.+)$/);
      if (opMatch) {
        const leftVal = this.resolveDimension(opMatch[1].trim(), parentWidth) ?? 0;
        const rightVal = this.resolveDimension(opMatch[3].trim(), parentWidth) ?? 0;
        return opMatch[2] === '+' ? leftVal + rightVal : leftVal - rightVal;
      }
    }
    const num = parseFloat(str);
    return isNaN(num) ? undefined : num;
  }

  /**
   * Compute box layout and detect overflow
   * @param {BoxNode} node
   * @param {number} [containerWidth]
   * @returns {{ computedWidth: number, scrollWidth: number, inducesDocOverflow: boolean }}
   */
  computeBox(node, containerWidth = this.viewport.width) {
    const style = node.style || {};
    const padding = style.padding || { top: 0, right: 0, bottom: 0, left: 0 };
    const margin = style.margin || { top: 0, right: 0, bottom: 0, left: 0 };
    const padX = padding.left + padding.right;
    const marX = margin.left + margin.right;

    let targetWidth = containerWidth - marX;
    if (style.widthStr) {
      targetWidth = this.resolveDimension(style.widthStr, containerWidth) ?? targetWidth;
    } else if (style.width !== undefined) {
      targetWidth = style.width;
    }

    if (style.minWidth !== undefined) {
      const min = this.resolveDimension(style.minWidth, containerWidth) ?? 0;
      targetWidth = Math.max(targetWidth, min);
    }
    if (style.maxWidth !== undefined) {
      const max = this.resolveDimension(style.maxWidth, containerWidth) ?? Infinity;
      targetWidth = Math.min(targetWidth, max);
    }

    const contentWidth = Math.max(0, targetWidth - padX);
    let childContentScrollWidth = 0;

    // Handle text length overflow if present
    if (node.text) {
      const charWidth = 8; // approx average char width in px
      const canWrap =
        style.wordBreak === 'break-word' ||
        style.wordBreak === 'break-all' ||
        style.overflowWrap === 'anywhere' ||
        style.overflowWrap === 'break-word';

      // Split words by whitespace to check if any single unbroken word overflows
      const words = node.text.split(/\s+/);
      const longestWordPx = Math.max(...words.map((w) => w.length * charWidth));

      if (!canWrap && longestWordPx > contentWidth) {
        childContentScrollWidth = Math.max(childContentScrollWidth, longestWordPx);
      } else {
        childContentScrollWidth = Math.max(childContentScrollWidth, Math.min(contentWidth, longestWordPx));
      }
    }

    // Handle child nodes
    if (node.children && node.children.length > 0) {
      const isFlexRow = style.display === 'flex' && (style.flexDirection === 'row' || !style.flexDirection);
      const isWrap = style.flexWrap === 'wrap';
      const gap = style.gap || 0;

      if (isFlexRow) {
        let totalRowWidth = 0;
        let maxSingleChildScroll = 0;

        // Check if flex children have flex: 1 or percentage with gap
        const totalGaps = Math.max(0, (node.children.length - 1) * gap);
        const availableForFlex = Math.max(0, contentWidth - totalGaps);

        for (let i = 0; i < node.children.length; i++) {
          const child = node.children[i];
          let childContainerWidth = contentWidth;
          if (child.style && (child.style.flex === 1 || child.style.widthStr === '50%' || child.style.widthStr === 'flex')) {
            childContainerWidth = availableForFlex / node.children.length;
          }
          const childResult = this.computeBox(child, childContainerWidth);
          const gapContribution = i > 0 ? gap : 0;
          totalRowWidth += childResult.computedWidth + gapContribution;
          maxSingleChildScroll = Math.max(maxSingleChildScroll, childResult.scrollWidth);
        }

        if (isWrap) {
          childContentScrollWidth = Math.max(childContentScrollWidth, maxSingleChildScroll);
        } else {
          childContentScrollWidth = Math.max(childContentScrollWidth, totalRowWidth, maxSingleChildScroll);
        }
      } else {
        // Block or flex column
        for (const child of node.children) {
          if (child.style && (child.style.position === 'fixed' || child.style.position === 'absolute')) {
            continue;
          }
          const childResult = this.computeBox(child, contentWidth);
          childContentScrollWidth = Math.max(childContentScrollWidth, childResult.scrollWidth);
        }
      }
    }

    const scrollWidth = Math.max(targetWidth, childContentScrollWidth + padX);
    node.computedWidth = targetWidth;
    node.scrollWidth = scrollWidth;

    // A scroll container (e.g. overflow-x: auto) absorbs its children's scrollWidth!
    const isScrollContainer =
      style.isScrollContainer ||
      style.overflowX === 'auto' ||
      style.overflowX === 'scroll' ||
      style.overflowX === 'hidden';

    const inducesDocOverflow = isScrollContainer
      ? targetWidth + marX > this.viewport.width
      : scrollWidth + marX > this.viewport.width;

    return {
      computedWidth: targetWidth,
      scrollWidth: isScrollContainer ? targetWidth : scrollWidth,
      inducesDocOverflow,
    };
  }

  /**
   * Verify an entire page structure for document horizontal overflow
   * @param {BoxNode} rootNode
   * @returns {{ passes: boolean, innerWidth: number, documentScrollWidth: number, overflowingNodes: string[] }}
   */
  simulatePage(rootNode) {
    /** @type {string[]} */
    const overflowingNodes = [];

    const checkNode = (node, parentWidth, insideScrollContainer = false) => {
      const res = this.computeBox(node, parentWidth);
      const style = node.style || {};
      const isScrollContainer =
        style.isScrollContainer ||
        style.overflowX === 'auto' ||
        style.overflowX === 'scroll' ||
        style.overflowX === 'hidden';

      if (!insideScrollContainer && res.inducesDocOverflow) {
        overflowingNodes.push(
          `${node.name}${node.className ? '.' + node.className : ''} (scrollWidth: ${res.scrollWidth}px > innerWidth: ${this.viewport.width}px)`
        );
      }
      if (node.children) {
        const innerContentWidth = isScrollContainer
          ? Math.max(node.computedWidth ?? this.viewport.width, node.scrollWidth ?? this.viewport.width)
          : (node.computedWidth ?? this.viewport.width);

        for (const child of node.children) {
          checkNode(child, innerContentWidth, insideScrollContainer || isScrollContainer);
        }
      }
    };

    const rootRes = this.computeBox(rootNode, this.viewport.width);
    checkNode(rootNode, this.viewport.width, false);

    const docScrollWidth = Math.max(rootRes.scrollWidth, this.viewport.width);
    const passes = docScrollWidth <= this.viewport.width && overflowingNodes.length === 0;

    return {
      passes,
      innerWidth: this.viewport.width,
      documentScrollWidth: docScrollWidth,
      overflowingNodes,
    };
  }
}
