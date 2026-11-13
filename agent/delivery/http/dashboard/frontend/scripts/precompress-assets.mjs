import { readdir, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";
import { brotliCompress, constants, gzip } from "node:zlib";

const gzipAsync = promisify(gzip);
const brotliAsync = promisify(brotliCompress);
const compressibleExtension = /\.(?:css|html|js|json|svg|txt|wasm)$/i;
const minimumSize = 500;

export async function precompressAssets(directory) {
  let count = 0;
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const assetPath = join(directory, entry.name);
    if (entry.isDirectory()) {
      count += await precompressAssets(assetPath);
      continue;
    }
    if (!entry.isFile() || !compressibleExtension.test(entry.name)) {
      continue;
    }
    const source = await readFile(assetPath);
    const variants = source.length < minimumSize ? [] : await Promise.all([
      brotliAsync(source, {
        params: { [constants.BROTLI_PARAM_QUALITY]: 11 },
      }),
      gzipAsync(source, { level: 9 }),
    ]);
    for (const [index, suffix] of [".br", ".gz"].entries()) {
      const compressed = variants[index];
      if (compressed && compressed.length < source.length) {
        await writeFile(assetPath + suffix, compressed);
        count += 1;
      } else {
        await rm(assetPath + suffix, { force: true });
      }
    }
  }
  return count;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const directory = fileURLToPath(new URL("../../static/", import.meta.url));
  const count = await precompressAssets(directory);
  console.log(`Created ${count} precompressed dashboard asset variants.`);
}
