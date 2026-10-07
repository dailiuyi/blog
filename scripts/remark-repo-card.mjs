/**
 * remark 插件：GitHub 仓库卡片（零依赖，仅使用原生 JS）。
 *
 * 语法（必须单独成段）：
 *   ::repo{repo="owner/name"}
 *   ::repo{repo="owner/name" desc="一句话描述" lang="C" stars="128"}
 *
 * 属性：
 *   - repo  必填，owner/name，只允许 GitHub 合法字符；不合法时该段落原样保留（并打印构建警告）。
 *   - desc  可选，覆盖描述；不提供时只显示仓库名与跳转。
 *   - lang  可选，语言标签，例如 C / TypeScript / Astro。
 *   - stars 可选，手动指定的 star 数（离线、可复现）；未提供时卡片只显示「GitHub ↗」。
 *
 * 设计要点：
 *   - 不联网：构建期绝不请求 GitHub API，star 数只来自显式属性。
 *   - 不引入任何依赖：不使用 unified/unist-util-visit，自己遍历 mdast children。
 *   - 段落 → html 节点（<a class="repo-card">）；默认每个文档注入一份 <style data-repo-card>，
 *     样式通过 CSS 变量（src/styles/tokens.css）取值，跟随明暗主题。
 *   - 没有使用该语法的文章输出与接入前完全一致。
 *
 * 选项（都可以省略）：
 *   injectStyles: false  不注入 <style>，由调用方（例如 BaseLayout.astro 渲染 REPO_CARD_CSS）
 *                        在页面里注入唯一一份；适合一页渲染多篇文档的列表页，避免每篇一份重复样式。
 *
 * Astro 注册方式（astro.config.mjs，由 lead 负责）——以下三种写法都支持：
 *   import { unified } from '@astrojs/markdown-remark';
 *   import remarkRepoCard, { REPO_CARD_CSS } from './scripts/remark-repo-card.mjs';
 *   unified({ remarkPlugins: [remarkRepoCard] })                              // 默认注入样式
 *   unified({ remarkPlugins: [[remarkRepoCard, { injectStyles: false }]] })   // 标准 unified 传参写法
 *   unified({ remarkPlugins: [remarkRepoCard({ injectStyles: false })] })     // 先当工厂调用（同样有效）
 */

/** 整段匹配形式：::repo{...}（花括号内不允许再出现花括号）。 */
const DIRECTIVE_PATTERN = /^::repo\s*\{([^{}]*)\}\s*$/;
/** 粗略判断是否想写指令（用于区分「畸形指令」与「普通正文」）。 */
const DIRECTIVE_PREFIX = /^::repo\b/;
/** 引号开合映射：直引号与弯引号都接受（smartypants 会把直引号变成弯引号）。 */
const QUOTE_PAIRS = {
  '"': ['"', '”', '“'],
  '“': ['”', '"', '“'],
  '”': ['”', '"', '“'],
  "'": ["'", '’', '‘'],
  '‘': ['’', "'", '‘'],
  '’': ['’', "'", '‘'],
};

/** GitHub 用户名：字母数字与连字符，不以连字符开头/结尾，最长 39。 */
const OWNER_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$/;
/** 仓库名：字母数字、点、下划线、连字符（允许 .github 这类前导点），且至少含一个字母数字。 */
const NAME_PATTERN = /^(?=.*[A-Za-z0-9])[A-Za-z0-9._-]+$/;

