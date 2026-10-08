# 项目决策记录 (DECISIONS)

本文档记录博客项目中的长期设计、文风规范与工程决策，避免口头或单次会话中的意图丢失。

---

## D-019: 博客全站文案去 AI 味与文风基调规范 (oil-tone)

- **日期**：2026-10-08
- **状态**：已采纳并实施 (Accepted & Implemented)
- **参考依据**：[oil-oil/oil-tone 规范](https://github.com/oil-oil/oil-tone)

### 背景与问题
博客原有文案（首页、关于页、项目卡片等）存在较明显的 AI 自动生成痕迹：
1. **标签式排列与模板腔**：例如“关键词：Java、算法、Agent应用”、“不止是代码，还有生活和音乐”。
2. **生硬的四字/格言感标题**：如“把想法做成能用的东西”、“把过程写清楚”、“代码之外，也有日常”。
3. **宏大包装与空泛动词**：项目描述偏向功能套话与空洞升华，缺乏真实的开发动机与动手细节。
4. **口吻疏离**：自我介绍偏向公关式简介，缺乏真实开发者的生活感与随性感。

### 决策内容
1. **核心文风定位**：采用**「平实且带有一点生活随性感的开发者自白」**。
   - 设定身份：软件工程大三学生、平时主要写 Java / 算法、用 AI 结对写小工具、喜欢 Yorushika 和动画。
   - 叙述原则：直白讲清楚“为什么写这个工具”、“是怎么做的”、“踩了什么坑”，删除无材料支持的哲学升华和模板化标语。
   - 语言节奏：遵循 `oil-tone` 规范，保留符合自然语序的“的/了”等虚词，句子读起来朗朗上口，贴近真实口语化书面语。
2. **实施范围**：
   - [index.astro](file:///e:/blog/astro-blog/src/pages/index.astro)：首页 Hero 介绍、各板块章节标题、音乐与日常卡片文案。
   - [about.astro](file:///e:/blog/astro-blog/src/pages/about.astro)：关于我页面导语、个人经历展开（算法底层、后端工程、AI 协作方式）、底部交流与求职自白。
   - [projects.astro](file:///e:/blog/astro-blog/src/pages/projects.astro) & [selected-projects.ts](file:///e:/blog/astro-blog/src/data/selected-projects.ts)：ELMA、Three Body Lab、minialloc、Card PDF 等项目的标题、说明与开发细节。
   - [music.astro](file:///e:/blog/astro-blog/src/pages/music.astro) & [blog/index.astro](file:///e:/blog/astro-blog/src/pages/blog/index.astro)：音乐页与文章列表的导引文案。
   - [src/content/projects/*.md](file:///e:/blog/astro-blog/src/content/projects/)：各项目对应 Markdown 文档的概要与描述。


---

## D-020: 文章正文排版与代码块高质感美化 (Direction C)

- **日期**：2026-10-08
- **状态**：已采纳并实施 (Accepted & Implemented)

### 背景与问题
博客正文排版与代码块较为素淡单调：
1. 代码块缺乏容器层次与精致度，顶部栏仅有朴素的语言文字与复制按钮。
2. 用户期望 Mac 窗口风格的红黄绿三色微圆点（`.code-block-dots`）以增加界面工艺感。
3. Astro 5 / 7 Content Layer 会将 Markdown 产物缓存在 `node_modules/.astro/data-store.json`，若仅修改 rehype 插件而不清空该缓存，Astro build 会直接复用旧 HTML，导致插件改动不生效。

### 决策内容
1. **代码块视觉工艺**：
   - 在 rehype 编译管线 ([scripts/rehype-code-block.mjs](file:///e:/blog/astro-blog/scripts/rehype-code-block.mjs)) 中，向每个 `<div class="code-block-bar">` 静态注入 `<div class="code-block-dots"><span class="dot dot-red"></span><span class="dot dot-yellow"></span><span class="dot dot-green"></span></div>`。
   - 在客户端组件 ([src/components/common/CodeCopy.astro](file:///e:/blog/astro-blog/src/components/common/CodeCopy.astro)) 中配置深色模式（#ff5f56, #ffbd2e, #27c93f）圆点样式及尺寸约束（9px），并保留客户端动态检测兜底。
   - 增加微弱外发光阴影与内边框，提升暗黑风格代码块的质感与立体感。
2. **正文微交互升级**：
   - 顶部阅读进度条增加翡翠流光渐变（`reading-progress`）。
   - 日系信笺感 `blockquote` 与立体图片框线。
   - 搜索按钮增加 `<kbd>⌘K</kbd>` 快捷键标识。
3. **构建缓存清理规范**：
   - 若修改了 Markdown 预处理/rehype/remark 插件，需清除 `node_modules/.astro` 缓存以触发重新编译。
