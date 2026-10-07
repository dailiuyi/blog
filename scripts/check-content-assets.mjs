#!/usr/bin/env node
/**
 * 内容资产一致性检查（零依赖，仅使用 Node 内置模块）。
 *
 * 用法：node scripts/check-content-assets.mjs
 *
 * 背景：MP3 被 .gitignore 排除（见 HANDOFF.md 第 5 节），CI checkout 里没有 MP3，
 * 只有 LRC 与封面。因此：
 *   - public/media/music 不存在，或整个目录下没有任何 .mp3：打印 SKIP 并以 0 退出。
 *   - 只要存在至少一个 .mp3（本地开发/服务器），就按 music.ts 声明做完整校验，
 *     发现不一致时以 1 退出。
 */

import { readdir, readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const musicSourcePath = path.join(root, 'src', 'data', 'music.ts');
const musicDir = path.join(root, 'public', 'media', 'music');

const SKIP_MESSAGE = 'SKIP: 媒体文件不在仓库中（MP3 由服务器提供）';

const pad2 = (value) => String(value).padStart(2, '0');

async function pathExists(target) {
  try {
    await stat(target);
    return true;
  } catch {
    return false;
  }
}

/** 把 music.ts 里的 /media/music/... URL 转成相对于 public/media/music 的路径。 */
function toMusicRelative(url) {
  const cleaned = String(url).replace(/^\/+/, '');
  const prefix = 'media/music/';
  return cleaned.startsWith(prefix) ? cleaned.slice(prefix.length) : cleaned;
}

/** 从 music.ts 文本解析曲目对象（不 import TS，纯文本解析）。 */
function parseTracks(chunk) {
  const tracks = [];
  const re = /\{\s*title:\s*'([^']*)'([^}]*)\}/g;
  let match;
  while ((match = re.exec(chunk)) !== null) {
    const lrcMatch = match[2].match(/lrc:\s*'([^']+)'/);
    tracks.push({ title: match[1], lrc: lrcMatch ? lrcMatch[1] : null });
  }
  return tracks;
}

/** 解析 musicAlbums：slug、cover、曲目列表。 */
function parseAlbums(source) {
  const slugRe = /slug:\s*'([^']+)'/g;
  const marks = [];
  let match;
  while ((match = slugRe.exec(source)) !== null) {
    marks.push({ slug: match[1], index: match.index });
  }
  if (marks.length === 0) {
    throw new Error('未能从 src/data/music.ts 解析出任何专辑 slug');
  }

  return marks.map((mark, i) => {
    const end = i + 1 < marks.length ? marks[i + 1].index : source.length;
    const chunk = source.slice(mark.index, end);
    const tracks = parseTracks(chunk);
    const titleCount = (chunk.match(/\{\s*title:/g) ?? []).length;
    if (tracks.length === 0 || tracks.length !== titleCount) {
      throw new Error(
        `专辑 ${mark.slug} 曲目解析不一致：regex 得到 ${tracks.length} 首，title 字段 ${titleCount} 个`,
      );
    }
    const cover = chunk.match(/cover:\s*'([^']+)'/);
    return {
      slug: mark.slug,
      tracks,
      cover: cover ? cover[1] : `/media/music/covers/${mark.slug}.webp`,
    };
  });
}

/** 递归收集 public/media/music 下的所有文件，返回相对该目录的路径。 */
async function collectFiles(directory, prefix = '', files = []) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) await collectFiles(path.join(directory, entry.name), relative, files);
    else if (entry.isFile()) files.push(relative);
  }
  return files;
}

