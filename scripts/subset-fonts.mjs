import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import subsetFont from 'subset-font';

const root = process.cwd();
const output = path.join(root, 'public', 'fonts');
const textRoots = ['src', path.join('public', 'media', 'music')];
const extensions = new Set(['.astro', '.css', '.js', '.ts', '.md', '.lrc']);

async function collectFiles(directory, files = []) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) await collectFiles(full, files);
    else if (extensions.has(path.extname(entry.name))) files.push(full);
  }
  return files;
}

const files = (await Promise.all(textRoots.map((name) => collectFiles(path.join(root, name))))).flat();
const corpus = [...new Set((await Promise.all(files.map((file) => readFile(file, 'utf8')))).join(''))].join('');
const source = await readFile(path.join(root, 'node_modules', '@fontsource', 'lxgw-wenkai', 'files', 'lxgw-wenkai-latin-500-normal.woff2'));
const subset = await subsetFont(source, corpus, { targetFormat: 'woff2' });

await mkdir(output, { recursive: true });
await writeFile(path.join(output, 'lxgw-wenkai-500-subset.woff2'), subset);
// 版权文本统一写成 LF：npm 包内的 LICENSE 是 CRLF，直接复制会让每次构建
// 都产生一整份"行尾变化"的无意义 diff（Windows/CI 之间还会来回抖动）。
async function copyLicense(sourceName, targetName) {
  const text = await readFile(path.join(root, 'node_modules', sourceName, 'LICENSE'), 'utf8');
  await writeFile(path.join(output, targetName), text.replace(/\r\n/g, '\n'));
}
await copyLicense('@fontsource/lxgw-wenkai', 'OFL-LXGW-WenKai.txt');
await copyLicense('@fontsource-variable/noto-sans-sc', 'OFL-Noto-Sans-SC.txt');
console.log(`Generated LXGW WenKai subset: ${(subset.length / 1024).toFixed(1)} KiB, ${corpus.length} glyph inputs`);
