import type { APIRoute } from 'astro';
import { getCollection } from 'astro:content';
import { toPlainText } from '../lib/site-search';
import { buildTagSlugMap } from '../lib/tag-slug';

// 正文只保留前若干字符：索引体积与"能不能搜到"之间的折中。
const bodyBudget = 900;

export const GET: APIRoute = async () => {
  const posts = (await getCollection('blog', ({ data }) => !data.draft)).sort(
    (a, b) => b.data.published.valueOf() - a.data.published.valueOf(),
  );
  const tags = [...new Set(posts.flatMap((post) => post.data.tags))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
  const slugByTag = buildTagSlugMap(tags);

  const entries = [
    ...posts.map((post) => ({
      title: post.data.title,
      description: post.data.description,
      href: `/blog/${post.id}/`,
      meta: `${post.data.category} · ${post.data.tags.join(' ')}`,
      text: toPlainText(post.body ?? '').slice(0, bodyBudget),
    })),
    ...tags.map((tag) => ({
      title: `#${tag}`,
      description: `与「${tag}」有关的文章`,
      href: `/tags/${slugByTag.get(tag)}/`,
      meta: 'Tag',
      text: `标签 ${tag}`,
    })),
    { title: 'Projects', description: '正在构建的项目与实验', href: '/projects/', meta: 'Page', text: '项目作品源码构建记录实验' },
    { title: 'Music', description: 'Yorushika、n-buna、Vocaloid 与听歌记录', href: '/music/', meta: 'Page', text: '音乐专辑歌词播放 Yorushika n-buna Vocaloid' },
    { title: 'About', description: '关于这个数字空间和它的主人', href: '/about/', meta: 'Page', text: '关于 nabunana Java 后端 算法 AI 协作' },
    { title: 'Archive', description: '按年份浏览全部文章', href: '/archive/', meta: 'Page', text: '归档 年份 时间线 全部文章' },
    { title: 'Tags', description: '按标签浏览文章', href: '/tags/', meta: 'Page', text: '标签 主题 分类 文章' },
  ];

  return new Response(JSON.stringify({ generated: new Date().toISOString(), entries }), {
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });
};