/** 卡片样式：与 src/components/common/ProjectPreview.astro 同一套语言（tokens.css 变量）。 */
export const REPO_CARD_CSS = [
  '.repo-card{display:flex;align-items:flex-start;gap:.9rem;min-width:0;margin:1.75rem 0;padding:1rem 1.15rem;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface-2);color:var(--text);transition:border-color .2s ease,transform .2s var(--ease)}',
  '.repo-card:hover{border-color:color-mix(in srgb,var(--accent) 45%,var(--border));transform:translateY(-2px)}',
  '.repo-card-mark{flex:none;width:20px;height:20px;margin-top:.15rem;color:var(--muted)}',
  '.repo-card-body{display:grid;gap:.3rem;min-width:0}',
  '.repo-card-name{font:500 .82rem/1.5 var(--font-mono);color:var(--muted);overflow-wrap:anywhere}',
  '.repo-card-name strong{color:var(--strong);font-weight:600}',
  '.repo-card-desc{font-size:.92rem;line-height:1.7;color:color-mix(in srgb,var(--text) 80%,var(--muted));overflow-wrap:anywhere}',
  '.repo-card-meta{display:flex;flex-wrap:wrap;gap:.85rem;font:600 .68rem/1.5 var(--font-mono);letter-spacing:.08em;text-transform:uppercase;color:var(--accent-deep)}',
  '.repo-card-cta{flex:none;margin-left:auto;align-self:center;white-space:nowrap;font:600 .68rem/1 var(--font-mono);letter-spacing:.08em;text-transform:uppercase;color:var(--accent-deep)}',
  '.repo-card-error{margin:1.5rem 0;padding:.75rem 1rem;border:1px dashed var(--border);border-radius:var(--radius-sm);color:var(--muted);font:500 .8rem/1.6 var(--font-mono)}',
  '@media(max-width:600px){.repo-card{flex-wrap:wrap;gap:.7rem;padding:.9rem 1rem}.repo-card-cta{margin-left:0;width:100%}}',
].join('');

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

/**
 * @param {unknown} value
 * @returns {string}
 */
export function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ESCAPES[char]);
}

/**
 * 解析单独成段的一行文本是否为仓库卡片指令。
 * @param {string} text
 * @returns {{ ok: true, repo: string, owner: string, name: string, desc: string, lang: string, stars: string, warnings: string[] }
 *   | { ok: false, status: 'not-directive' | 'syntax' | 'missing-repo' | 'bad-repo', reason: string }}
 */
export function parseRepoDirective(text) {
  const source = String(text).trim();
  if (!DIRECTIVE_PREFIX.test(source)) {
    return { ok: false, status: 'not-directive', reason: '不是 ::repo 指令' };
  }

  const match = DIRECTIVE_PATTERN.exec(source);
  if (!match) {
    return { ok: false, status: 'syntax', reason: '指令必须写成单独一段的 ::repo{...}，且属性引号必须闭合' };
  }

  const attributes = parseAttributes(match[1]);
  if (!attributes) {
    return { ok: false, status: 'syntax', reason: '属性解析失败（引号未闭合或存在多余字符）' };
  }

  const normalized = normalizeCard(attributes);
  if (!normalized.ok) return normalized;

  const known = new Set(['repo', 'desc', 'lang', 'stars']);
  for (const key of Object.keys(attributes)) {
    if (!known.has(key)) normalized.warnings.push(`忽略未知属性 ${key}`);
  }
  return normalized;
}

/**
 * 校验并规范化卡片参数（Markdown 指令与 .astro 组件共用）。
 * @param {{ repo?: unknown, desc?: unknown, lang?: unknown, stars?: unknown }} props
 * @returns {{ ok: true, repo: string, owner: string, name: string, desc: string, lang: string, stars: string, warnings: string[] }
 *   | { ok: false, status: 'missing-repo' | 'bad-repo', reason: string }}
 */
