import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import rehypeSlug from 'rehype-slug';
import rehypeAutolinkHeadings from 'rehype-autolink-headings';

export default defineConfig({
  site: 'https://elma-gohan.xyz',
  output: 'static',
  // /prototype/ 是设计实验区，不进 sitemap；正式路由、标签页与归档页保留。
  integrations: [sitemap({ filter: (page) => !new URL(page).pathname.startsWith('/prototype/') })],
  markdown: {
    shikiConfig: { theme: 'github-dark-default', wrap: true },
    // 模板字符串写法是 Astro 官方支持的插件写法，避免额外的 import 解析问题。
    // 注意：GFM（表格/任务列表/删除线/脚注）由默认的 Sätteri 处理器提供，无需 remark-gfm。
    // rehype-slug：给标题补稳定 id；rehype-autolink-headings：生成可点击的锚点链接。
    rehypePlugins: [
      rehypeSlug,
      [rehypeAutolinkHeadings, { behavior: 'append', properties: { className: ['heading-anchor'], ariaLabel: '本节链接' } }],
    ],
  },
});
