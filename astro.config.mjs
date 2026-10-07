import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import { unified } from '@astrojs/markdown-remark';
import remarkRepoCard from './scripts/remark-repo-card.mjs';
import rehypeCodeBlock from './scripts/rehype-code-block.mjs';
import rehypeSlug from 'rehype-slug';
import rehypeAutolinkHeadings from 'rehype-autolink-headings';

export default defineConfig({
  site: 'https://elma-gohan.xyz',
  output: 'static',
  // /prototype/ 是设计实验区，不进 sitemap；正式路由、标签页与归档页保留。
  integrations: [sitemap({ filter: (page) => !new URL(page).pathname.startsWith('/prototype/') })],
  markdown: {
    shikiConfig: { theme: 'github-dark-default', wrap: true },
    // Astro 7 起顶层 markdown.remarkPlugins / rehypePlugins 已废弃，插件必须挂到 processor 上；
    // 注意它们不会被自动合并，漏掉就会静默失效（例如标题 id 与锚点直接消失）。
    processor: unified({
      // remark 阶段：::repo{repo="owner/name"} 渲染成仓库卡片。
      // injectStyles:false —— 卡片样式由 BaseLayout 全站注入唯一一份，
      // 否则一页渲染多篇文档时会各带一份（约 1.4KB/份）。
      remarkPlugins: [[remarkRepoCard, { injectStyles: false }]],
      // rehype 阶段：标题 id + 可点击锚点；代码块包装（复制按钮）。
      // 代码块插件放最后：它只改写 <pre>，不碰标题，对 slug/锚点零干扰。
      rehypePlugins: [
        rehypeSlug,
        [rehypeAutolinkHeadings, { behavior: 'append', properties: { className: ['heading-anchor'], ariaLabel: '本节链接' } }],
        rehypeCodeBlock,
      ],
    }),
  },
});
