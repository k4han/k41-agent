import assert from "node:assert/strict";
import { createReadStream, existsSync, watch } from "node:fs";
import { cp, mkdir, readFile, rm, stat } from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const source = path.join(root, "website");
const output = path.join(root, "dist", "site");
const docsRoot = path.join(root, "docs");
const docsSource = path.join(docsRoot, "guide");
const docsOutput = path.join(root, "dist", "docs");
const sharedDocs = ["model-pools.md", "workspace-previews.md"];
const installers = ["install.ps1", "install.sh", "uninstall.ps1"];
const command = process.argv[2] || "build";

async function check() {
  const html = await readFile(path.join(source, "index.html"), "utf8");
  assert.match(html, /<html lang="en">/, "The page must declare its language.");
  assert.match(html, /name="viewport"/, "The page must support mobile viewports.");
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(ids).size, ids.length, "HTML IDs must be unique.");
  for (const [, reference] of html.matchAll(/\b(?:src|href)="([^"]+)"/g)) {
    if (/^(?:https?:|mailto:)/.test(reference) || reference === "#") continue;
    if (reference.startsWith("#")) {
      assert.ok(ids.includes(reference.slice(1)), `Missing anchor: ${reference}`);
      continue;
    }
    assert.ok(reference.startsWith("./"), `Use relative asset URLs for the website: ${reference}`);
    const relative = reference.slice(2).split("#")[0];
    if (relative.startsWith("docs/")) {
      const page = relative.slice("docs/".length);
      const markdown = page.endsWith("/") || !page ? `${page}index.md` : page.replace(/\.html$/, ".md");
      assert.ok(existsSync(path.join(docsSource, markdown)), `Missing documentation page: ${reference}`);
      continue;
    }
    assert.ok(existsSync(path.join(installers.includes(relative) ? root : source, relative)), `Missing asset: ${reference}`);
  }
  for (const [, reference] of html.matchAll(/\baria-(?:controls|labelledby)="([^"]+)"/g)) {
    for (const id of reference.split(/\s+/)) assert.ok(ids.includes(id), `Missing ARIA target: ${id}`);
  }
  const css = await readFile(path.join(source, "styles.css"), "utf8");
  for (const [, reference] of css.matchAll(/url\("([^\"]+)"\)/g)) {
    assert.ok(reference.startsWith("./"), `Stylesheet assets must be self-hosted and relative: ${reference}`);
    assert.ok(existsSync(path.join(source, reference)), `Missing stylesheet asset: ${reference}`);
  }
  const syntax = spawnSync(process.execPath, ["--check", path.join(source, "script.js")], { encoding: "utf8" });
  assert.equal(syntax.status, 0, syntax.stderr || "Website JavaScript syntax check failed.");
  for (const installer of installers) assert.ok(existsSync(path.join(root, installer)), `Missing installer: ${installer}`);
  assert.ok(existsSync(path.join(docsSource, "index.md")), "The documentation home page must exist.");
  console.log("Site checks passed: asset paths, anchors, accessibility references, and JavaScript syntax.");
}

async function build() {
  await check();
  const { build: buildDocs } = await import("vitepress");
  await buildDocs(docsRoot);
  assert.equal(path.dirname(output), path.join(root, "dist"), "Build output must stay inside the project dist directory.");
  assert.equal(path.basename(output), "site", "Only the generated site directory may be replaced.");
  await rm(output, { recursive: true, force: true });
  await mkdir(output, { recursive: true });
  await cp(source, output, { recursive: true });
  for (const installer of installers) await cp(path.join(root, installer), path.join(output, installer));
  await cp(path.join(root, "CNAME"), path.join(output, "CNAME"));
  await cp(docsOutput, path.join(output, "docs"), { recursive: true });
  await cp(path.join(docsOutput, "404.html"), path.join(output, "404.html"));
  console.log(`Static site built at ${path.relative(root, output)} (including VitePress docs and the original installers).`);
}

async function serve() {
  await build();
  const base = "/";
  const mime = { ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".json": "application/json", ".xml": "application/xml", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2", ".ps1": "text/plain; charset=utf-8", ".sh": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8" };
  const server = http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url, "http://localhost");
      if (!url.pathname.startsWith(base)) {
        response.writeHead(404).end("Not found");
        return;
      }
      const relative = decodeURIComponent(url.pathname.slice(base.length)) || "index.html";
      let target = path.resolve(output, relative);
      const inside = path.relative(output, target);
      if (inside.startsWith("..") || path.isAbsolute(inside)) {
        response.writeHead(403).end("Forbidden");
        return;
      }
      if ((await stat(target)).isDirectory()) {
        if (!url.pathname.endsWith("/")) {
          response.writeHead(302, { Location: `${url.pathname}/${url.search}` }).end();
          return;
        }
        target = path.join(target, "index.html");
      }
      if (!(await stat(target)).isFile()) {
        response.writeHead(404).end("Not found");
        return;
      }
      response.writeHead(200, { "Content-Type": mime[path.extname(target)] || "application/octet-stream", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
      if (request.method === "HEAD") response.end();
      else createReadStream(target).on("error", () => response.destroy()).pipe(response);
    } catch {
      const errorPage = await readFile(path.join(output, "404.html")).catch(() => "Not found");
      response.writeHead(404, { "Content-Type": "text/html; charset=utf-8" }).end(errorPage);
    }
  });
  server.on("error", (error) => { console.error(error.message); process.exitCode = 1; });
  const port = Number(process.env.PORT || 4173);
  server.listen(port, "127.0.0.1", () => {
    console.log(`Website: http://127.0.0.1:${port}${base}`);
    console.log(`Docs:    http://127.0.0.1:${port}${base}docs/`);
  });
  let debounce;
  let building = false;
  let pending = false;
  async function rebuild() {
    if (building) { pending = true; return; }
    building = true;
    try { await build(); } catch (error) { console.error(error.message); }
    finally {
      building = false;
      if (pending) { pending = false; void rebuild(); }
    }
  }
  function schedule() { clearTimeout(debounce); debounce = setTimeout(() => void rebuild(), 120); }
  const watchers = [
    watch(source, { recursive: true }, schedule),
    watch(docsSource, { recursive: true }, schedule),
    watch(path.join(docsRoot, ".vitepress", "theme"), { recursive: true }, schedule),
    watch(path.join(docsRoot, ".vitepress"), (_event, filename) => { if (filename === "config.mts") schedule(); }),
    ...installers.map((file) => watch(path.join(root, file), schedule)),
    watch(path.join(root, "CNAME"), schedule),
    ...sharedDocs.map((file) => watch(path.join(docsRoot, file), schedule)),
  ];
  function close() {
    clearTimeout(debounce);
    for (const watcher of watchers) watcher.close();
    server.close();
  }
  process.once("SIGINT", close);
  process.once("SIGTERM", close);
}

if (command === "check") await check();
else if (command === "build") await build();
else if (command === "dev") await serve();
else throw new Error(`Unknown site command: ${command}. Use check, build, or dev.`);
