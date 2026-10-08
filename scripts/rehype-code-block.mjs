/**
 * rehype 插件：把正文里的每个 `<pre>` 包成带「语言标签 + 复制按钮」的代码块。
 *
 * 为什么是 rehype 插件而不是 `markdown.components: { pre }`：
 *   该配置项只存在于 MDX 的 `<Content components={...} />`，Astro 7 的 markdown schema 里
 *   根本没有 `components` 键（未知键会被静默丢弃），.md 的 `Content` 组件也完全忽略 props。
 *   .md 唯一可行的注入点是处理器插件管线（本仓库已是 @astrojs/markdown-remark 的 unified）。
 *
 * 产物结构（类名与 src/components/common/CodeBlock.astro 的契约一致，
 * 样式与客户端逻辑由 src/components/common/CodeCopy.astro 提供，布局里渲染一次即可）：
 *
 *   <div class="code-block" data-code-block>
 *     <div class="code-block-bar">
 *       <span class="code-block-lang">python</span>                    ← 取不到语言时整段省略
 *       <button type="button" class="code-block-copy" data-code-copy aria-label="复制 python 代码">
 *         <span data-code-copy-label>复制</span>
 *       </button>
 *       <span class="code-block-status" role="status" aria-live="polite" data-code-status></span>
 *     </div>
 *     <pre class="astro-code …">…</pre>                              ← 原节点整体原样保留
 *   </div>
 *
 * 设计要点：
 *   - 零依赖：不使用 unist-util-visit，自己遍历 hast children。
 *   - 原 `<pre>` 节点对象原封不动塞进包装层，class / style / tabindex / 高亮子节点全部保留，
 *     Shiki 高亮与换行样式不受影响。
 *   - 没有代码块（或没有 `<pre><code>`）的文章输出与接入前逐字节一致：不改树、不注入样式。
 *   - 幂等：遇到已带 data-code-block 的容器不再重复包装。
 *
 * 注册方式（astro.config.mjs 的 markdown.processor，由 lead 负责）：
 *   import rehypeCodeBlock from './scripts/rehype-code-block.mjs';
 *   processor: unified({
 *     rehypePlugins: [rehypeSlug, [rehypeAutolinkHeadings, {...}], rehypeCodeBlock],
 *   })
 */

/** 与 CodeBlock.astro 保持一致的展示用别名（仅影响标签文字）。 */
const LANGUAGE_ALIASES = { plaintext: 'text', shell: 'bash', sh: 'bash', yml: 'yaml', node: 'javascript', md: 'markdown' };
/** 兜底：从 class="language-xxx" 里取语言（本仓库实际产物用 data-language，两者都兼容）。 */
const CLASS_LANGUAGE_PATTERN = /(?:^|\s)language-([\w+#.-]+)/;
/** `<pre>` 的直接子元素里必须有 `<code>`，避免误包非代码用途的 pre。 */
const hasCodeChild = (node) => Array.isArray(node.children) && node.children.some((child) => child.type === 'element' && child.tagName === 'code');

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

/**
 * @param {unknown} value
 * @returns {string}
 */
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (character) => ESCAPES[character]);

/**
 * @param {any} node
 * @returns {string[]}
 */
const classNamesOf = (node) => {
  const value = node?.properties?.className;
  if (Array.isArray(value)) return value.map((entry) => String(entry));
  if (typeof value === 'string') return value.split(/\s+/).filter(Boolean);
  return [];
};

/**
 * 语言标签：优先 data-language（本仓库实际形态），回退 class 里的 language-xxx；取不到返回空串。
 * @param {any} pre
 * @returns {string}
 */
export function languageOf(pre) {
  const properties = pre?.properties ?? {};
  const raw = properties['data-language'] ?? properties.dataLanguage ?? classNamesOf(pre).map((name) => CLASS_LANGUAGE_PATTERN.exec(name)?.[1]).find(Boolean);
  const language = String(raw ?? '').trim().toLowerCase();
  return language ? LANGUAGE_ALIASES[language] ?? language : '';
}

/**
 * 构造包装层：bar（语言标签 + 复制按钮 + 屏幕阅读器状态）+ 原 pre。
 * @param {any} pre
 * @returns {any}
 */
export function wrapCodeBlock(pre) {
  const label = languageOf(pre);
  const barChildren = [];
  barChildren.push({
    type: 'element',
    tagName: 'div',
    properties: { className: ['code-block-dots'], 'aria-hidden': 'true' },
    children: [
      { type: 'element', tagName: 'span', properties: { className: ['dot', 'dot-red'] }, children: [] },
      { type: 'element', tagName: 'span', properties: { className: ['dot', 'dot-yellow'] }, children: [] },
      { type: 'element', tagName: 'span', properties: { className: ['dot', 'dot-green'] }, children: [] },
    ],
  });
  if (label) {
    barChildren.push({ type: 'element', tagName: 'span', properties: { className: ['code-block-lang'] }, children: [{ type: 'text', value: label }] });
  }
  barChildren.push({
    type: 'element',
    tagName: 'button',
    properties: { type: 'button', className: ['code-block-copy'], 'data-code-copy': '', 'aria-label': label ? `复制 ${label} 代码` : '复制代码' },
    children: [{ type: 'element', tagName: 'span', properties: { 'data-code-copy-label': '' }, children: [{ type: 'text', value: '复制' }] }],
  });
  barChildren.push({ type: 'element', tagName: 'span', properties: { className: ['code-block-status'], role: 'status', 'aria-live': 'polite', 'data-code-status': '' }, children: [] });
  return {
    type: 'element',
    tagName: 'div',
    properties: { className: ['code-block'], 'data-code-block': '' },
    children: [{ type: 'element', tagName: 'div', properties: { className: ['code-block-bar'] }, children: barChildren }, pre],
  };
}

/**
 * 遍历 hast 树，把每个 `<pre><code>` 替换成包装层；返回包装数量。
 * @param {any} tree
 * @returns {number}
 */
export function applyCodeBlocks(tree) {
  if (!tree || !Array.isArray(tree.children)) return 0;
  let wrapped = 0;

  const walk = (parent) => {
    for (let index = 0; index < parent.children.length; index += 1) {
      const node = parent.children[index];
      if (!node || node.type !== 'element') continue;
      if (node.tagName === 'pre') {
        if (!hasCodeChild(node)) continue;
        parent.children[index] = wrapCodeBlock(node);
        wrapped += 1;
        continue;
      }
      // 已包装的容器不再下探，保证重复执行时幂等。
      if (node.properties?.['data-code-block'] !== undefined) continue;
      if (Array.isArray(node.children)) walk(node);
    }
  };

  walk(tree);
  return wrapped;
}

/** 默认导出：rehype 插件工厂（unified 约定）。 */
export default function rehypeCodeBlock() {
  return (tree) => {
    applyCodeBlocks(tree);
  };
}

export { escapeHtml, hasCodeChild };
