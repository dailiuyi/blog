# blog · nabunana-digital-space

基于 [Astro](https://astro.build) 的个人博客，域名为 [elma-gohan.xyz](https://elma-gohan.xyz) 的内容站点。

## 功能

- Markdown 渲染管线：基于 `@astrojs/markdown-remark`，启用 rehype 标题锚点（`rehype-slug` + `rehype-autolink-headings`）与自定义代码块插件（标题锚点、代码块复制、系列连载与仓库卡片）
- 内容发现：标签、归档、404 页与全文搜索
- 内容管理：`src/content` 使用 Astro Content Collections，`src/content.config.ts` 定义类型校验
- 字体子集化：`scripts/subset-fonts.mjs` 按实际用字裁剪中文字体，减小加载体积
- 访问统计：`stats/` 内置 Go 编写的统计服务，附带 systemd 安装脚本（`scripts/install-stats-service.sh`）
- 工程化：CI 校验内容资源与分享图（`scripts/check-content-assets.mjs`）、Python 验收测试（`tests/`）、Dependabot 依赖更新

## 常用命令

```bash
npm run dev      # 本地开发
npm run build    # 字体子集化 + astro check + 构建
npm run preview  # 本地预览构建产物
npm run fonts    # 仅执行字体子集化
```

要求 Node.js >= 22.19.0。

## 目录结构

```
src/          # 页面、组件、布局、样式与内容集合
public/       # 静态资源（媒体、字体、favicon、OG 图）
scripts/      # 构建、部署与验收脚本
stats/        # Go 访问统计服务
tests/        # Python 验收测试
docs/         # 验收流程文档
DESIGN_LANGUAGE.md / MOTION_DESIGN.md / LITERARY_SPATIAL_LIGHTING.md  # 设计规范
OPERATIONS.md / WORKFLOW.md / HANDOFF.md                              # 运维与协作流程
```

## 部署

`scripts/deploy-release.sh` 与 `scripts/deploy-stats-release.sh` 负责站点与统计服务的发布，细节见 `OPERATIONS.md`。