export function normalizeCard(props) {
  const rawRepo = props?.repo;
  if (rawRepo === undefined || rawRepo === null || String(rawRepo).trim() === '') {
    return { ok: false, status: 'missing-repo', reason: '缺少 repo 属性' };
  }

  const repo = String(rawRepo).trim();
  const segments = repo.split('/');
  const owner = segments[0] ?? '';
  const name = segments[1] ?? '';
  const validOwner = owner.length <= 39 && OWNER_PATTERN.test(owner);
  const validName = name.length <= 100 && NAME_PATTERN.test(name);
  if (segments.length !== 2 || !validOwner || !validName) {
    return { ok: false, status: 'bad-repo', reason: `repo 必须是合法的 owner/name（收到 ${JSON.stringify(repo)}）` };
  }

  const warnings = [];
  const rawStars = collapse(props?.stars).replace(/[\s,]/g, '');
  let stars = '';
  if (rawStars) {
    if (/^\d{1,7}$/.test(rawStars)) stars = String(Number(rawStars));
    else warnings.push(`忽略非法 stars 值 ${JSON.stringify(String(props.stars))}（只接受数字）`);
  }

  return {
    ok: true,
    repo: `${owner}/${name}`,
    owner,
    name,
    desc: collapse(props?.desc).slice(0, 300),
    lang: collapse(props?.lang).slice(0, 24),
    stars,
    warnings,
  };
}

/**
 * 渲染入口：一步完成「校验 + 渲染」，并把失败原因带回来，供 .astro 组件显示精确提示。
 * Markdown 路径不走这里（applyRepoCards 已持有 normalizeCard 的结果，直接调用 cardHtml）。
 * @param {{ repo?: unknown, desc?: unknown, lang?: unknown, stars?: unknown }} props
 * @returns {{ html: string, error: '' } | { html: null, error: string }}
 */
export function renderRepoCard(props) {
  const card = normalizeCard(props);
  return card.ok ? { html: cardHtml(card), error: '' } : { html: null, error: card.reason };
}

/**
 * 遍历 mdast 树，把 `::repo{...}` 段落替换成卡片 html 节点。
 * @param {any} tree
 * @param {(message: string) => void} [report]
 * @param {{ injectStyles?: boolean }} [options] injectStyles=false 时不注入 <style>，由调用方注入唯一一份
 * @returns {number} 成功转换的卡片数
 */
export function applyRepoCards(tree, report = () => {}, options = {}) {
  if (!tree || !Array.isArray(tree.children)) return 0;
  const injectStyles = options?.injectStyles !== false;
  let converted = 0;

  const replaceParagraph = (node) => {
    const inline = node.children ?? [];
    const text = inline.map((child) => (child.type === 'text' ? child.value : '')).join('');
    if (!DIRECTIVE_PREFIX.test(text.trim())) return null;
    const line = node.position?.start?.line;
    const where = line ? `（第 ${line} 行）` : '';
    if (inline.some((child) => child.type !== 'text')) {
      report(`[remark-repo-card] ${where} ::repo 指令不支持行内格式与原始 HTML（例如 **粗体** 或 <b>），已按原文保留。`);
      return null;
    }
    const parsed = parseRepoDirective(text);
    if (!parsed.ok) {
      report(`[remark-repo-card] ${where} 指令无效：${parsed.reason}；已按原文保留，未生成链接。`);
      return null;
    }
    for (const warning of parsed.warnings) report(`[remark-repo-card] ${where} ${warning}。`);
    return { type: 'html', value: cardHtml(parsed) };
  };

  const walk = (parent) => {
    for (let index = 0; index < parent.children.length; index += 1) {
      const node = parent.children[index];
      if (node.type === 'paragraph') {
        const replacement = replaceParagraph(node);
        if (replacement) {
          parent.children[index] = replacement;
          converted += 1;
        }
        continue;
      }
      if (Array.isArray(node.children)) walk(node);
    }
  };

  walk(tree);

  // 每个文档最多注入一份样式；injectStyles=false 时完全交给调用方（见 REPO_CARD_CSS）。
  if (converted > 0 && injectStyles && !hasInjectedStyle(tree)) {
    tree.children.unshift({ type: 'html', value: `<style data-repo-card>${REPO_CARD_CSS}</style>` });
  }
  return converted;
}

