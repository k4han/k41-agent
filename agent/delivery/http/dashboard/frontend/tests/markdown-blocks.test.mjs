import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compileFunction } from 'node:vm';
import test from 'node:test';
import { marked } from 'marked';
import ts from 'typescript';

marked.setOptions({ gfm: true, breaks: true });

function fixture() {
  const sanitized = [];
  const source = readFileSync(new URL('../src/lib/markdownBlocks.ts', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  const dependencies = {
    marked: { marked },
    dompurify: { default: { sanitize: html => { sanitized.push(html); return html; } } },
  };
  compileFunction(outputText, ['exports', 'require'])(exports, name => dependencies[name]);
  return { render: exports.createMarkdownBlocks(), sanitized };
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
  ]) {
    for (let end = 0; end <= source.length; end++) {
      const prefix = source.slice(0, end);
      assert.equal(render(prefix).join(''), marked.parse(prefix), `prefix: ${JSON.stringify(prefix)}`);
    }
  }
});
