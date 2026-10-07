/**
 * 标签 / 系列名 → URL 段的映射。
 *
 * 为什么需要它：标签和系列名是自由文本，可以直接包含 `/`（例如 `CI/CD`）。
 * Astro 的 `[tag].astro` 只能匹配单个路径段，`/tags/CI/CD/` 会落到两段路径上，
 * 静态生成时会报 "Missing parameter: tag"。所以 URL 里一律使用 slug。
 *
 * 纯中文名的回退策略在标签与系列上**故意不同**：
 * - 标签：`tag` + 序号（`tag-4`）。这些 URL 已经上线并被索引，改成哈希会直接破坏
 *   既有链接，所以保持原样。
 * - 系列：`series-<确定性短哈希>`。系列是新增功能，没有历史链接包袱；而回退成
 *   `series` 会让 URL 变成 `/series/series/`（与路由同名，看起来像 bug），
 *   且第二个中文系列会变 `series-2`、插入顺序一变就换号。
 * 站点不引拼音表：那是新依赖，对读者价值有限。
 */

/** FNV-1a 的 32 位变体，输出 6 位 base36。确定性、无依赖。 */
export function shortHash(value: string): string {
  let hash = 0x811c9dc5;
  for (const char of value) {
    hash ^= char.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(36).padStart(6, '0').slice(-6);
}

/**
 * `hashFallback: false`（标签用）时空值回退为固定词，由调用方去重成 `tag-N`；
 * `hashFallback: true`（系列用）时回退为 `<前缀>-<短哈希>`，与插入顺序无关。
 */
export function slugifyName(value: string, fallbackPrefix: string, hashFallback = false): string {
  const trimmed = value.trim();
  const slug = trimmed
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  if (slug) return slug;
  if (!trimmed) return fallbackPrefix;
  return hashFallback ? `${fallbackPrefix}-${shortHash(trimmed)}` : fallbackPrefix;
}

export function slugifyTag(tag: string): string {
  return slugifyName(tag, 'tag');
}

/** 标签集合 → slug → 原标签。顺序稳定，因此 slug 也稳定。 */
export function buildTagSlugMap(tags: Iterable<string>): Map<string, string> {
  const map = new Map<string, string>();
  const used = new Set<string>();
  for (const tag of tags) {
    let slug = slugifyTag(tag);
    if (used.has(slug)) {
      let suffix = 2;
      while (used.has(`${slug}-${suffix}`)) suffix += 1;
      slug = `${slug}-${suffix}`;
    }
    used.add(slug);
    map.set(tag, slug);
  }
  return map;
}
