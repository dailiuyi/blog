/**
 * 标签 → URL 段的映射。
 *
 * 为什么需要它：标签是自由文本，可以直接包含 `/`（例如 `CI/CD`）。
 * Astro 的 `[tag].astro` 只能匹配单个路径段，`/tags/CI/CD/` 会落到两段路径上，
 * 静态生成时会报 "Missing parameter: tag"。所以 URL 里一律使用 slug，
 * 中文标签则退化为可读的 `tag-N`（标签顺序稳定，编号也就稳定）。
 */

export function slugifyTag(tag: string): string {
  const slug = tag
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return slug || 'tag';
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