/**
 * 默认导出：remark 插件。
 *
 * 三种调用写法都成立（见文件头注释）：
 *   [remarkRepoCard]                            unified 以 attacher 形式无参调用 → 默认注入样式
 *   [[remarkRepoCard, { injectStyles: false }]] 标准 unified 传参写法
 *   [remarkRepoCard({ injectStyles: false })]   先当工厂调用；返回的函数被 unified 再当 attacher 无参调用时
 *                                               会返回自身，因此不会静默失效（这是统一管线的常见坑）
 * @param {{ injectStyles?: boolean }} [options]
 */
export default function remarkRepoCard(options) {
  const settings = { injectStyles: options?.injectStyles !== false };
  const transformer = (tree) => {
    // tree 为 undefined 说明这次是 attacher 调用（不是渲染调用）：把自身交回 unified。
    if (tree === undefined) return transformer;
    applyRepoCards(tree, (message) => console.warn(message), settings);
    return undefined;
  };
  return transformer;
}

/**
 * 生成卡片 HTML（Markdown 指令与 .astro 组件共用）。
 * 入参必须是 normalizeCard 成功分支的返回值（即 card.ok === true 的对象）。
 * @param {{ repo: string, owner: string, name: string, desc: string, lang: string, stars: string }} card
 * @returns {string}
 */
export function cardHtml(card) {
  const href = `https://github.com/${card.owner}/${card.name}`;
  const desc = card.desc ? `<span class="repo-card-desc">${escapeHtml(card.desc)}</span>` : '';
  const meta = [
    card.lang ? `<span class="repo-card-lang">${escapeHtml(card.lang)}</span>` : '',
    card.stars ? `<span class="repo-card-stars">★ ${escapeHtml(card.stars)}</span>` : '',
  ].join('');
  return (
    `<a class="repo-card" href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer" data-repo="${escapeHtml(card.repo)}">` +
    `<svg class="repo-card-mark" viewBox="0 0 16 16" width="20" height="20" aria-hidden="true" focusable="false"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/></svg>` +
    `<span class="repo-card-body"><span class="repo-card-name">${escapeHtml(card.owner)}/<strong>${escapeHtml(card.name)}</strong></span>${desc}` +
    (meta ? `<span class="repo-card-meta">${meta}</span>` : '') +
    `</span><span class="repo-card-cta">GitHub ↗</span></a>`
  );
}

/**
 * 解析 `key="value"` 列表，遇到非法字符或未闭合引号返回 null。
 *
 * 注意：Astro 默认开启 remark-smartypants，它在插件运行前就把直引号变成弯引号
 * （"…" → “…”），所以这里必须同时接受直引号与弯引号，否则合法语法在构建中会失效。
 */
function parseAttributes(source) {
  const attributes = {};
  let index = 0;
  while (index < source.length) {
    while (index < source.length && /[\s,]/.test(source[index])) index += 1;
    if (index >= source.length) break;

    const key = /^[A-Za-z][A-Za-z0-9-]*/.exec(source.slice(index));
    if (!key) return null;
    index += key[0].length;

    while (index < source.length && /\s/.test(source[index])) index += 1;
    if (source[index] !== '=') return null;
    index += 1;
    while (index < source.length && /\s/.test(source[index])) index += 1;

    const closer = QUOTE_PAIRS[source[index]];
    if (!closer) return null;
    index += 1;
    let end = -1;
    for (const candidate of closer) {
      const found = source.indexOf(candidate, index);
      if (found !== -1 && (end === -1 || found < end)) end = found;
    }
    if (end === -1) return null;
    attributes[key[0].toLowerCase()] = source.slice(index, end);
    index = end + 1;
  }
  return attributes;
}

function collapse(value) {
  return value === undefined || value === null ? '' : String(value).replace(/\s+/g, ' ').trim();
}

function hasInjectedStyle(tree) {
  return tree.children.some(
    (node) => node.type === 'html' && typeof node.value === 'string' && node.value.includes('<style data-repo-card>'),
  );
}
