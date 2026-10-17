import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/workspacePreview.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
runInNewContext(outputText, { exports });
const { parseDelimitedPreview, workspacePreviewKind, sandboxedWorkspaceHtml } = exports;

test('binary formats use native previews and unsupported files remain downloadable', () => {
  for (const [path, kind] of [['photo.PNG', 'image'], ['drawing.svg', 'image'], ['report.pdf', 'pdf'], ['song.mp3', 'audio'], ['movie.webm', 'video']]) {
    assert.equal(workspacePreviewKind(path, true), kind);
    assert.equal(workspacePreviewKind(path, false), kind);
  }
  assert.equal(workspacePreviewKind('archive.zip', true), 'unsupported');
  assert.equal(workspacePreviewKind('file.md', true), 'unsupported');
  assert.equal(workspacePreviewKind('file.md', false), 'markdown');
  assert.equal(workspacePreviewKind('file.html', false), 'html');
  assert.equal(workspacePreviewKind('file.csv', false), 'table');
  assert.equal(workspacePreviewKind('file.py', false), 'text');
  assert.equal(workspacePreviewKind('png', false), 'text');
  assert.equal(workspacePreviewKind('.pdf', false), 'text');
});

test('CSV handles quoted delimiters, escaped quotes, multiline cells, BOM and CRLF', () => {
  const result = parseDelimitedPreview('\uFEFFname,value\r\n"one,two","say ""hi"""\r\nnext,"multi\nline"\r\n', ',');
  assert.deepEqual(JSON.parse(JSON.stringify(result.rows)), [['name', 'value'], ['one,two', 'say "hi"'], ['next', 'multi\nline']]);
  assert.equal(result.truncated, false);
  assert.deepEqual(JSON.parse(JSON.stringify(parseDelimitedPreview('a\tb\t', '\t').rows)), [['a', 'b', '']]);
  assert.equal(parseDelimitedPreview('', ',').rows.length, 0);
});

test('table previews bound row and column counts and report truncation', () => {
  assert.equal(parseDelimitedPreview('a\nb\n', ',', 2).truncated, false);
  const rows = parseDelimitedPreview('a\nb\nc', ',', 2);
  assert.equal(rows.rows.length, 2);
  assert.equal(rows.truncated, true);
  const columns = parseDelimitedPreview('a,b,c\nd,e,f', ',', 200, 2);
  assert.equal(columns.rows[0].length, 2);
  assert.equal(columns.truncated, true);
});

test('HTML policy precedes document content and blocks scripts and network access', () => {
  const html = sandboxedWorkspaceHtml('<script>parent.compromised = true</script>');
  assert.ok(html.startsWith('<meta http-equiv="Content-Security-Policy"'));
  assert.ok(html.includes("default-src 'none'"));
  assert.ok(html.includes("base-uri 'none'"));
  assert.ok(!html.includes("script-src 'unsafe-inline'"));
});
