import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { mkdtemp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { brotliDecompressSync, gunzipSync } from "node:zlib";

import { precompressAssets } from "../scripts/precompress-assets.mjs";

test("precompression preserves assets and produces decodable gzip and Brotli variants", async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "k41-compression-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const assets = join(directory, "assets");
  await mkdir(assets);
  const sources = new Map([
    ["index.html", Buffer.from('<div id="root"></div>'.repeat(100))],
    ["assets/app.js", Buffer.from("export const ready = true;\n".repeat(100))],
    ["assets/app.css", Buffer.from("body { color: red; }\n".repeat(100))],
    ["assets/engine.wasm", Buffer.concat([Buffer.from([0, 97, 115, 109]), Buffer.alloc(4096)])],
  ]);
  for (const [name, source] of sources) {
    await writeFile(join(directory, name), source);
  }
  await writeFile(join(assets, "small.js"), "export {};");
  await writeFile(join(assets, "image.png"), Buffer.alloc(2048));
  await writeFile(join(assets, "random.wasm"), randomBytes(4096));

  assert.equal(await precompressAssets(directory), sources.size * 2);
  for (const [name, source] of sources) {
    const assetPath = join(directory, name);
    assert.deepEqual(await readFile(assetPath), source);
    assert.deepEqual(gunzipSync(await readFile(assetPath + ".gz")), source);
    assert.deepEqual(brotliDecompressSync(await readFile(assetPath + ".br")), source);
  }
  const files = await readdir(assets);
  for (const name of ["small.js", "image.png", "random.wasm"]) {
    assert.ok(!files.includes(name + ".gz"));
    assert.ok(!files.includes(name + ".br"));
  }

  // Repeated builds must neither recompress sidecars nor retain stale variants.
  assert.equal(await precompressAssets(directory), sources.size * 2);
  assert.ok(!(await readdir(assets)).some((name) => /\.(br|gz)\.(br|gz)$/.test(name)));
  await writeFile(join(assets, "app.js"), "export {};");
  await precompressAssets(directory);
  await assert.rejects(readFile(join(assets, "app.js.gz")), { code: "ENOENT" });
  await assert.rejects(readFile(join(assets, "app.js.br")), { code: "ENOENT" });
});
