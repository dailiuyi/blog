import type { APIRoute } from 'astro';
import { getCollection } from 'astro:content';

/**
 * 每篇文章的分享图，沿用 og-default.svg 的"星河 + 波纹"视觉语言。
 *
 * 边界说明：这里输出 SVG，中文标题依赖读取方系统里可用的中日文字体；
 * 不内嵌 LXGW 子集（660KB 的字体 base64 会让每张分享图膨胀到约 900KB）。
 * 如果以后需要保证任何平台都能渲染中文，再考虑改为内嵌字体或预渲染 PNG。
 */

const escapeXml = (value: string) =>
  value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&apos;');

// 按中文字形宽度粗略换行：CJK 记 1 格，其余记 0.55 格，每行 14 格。
function wrapTitle(title: string, perLine = 14, maxLines = 3): string[] {
  const lines: string[] = [];
  let current = '';
  let width = 0;
  for (const char of [...title]) {
    const cost = /[\u2e80-\u9fff\uf900-\ufaff]/.test(char) ? 1 : 0.55;
    if (width + cost > perLine && current) {
      lines.push(current);
      current = char;
      width = cost;
    } else {
      current += char;
      width += cost;
    }
  }
  if (current) lines.push(current);
  if (lines.length <= maxLines) return lines;
  const kept = lines.slice(0, maxLines);
  kept[maxLines - 1] = `${kept[maxLines - 1].slice(0, Math.max(1, perLine - 1))}…`;
  return kept;
}

const palette = { paper: '#F5FBFA', ink: '#102E2D', muted: '#64817F', accent: '#216E69', wave1: '#3DAFA5', wave2: '#83C9D7' };

function render(title: string, meta: string): string {
  const lines = wrapTitle(title);
  const startY = lines.length === 1 ? 300 : lines.length === 2 ? 262 : 232;
  const titleMarkup = lines
    .map((line, index) => `<text x="78" y="${startY + index * 96}" font-family="'PingFang SC','Hiragino Sans GB','Noto Sans SC','Source Han Sans SC','Microsoft YaHei',system-ui,sans-serif" font-size="76" font-weight="600" fill="${palette.ink}">${escapeXml(line)}</text>`)
    .join('');

  return `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" role="img" aria-label="${escapeXml(title)}"><title>${escapeXml(title)}</title><rect width="1200" height="630" fill="${palette.paper}"/><circle cx="1010" cy="135" r="86" fill="none" stroke="${palette.wave1}" stroke-opacity=".35"/><path d="M70 512c150-90 252 70 402 0 144-67 255-104 384-18 101 67 187 13 284-32" fill="none" stroke="${palette.wave1}" stroke-width="2" stroke-opacity=".55"/><path d="M70 539c167-52 268 54 426 4 148-47 239-31 352 12 91 35 182 4 292-50" fill="none" stroke="${palette.wave2}" stroke-width="2" stroke-opacity=".35"/><text x="80" y="118" font-family="monospace" font-size="20" letter-spacing="4" fill="${palette.accent}">NABUNANA / WRITING</text>${titleMarkup}<text x="84" y="596" font-family="monospace" font-size="18" letter-spacing="3" fill="${palette.muted}">${escapeXml(meta)}</text></svg>`;
}

export const GET: APIRoute = async ({ params }) => {
  const posts = await getCollection('blog', ({ data }) => !data.draft);
  const post = posts.find((entry) => entry.id === params.slug);
  if (!post) return new Response('Not found', { status: 404 });

  const meta = `${post.data.category.toUpperCase()} · ${post.data.published.toISOString().slice(0, 10)} · elma-gohan.xyz`;
  return new Response(render(post.data.title, meta), {
    headers: { 'Content-Type': 'image/svg+xml; charset=utf-8' },
  });
};

export async function getStaticPaths() {
  const posts = await getCollection('blog', ({ data }) => !data.draft);
  return posts.map((post) => ({ params: { slug: post.id }, props: {} }));
}
