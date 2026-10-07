/**
 * 系列 / 连载分组。
 *
 * 为什么需要它：blog frontmatter 里的 `series` / `seriesOrder` 都是可选字段，
 * 站内绝大多数文章没有系列。把"有没有系列 / 第几篇 / 上一篇是谁"这类判断收拢成
 * 纯函数后，页面只负责渲染，未定义系列的文章不会渲染空壳，也不会报错。
 *
 * slug 策略与 `src/lib/tag-slug.ts` 共用同一套实现（`slugifyName`），保证标签与系列
 * 的 URL 行为一致。纯中文名（如「静态博客发布链路」）无法转成 ASCII，回退为
 * `series-<确定性短哈希>`；而不是与路由同名的 `series`（`/series/series/` 看起来像 bug），
 * 也不会因为插入顺序变化而变号。
 */
import { slugifyName } from './tag-slug.ts';

/** frontmatter 中与系列相关的可选字段。 */
export interface SeriesFields {
  series?: string;
  seriesOrder?: number;
}

/** 排序与分组只需要这些字段，因此不依赖 Astro 的具体集合类型。 */
export interface SeriesPostLike {
  id: string;
  data: SeriesFields & { published: Date };
}

export interface SeriesEntry<T extends SeriesPostLike> {
  name: string;
  slug: string;
  /** 已按阅读顺序排好（见 sortSeriesPosts）。 */
  posts: T[];
}

export interface SeriesMembership<T extends SeriesPostLike> {
  name: string;
  slug: string;
  /** 1 开始的位置。 */
  position: number;
  total: number;
  previous: T | null;
  next: T | null;
}

/** 系列名 → URL 段。纯中文/符号名回退为 `series-<短哈希>`，由 buildSeriesSlugMap 去重。 */
export function slugifySeries(name: string): string {
  return slugifyName(name, 'series', true);
}

/** 系列名集合 → slug → 原系列名。顺序稳定，因此 slug 也稳定。 */
export function buildSeriesSlugMap(names: Iterable<string>): Map<string, string> {
  const map = new Map<string, string>();
  const used = new Set<string>();
  for (const name of names) {
    let slug = slugifySeries(name);
    if (used.has(slug)) {
      let suffix = 2;
      while (used.has(`${slug}-${suffix}`)) suffix += 1;
      slug = `${slug}-${suffix}`;
    }
    used.add(slug);
    map.set(name, slug);
  }
  return map;
}

/** 空白字符串一律视为"没有系列"。 */
export function seriesName(data: SeriesFields): string | null {
  const name = data.series?.trim();
  return name ? name : null;
}

/**
 * 阅读顺序：先按 seriesOrder 升序。
 *
 * 缺失 seriesOrder 的文章排在已编号文章之后，组内按 published 升序（即连载的
 * 时间顺序），最后用 id 兜底，保证同一份数据每次构建结果一致。
 * 因此"某系列全部文章都没写 seriesOrder"时，退化为纯时间顺序，而不是报错。
 */
export function sortSeriesPosts<T extends SeriesPostLike>(posts: T[]): T[] {
  return [...posts].sort((a, b) => {
    const orderA = a.data.seriesOrder ?? Number.POSITIVE_INFINITY;
    const orderB = b.data.seriesOrder ?? Number.POSITIVE_INFINITY;
    if (orderA !== orderB) return orderA - orderB;
    const time = a.data.published.valueOf() - b.data.published.valueOf();
    if (time !== 0) return time;
    return a.id.localeCompare(b.id);
  });
}

/** 至少含 1 篇文章的系列；系列名按中文排序，slug 稳定且互不冲突。 */
export function buildSeriesIndex<T extends SeriesPostLike>(posts: T[]): SeriesEntry<T>[] {
  const groups = new Map<string, T[]>();
  for (const post of posts) {
    const name = seriesName(post.data);
    if (!name) continue;
    const group = groups.get(name);
    if (group) group.push(post);
    else groups.set(name, [post]);
  }
  const names = [...groups.keys()].sort((a, b) => a.localeCompare(b, 'zh-CN'));
  const slugByName = buildSeriesSlugMap(names);
  return names.map((name) => ({
    name,
    slug: slugByName.get(name) ?? slugifySeries(name),
    posts: sortSeriesPosts(groups.get(name) ?? []),
  }));
}

/** post.id → 位置与前后篇。不属于任何系列的文章不在 Map 里。 */
export function buildSeriesMembershipMap<T extends SeriesPostLike>(
  index: SeriesEntry<T>[],
): Map<string, SeriesMembership<T>> {
  const map = new Map<string, SeriesMembership<T>>();
  for (const series of index) {
    series.posts.forEach((post, i) => {
      map.set(post.id, {
        name: series.name,
        slug: series.slug,
        position: i + 1,
        total: series.posts.length,
        previous: series.posts[i - 1] ?? null,
        next: series.posts[i + 1] ?? null,
      });
    });
  }
  return map;
}
