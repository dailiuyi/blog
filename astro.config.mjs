import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  site: 'https://elma-gohan.xyz',
  output: 'static',
  // /prototype/ 是设计实验区，不进 sitemap；正式路由、标签页与归档页保留。
  integrations: [sitemap({ filter: (page) => !new URL(page).pathname.startsWith('/prototype/') })],
  markdown: {
    shikiConfig: { theme: 'github-dark-default', wrap: true },
  },
});
