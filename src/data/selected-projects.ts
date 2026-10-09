const elmaProject = {
  id: 'elma',
  name: 'ELMA',
  subtitle: '今天吃什么',
  description: '每天中午不知道吃什么时写的。输入预算和距离，在附近随机挑一家，还能换同轮备选。最重要的是把每次的选择理由存下来，避免下次又纠结。',
  detail: '把一次推荐的候选和依据保存下来，让选择可以复盘。',
  tech: ['Java', 'Spring Boot', 'Vue'],
  article: '/blog/elma-low-regret/',
  repo: 'https://github.com/dailiuyi/elma-gohan',
} as const;

const threeBodyProject = {
  id: 'threebody',
  name: 'Three Body Lab',
  subtitle: '三体参数实验室',
  description: '为了看三体运动轨迹写的一个模拟器。Java 在后端算数值积分，通过 WebSocket 把实时坐标推给前端 Canvas 绘制轨道。',
  detail: '从纯 Java 计算内核，到浏览器里的实时模拟与历史回放。',
  tech: ['Java', 'WebSocket', 'Canvas'],
  article: '/blog/threebody-realtime-pipeline/',
  repo: 'https://github.com/dailiuyi/ThreeBodySimulation',
  live: 'https://threebody.elma-gohan.xyz/',
} as const;

const miniAllocProject = {
  id: 'minialloc',
  name: 'minialloc',
  subtitle: '在 8KB 里理解内存',
  description: '用 C 语言在一个 8KB 的静态数组里实现 malloc/free。手动处理内存对齐、空闲块链表分裂和合并，写完之后对指针和越界有了更深体会。',
  detail: '记录 C 指针、内存对齐与边界测试中的具体问题。',
  tech: ['C', 'Memory', 'Testing'],
  article: '/blog/minimalloc-from-scratch/',
  repo: 'https://github.com/dailiuyi/minialloc',
} as const;

const cardPdfProject = {
  id: 'cardpdf',
  name: 'Card PDF Studio',
  subtitle: '按真实尺寸打印卡牌',
  description: '自己想印 TCG 卡牌时折腾的桌面工具。严格以毫米计算真实物理尺寸，把多张卡片自动拼版到 A4 PDF 上，直接拿去打印店就能裁切。',
  detail: '尺寸从头到尾以毫米为准，像素只出现在渲染边界；原图比例不符时等比缩放并居中留白。',
  tech: ['Python', 'Pillow', 'Tkinter'],
  article: '/blog/tcg-card-pdf-layout/',
  repo: 'https://github.com/dailiuyi/card-pdf-studio',
} as const;

/** Showcase entries on the home page and /about/ (three projects). */
export const selectedProjects = [elmaProject, threeBodyProject, miniAllocProject] as const;

/** Showcase entries on /projects/, where Card PDF Studio is promoted from the tools list. */
export const projectPageProjects = [...selectedProjects, cardPdfProject] as const;

export type ProjectId = typeof projectPageProjects[number]['id'];
export type ProjectCard = typeof projectPageProjects[number];
