const elmaProject = {
  id: 'elma',
  name: 'ELMA',
  subtitle: '今天吃什么',
  description: '给出距离、预算和口味，从附近的餐厅里选一家。不满意，就在这一轮候选里换一家。',
  detail: '把一次推荐的候选和依据保存下来，让选择可以复盘。',
  tech: ['Java', 'Spring Boot', 'Vue'],
  article: '/blog/elma-low-regret/',
  repo: 'https://github.com/dailiuyi/elma-gohan',
  source: 'https://github.com/dailiuyi/elma-gohan/blob/bbc45f1e5972facb8beb790e9ebb94efbc08c587/docs/images/readme/product-demo.gif',
} as const;

const threeBodyProject = {
  id: 'threebody',
  name: 'Three Body Lab',
  subtitle: '三体参数实验室',
  description: '调整天体的质量、位置与速度，观察轨迹，再用数值诊断复盘一次实验。',
  detail: '从纯 Java 计算内核，到浏览器里的实时模拟与历史回放。',
  tech: ['Java', 'WebSocket', 'Canvas'],
  article: '/blog/threebody-realtime-pipeline/',
  repo: 'https://github.com/dailiuyi/ThreeBodySimulation',
  source: 'https://github.com/dailiuyi/ThreeBodySimulation/tree/444c21154677bd01a3d483d051fc051398bd8b00/screenshots/2026-08-14',
  live: 'https://threebody.elma-gohan.xyz/',
} as const;

const miniAllocProject = {
  id: 'minialloc',
  name: 'minialloc',
  subtitle: '在 8KB 里理解内存',
  description: '从一个小型堆分配器开始，理解内存块怎样分配、分裂、释放与合并。',
  detail: '记录 C 指针、内存对齐与边界测试中的具体问题。',
  tech: ['C', 'Memory', 'Testing'],
  article: '/blog/minimalloc-from-scratch/',
  repo: 'https://github.com/dailiuyi/minialloc',
  source: 'https://github.com/dailiuyi/minialloc/blob/79afe6a16ed41e92116a896eff55f27f1c1f0e39/minimalloc.c',
} as const;

const cardPdfProject = {
  id: 'cardpdf',
  name: 'Card PDF Studio',
  subtitle: '按真实尺寸打印卡牌',
  description: '选好图片，填上卡片尺寸和页边距，生成一份能按真实物理尺寸打印的 A4 卡牌 PDF。',
  detail: '尺寸从头到尾以毫米为准，像素只出现在渲染边界；原图比例不符时等比缩放并居中留白。',
  tech: ['Python', 'Pillow', 'Tkinter'],
  article: '/blog/tcg-card-pdf-layout/',
  repo: 'https://github.com/dailiuyi/card-pdf-studio',
  source: '/blog/tcg-card-pdf-layout/',
} as const;

/** Showcase entries on the home page and /about/ (three projects). */
export const selectedProjects = [elmaProject, threeBodyProject, miniAllocProject] as const;

/** Showcase entries on /projects/, where Card PDF Studio is promoted from the tools list. */
export const projectPageProjects = [...selectedProjects, cardPdfProject] as const;

export type ProjectId = typeof projectPageProjects[number]['id'];
export type ProjectCard = typeof projectPageProjects[number];
