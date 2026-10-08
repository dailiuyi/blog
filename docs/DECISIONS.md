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

### 验证结果
- 全站通过 `npm run fonts && astro check && astro build` 编译，73 个静态路由生成正常，无 TypeScript / Astro 语法错误。
