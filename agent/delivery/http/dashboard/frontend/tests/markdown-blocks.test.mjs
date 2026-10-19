import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compileFunction } from 'node:vm';
import test from 'node:test';
import { marked } from 'marked';
import ts from 'typescript';

marked.setOptions({ gfm: true, breaks: true });

function fixture() {
  const sanitized = [];
  const lexed = [];
  const source = readFileSync(new URL('../src/lib/markdownBlocks.ts', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  const dependencies = {
    marked: { marked: new Proxy(marked, {
      get(target, key) {
        if (key === 'lexer') return source => { lexed.push(source); return marked.lexer(source); };
        return target[key];
      },
    }) },
    dompurify: { default: { sanitize: html => { sanitized.push(html); return html; } } },
  };
  compileFunction(outputText, ['exports', 'require'])(exports, name => dependencies[name]);
  const update = exports.createMarkdownBlocks();
  const blocks = [];
  return {
    blocks,
    lexed,
    render(source) {
      const next = update(source);
      blocks.length = next.length;
      next.blocks.forEach((block, index) => { blocks[next.start + index] = block; });
      return blocks.map(block => block.html);
    },
    sanitized,
  };
}

test('streaming sanitizes only changed blocks and retains complete document semantics', () => {
  const { render, sanitized } = fixture();
  const prefix = '# Heading\n\nCompleted paragraph.\n\n```js\nconst done = true;\n```\n\n';
  assert.equal(render(prefix + 'Trailing').join(''), marked.parse(prefix + 'Trailing'));
  sanitized.length = 0;
  for (const tail of ['Trailing text', 'Trailing text\nwith more', 'Trailing text\nwith **bold**']) {
    assert.equal(render(prefix + tail).join(''), marked.parse(prefix + tail));
  }
  assert.equal(sanitized.length, 3);
  assert.ok(sanitized.every(html => !html.includes('Completed paragraph') && !html.includes('const done')));
});

test('later reference definitions and block reclassification invalidate cached output', () => {
  const { render } = fixture();
  for (const text of [
    '[reference][target]\n\nParagraph',
    '[reference][target]\n\nParagraph\n\n[target]: https://example.com',
    '[reference][target]\n\nParagraph\n\n[target]: https://example.org',
    'Heading\n', 'Heading\n=======\n',
    '- one\n\n- two\n', '- one\n\n- two\n\n  nested paragraph\n',
    '> quoted\n>\n> paragraph\n\n---\n\n| a | b |\n|---|---|\n| c | d |',
  ]) assert.equal(render(text).join(''), marked.parse(text));
});

test('raw HTML spanning blocks is sanitized as a complete document', () => {
  const { render, sanitized } = fixture();
  for (const text of ['<div>\n\n**content**\n\n</div>', 'paragraph <span>\n\nnext paragraph</span>']) {
    sanitized.length = 0;
    assert.equal(render(text).join(''), marked.parse(text));
    assert.deepEqual(sanitized, [marked.parse(text)]);
  }
});

test('incomplete streamed syntax matches complete-document parsing at every prefix', () => {
  const { render } = fixture();
  for (const source of [
    '# Heading\n\nParagraph **bold** and [link](https://example.com).\n\nLast paragraph',
    '- first\n\n- second\n\n  nested paragraph\n\n> quote\n>\n> next paragraph',
    'Heading\n=======\n\n| a | b |\n|---|---|\n| c | d |\n\n---\n',
    '```js\nconst value = "<div>";\n```\n\n[reference][target]\n\n[target]: https://example.com',
    '<div>\n\n**Nested content**\n\n</div>\n\nTrailing paragraph',
    'Leading paragraph.\n\n- first\n\n- second\n\n  nested paragraph\n\n    indented continuation\n\nTail',
    'Leading paragraph.\n\n> first\n>\n> - nested\n>\n>   continuation\n\nTail',
    'Leading paragraph.\n\n~~~mermaid\nflowchart LR\nA --> B\n~~~\n\nTail',
    'Leading paragraph.\n\n    code\n\n    more code\n\nTail',
    '[target]: https://example.com\n\n[reference][target]\n\n[target]: https://ignored.example\n\nTail',
    'Leading paragraph.\r\n\r\nHeading\r\n---\r\n\r\nTail',
    '# Heading\n\nparagraph\n***changes back into text\n\nTail',
    '# Heading\n\n- first\n\n- second\n\nTail',
    '# Heading\n\n===\n---\n\tindented\n  nested\n|---|---|\n- item',
  ]) {
    for (let end = 0; end <= source.length; end++) {
      const prefix = source.slice(0, end);
      assert.equal(render(prefix).join(''), marked.parse(prefix), `prefix: ${JSON.stringify(prefix)}`);
    }
  }
});

test('append updates lex only the mutable suffix and retain completed block identities', () => {
  const { render, blocks, lexed } = fixture();
  const prefix = Array.from({ length: 960 }, (_, index) => `Paragraph ${index}. ${'Stable context '.repeat(24)}\n\n`).join('');
  render(prefix + 'Tail');
  const completed = blocks.slice(0, -1);
  lexed.length = 0;
  const tails = ['Tail grows', 'Tail grows again', 'Tail grows again **bold**'];
  for (const tail of tails) {
    assert.equal(render(prefix + tail).join(''), marked.parse(prefix + tail));
    completed.forEach((block, index) => assert.equal(blocks[index], block));
  }
  assert.deepEqual(lexed.map(source => source.length), tails.map(source => source.length));
  assert.deepEqual(lexed, tails);
});

test('edits and truncation reset lexical offsets before append updates resume', () => {
  const { render, lexed } = fixture();
  const sources = [
    '# Original\n\nStable paragraph.\n\nTail',
    '# Edited\n\nStable paragraph.\n\nTail',
    '# Edited\n\nStable paragraph.\n\nTail grows',
    '# Edited\n\n',
    '# Edited\n\nReplacement',
    '',
    'Fresh paragraph',
  ];
  for (const source of sources) assert.equal(render(source).join(''), marked.parse(source));
  assert.ok(lexed.includes(sources[1]));
  assert.ok(lexed.includes(sources[3]));
  assert.ok(lexed.includes('Tail grows'));
});

test('fence completion updates metadata even when the rendered code HTML is identical', () => {
  const { render, blocks } = fixture();
  const source = 'flowchart LR\nA --> B';
  for (const [opening, closing] of [['```mermaid', '```'], ['~~~~mmd', '~~~~']]) {
    render(`${opening}\n${source}\n`);
    const html = blocks[0].html;
    assert.deepEqual(blocks[0].mermaidBlocks.map(block => block.complete), [false]);
    render(`${opening}\n${source}\n${closing}`);
    assert.equal(blocks[0].html, html);
    assert.deepEqual(blocks[0].mermaidBlocks, [{ source, complete: true }]);
    render(`${opening}\n${source}\n${closing}\n\n${opening}\n${source}\n`);
    const complete = blocks.flatMap(block => block.mermaidBlocks)
      .filter(block => block.source === source && block.complete).length;
    assert.equal(complete, 1);
  }
});

test('mermaid metadata keeps identical fences in document order', () => {
  const { render, blocks } = fixture();
  const source = 'flowchart LR\nA --> B';
  render([
    '- ```mermaid',
    '  flowchart LR',
    '  A --> B',
    '',
    '- ```mermaid',
    '  flowchart LR',
    '  A --> B',
    '  ```',
    '',
  ].join('\n'));
  assert.deepEqual(blocks[0].mermaidBlocks, [
    { source, complete: false },
    { source, complete: true },
  ]);
});

test('mixed block boundaries remain equivalent to a full parse across streamed prefixes', () => {
  const { render } = fixture();
  const lines = [
    'paragraph\n', '\n', ' \n', '- item\n', '  nested\n', '> quote\n',
    '```js\n', '```\n', '~~~~\n', '[target]: https://example.com\n',
    '[label][target]\n', '===\n', '---\n', '| a | b |\n', '|---|---|\n',
    '<div>\n', '</div>\n', '\tindented\n', '![image](image.png)\n',
  ];
  let seed = 42;
  for (let document = 0; document < 80; document++) {
    let source = '# Stable heading\n\n';
    for (let line = 0; line < 10; line++) {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      source += lines[seed % lines.length];
    }
    render('');
    for (let end = 1; end <= source.length; end++) {
      const prefix = source.slice(0, end);
      assert.equal(render(prefix).join(''), marked.parse(prefix), `document ${document}, prefix: ${JSON.stringify(prefix)}`);
    }
  }
});
