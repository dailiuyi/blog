/**
 * 站内搜索的共享逻辑：构建时生成索引，浏览器端做匹配与摘录。
 *
 * 设计边界：
 * - 不引入任何依赖。索引是构建期生成的静态 JSON 文件。
 * - 以中文为主，所以候选集是逐字的，超集必须能被查询"包住"（"工程" 能搜到 "软件工程"）。
 * - 单个字符的查询不做模糊匹配，只做子串匹配，否则中文会把整站都命中。
 */

export interface SearchEntry {
  title: string;
  description: string;
  href: string;
  meta: string;
  text: string;
}

export interface SearchHit {
  entry: SearchEntry;
  snippet: string;
}

const snippetLength = 76;
const maxResults = 12;

/** 去掉 Markdown 语法，得到用于搜索与摘录的纯文本。 */
export function toPlainText(markdown: string): string {
  return markdown
    .replace(/\r\n?/g, '\n')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/~~~[\s\S]*?~~~/g, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/^\s{0,3}(?:[-*+]|\d+\.)\s+/gm, '')
    .replace(/^\s{0,3}(?:[-*_]\s*){3,}$/gm, ' ')
    .replace(/[*_~]/g, '')
    .replace(/\|/g, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&[a-z]+;|&#\d+;/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function bigrams(value: string): string[] {
  const ramps = new Set<string>();
  for (let index = 0; index < value.length - 1; index += 1) ramps.add(value.slice(index, index + 2));
  return [...ramps].map((ramp) => ramp.toLowerCase());
}

/** 在正文里围绕命中位置取一段上下文；没有子串命中时退回到开头。 */
function excerpt(text: string, query: string): string {
  if (!text) return '';
  const at = query ? text.toLowerCase().indexOf(query) : -1;
  if (at < 0) return text.slice(0, snippetLength) + (text.length > snippetLength ? '…' : '');
  const start = Math.max(0, at - Math.floor(snippetLength / 3));
  const end = Math.min(text.length, start + snippetLength);
  return `${start > 0 ? '…' : ''}${text.slice(start, end)}${end < text.length ? '…' : ''}`;
}

/**
 * 对已加载的索引做一次查询。
 * 得分顺序：标题命中 > 摘要/元信息命中 > 正文子串命中（越靠前越重）。
 * 只有在完全没有子串命中时，才启用二元组模糊匹配，且要求覆盖率过半，
 * 否则「不存在的关键词zzz」这类查询会因零散字对而返回一堆无关结果。
 */
export function searchEntries(entries: SearchEntry[], rawQuery: string): SearchHit[] {
  const query = rawQuery.trim().toLowerCase();
  if (!query) return [];
  // 单个汉字几乎出现在每篇文章里，搜出来只会是一屏噪音；要求至少两个字符。
  if (/^[\u2e80-\u9fff\uf900-\ufaff]$/.test(query)) return [];
  const grams = query.length > 1 ? bigrams(query) : [];
  const scored: Array<{ entry: SearchEntry; score: number }> = [];

  for (const entry of entries) {
    const title = entry.title.toLowerCase();
    const meta = `${entry.description} ${entry.meta}`.toLowerCase();
    const text = (entry.text ?? '').toLowerCase();
    let score = 0;

    if (title.includes(query)) score += 100;
    if (meta.includes(query)) score += 45;
    const inText = text.indexOf(query);
    if (inText >= 0) score += 30 + Math.max(0, 10 - inText / 400);

    if (score === 0 && grams.length > 1) {
      let matched = 0;
      for (const gram of grams) if (title.includes(gram) || meta.includes(gram) || text.includes(gram)) matched += 1;
      const coverage = matched / grams.length;
      if (coverage >= 0.6) score = coverage * 20;
    }

    if (score > 0) scored.push({ entry, score });
  }

  return scored
    .sort((a, b) => b.score - a.score)
    .slice(0, maxResults)
    .map(({ entry }) => ({ entry, snippet: excerpt(entry.text ?? entry.description, query) }));
}