async function main() {
  const source = await readFile(musicSourcePath, 'utf8');
  const albums = parseAlbums(source);
  const totalTracks = albums.reduce((total, album) => total + album.tracks.length, 0);

  console.log('内容资产一致性检查');
  console.log(`来源：src/data/music.ts（${albums.length} 张专辑 / ${totalTracks} 首曲目）`);
  console.log('扫描：public/media/music');

  const hasMediaDir = await pathExists(musicDir);
  const files = hasMediaDir ? await collectFiles(musicDir) : [];
  const mp3Files = files.filter((file) => file.toLowerCase().endsWith('.mp3'));

  // 硬要求：CI checkout 没有 MP3，必须 SKIP 且退出 0，绝不能红灯。
  if (!hasMediaDir || mp3Files.length === 0) {
    console.log(SKIP_MESSAGE);
    console.log(
      `摘要：声明 ${albums.length} 张专辑 / ${totalTracks} 首曲目；` +
        (hasMediaDir
          ? `媒体目录存在但没有 MP3（共 ${files.length} 个非 MP3 文件，如 LRC/封面）。`
          : '媒体目录不存在。'),
    );
    console.log('结论：SKIP（退出码 0）');
    return 0;
  }

  const errors = [];
  const declared = new Map();
  let checks = 0;

  const declare = (relative, description) => {
    if (declared.has(relative)) {
      errors.push(`声明重复：public/media/music/${relative} 被 ${declared.get(relative)} 与 ${description} 同时占用`);
    }
    declared.set(relative, description);
  };

  const expect = async (relative, description) => {
    checks += 1;
    if (!(await pathExists(path.join(musicDir, relative)))) {
      errors.push(`缺少${description}：public/media/music/${relative}`);
    }
  };

  for (const album of albums) {
    const albumDir = path.join(musicDir, album.slug);
    if (!(await pathExists(albumDir))) {
      errors.push(`缺少专辑目录：public/media/music/${album.slug}/`);
    }

    for (const [index, track] of album.tracks.entries()) {
      const number = pad2(index + 1);
      const mp3Relative = `${album.slug}/${number}.mp3`;
      const lrcRelative = track.lrc
        ? toMusicRelative(track.lrc)
        : `${album.slug}/${number}.lrc`;
      declare(mp3Relative, `专辑 ${album.slug} 第 ${number} 首（${track.title}）`);
      declare(lrcRelative, `专辑 ${album.slug} 第 ${number} 首歌词（${track.title}）`);
      await expect(mp3Relative, `MP3（专辑 ${album.slug} 第 ${number} 首「${track.title}」）`);
      await expect(lrcRelative, `LRC（专辑 ${album.slug} 第 ${number} 首「${track.title}」）`);
    }

    const coverRelative = toMusicRelative(album.cover);
    declare(coverRelative, `专辑 ${album.slug} 封面`);
    await expect(coverRelative, `封面（专辑 ${album.slug}）`);

    // 播放器列表只显示 44×44，所以每张封面还有一个 96px 缩略图（covers/thumbs/），
    // 由生成脚本从封面派生。它不是 music.ts 里的独立声明，但属于合法资产。
    // 注意：declare() 用的路径没有前导斜杠，所以这里不能按 '/covers/' 匹配。
    const thumbRelative = coverRelative.replace(/(^|\/)covers\//, '$1covers/thumbs/');
    declare(thumbRelative, `专辑 ${album.slug} 封面缩略图`);
    await expect(thumbRelative, `封面缩略图（专辑 ${album.slug}）`);
  }

  const albumSlugs = new Set(albums.map((album) => album.slug));
  for (const entry of await readdir(musicDir, { withFileTypes: true })) {
    if (entry.name.startsWith('.') || entry.name === 'covers') continue;
    if (entry.isDirectory() && !albumSlugs.has(entry.name)) {
      errors.push(`孤儿目录：public/media/music/${entry.name}/（music.ts 未声明）`);
    }
  }

  for (const file of files) {
    const base = path.basename(file);
    if (base.startsWith('.')) continue; // .gitkeep / .DS_Store 等忽略
    checks += 1;
    if (!declared.has(file)) {
      errors.push(`孤儿文件：public/media/music/${file}（music.ts 未声明）`);
    }
  }

  console.log(
    `实际扫描：${files.length} 个文件，其中 MP3 ${mp3Files.length} 个（存在 MP3，执行完整校验）`,
  );
  console.log(`检查项：${checks}`);

  if (errors.length === 0) {
    console.log('结论：PASS（退出码 0）');
    return 0;
  }

  console.log(`不一致明细（${errors.length} 项）：`);
  for (const error of errors) console.log(`  - ${error}`);
  console.log('结论：FAIL（退出码 1）');
  return 1;
}

try {
  process.exitCode = await main();
} catch (error) {
  console.error(`检查脚本自身失败：${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
}
