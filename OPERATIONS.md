# nabunana 博客编译与维护手册

这份文档用于维护本仓库根目录中的 Astro 静态博客（本机工作副本为 `/home/innovation/文档/Code/blog`；旧文档里的 `E:\blog\astro-blog` 已不再使用）。博客文章、页面前端、音乐播放器和静态资源会由同一次构建生成，不需要再运行旧 Hexo 项目。

## 1. 环境与目录

推荐环境：

- Linux（生产与 CI）、macOS 或 WSL；本文示例统一使用 bash
- Node.js `>= 22.19`；`.node-version` 与 CI 固定为 `24`
- npm 10 或更高版本
- bash

仓库 `.node-version` 为 `24`，`package.json` 的 `engines.node` 为 `>=22.19.0`：22.19 及以上的 22.x 也能本地构建，CI 与生产发布使用 Node 24。生产环境是 Linux + Nginx（见第 8 节与 `HANDOFF.md`），本文不再假设 Windows 路径。

主要目录：

| 路径 | 用途 |
| --- | --- |
| `src/content/blog` | 正式博客文章，Markdown 格式 |
| `src/content/projects` | 项目条目 |
| `src/content/notes` | 首页短笔记 |
| `src/components` | 页面与播放器组件 |
| `src/data/music.ts` | 专辑、歌曲、歌词和播放索引数据 |
| `src/lib/environment.ts` | Summer / Night 环境状态与“星河”正式光影方案 |
| `public/media` | 音频、歌词、封面和图片等静态资源 |
| `dist` | 正式构建产物，可交给 Nginx 提供访问 |

`dist`、`.astro` 和 `node_modules` 都是生成目录，不要在里面直接修改文件。

## 2. 第一次安装

在仓库根目录打开终端：

```bash
cd <仓库根目录>
node --version
npm --version
npm ci
```

`npm ci` 会严格按照 `package-lock.json` 安装依赖。只有主动升级依赖时才使用 `npm install`。

## 3. 本地编辑与实时预览

进入项目并启动开发服务器：

```bash
cd <仓库根目录>
export ASTRO_TELEMETRY_DISABLED=1
npm run dev -- --host 127.0.0.1
```

浏览器打开：

- 首页：`http://127.0.0.1:4321/`
- 文章列表：`http://127.0.0.1:4321/blog/`
- 音乐页：`http://127.0.0.1:4321/music/`
- 光照实验：`http://127.0.0.1:4321/prototype/acg/lighting/`

开发服务器运行时，修改 `src` 或 `public` 中的文件通常会自动刷新。按 `Ctrl+C` 停止服务器。

如果 4321 端口被占用：

```bash
npm run dev -- --host 127.0.0.1 --port 4322
```

## 4. 新增或修改博客文章

在 `src/content/blog` 中创建 UTF-8 编码的 Markdown 文件。文件名会成为文章地址的一部分，建议只用小写英文、数字和连字符，例如：

```text
src/content/blog/my-new-post.md
```

文章模板：

```markdown
---
title: "文章标题"
description: "用于列表、搜索和分享卡片的简短摘要。"
published: 2026-08-20T18:00:00+08:00
updated: 2026-08-20T18:00:00+08:00
draft: true
category: Notes
tags: [Astro, Web]
featured: false
author: nabunana
readingTime: "6 min"
---

从这里开始写正文。
```

注意事项：

- `draft: true` 的文章不会进入正式首页、文章列表、详情页和 RSS。
- 准备公开时改为 `draft: false`，或者删除 `draft` 字段。
- `published` 和 `updated` 使用带时区的 ISO 时间。
- `tags` 必须是数组。
- 不要把旧 Hexo 文章目录重新复制进来；正式站只读取当前 Astro 内容集合。

保存后先在开发服务器中检查文章地址：

```text
http://127.0.0.1:4321/blog/my-new-post/
```

### 4.1 系列 / 连载（可选）

同一主题的多篇文章用两个可选字段串起来，读者能看到阅读顺序：

```yaml
series: "Three Body Lab"
seriesOrder: 2
```

- `series` 是系列名；`seriesOrder` 从 1 开始。
- 填写后会自动生成 `/series/` 总览与该系列的 `/series/<slug>/` 页面；文章页会显示「第 N 篇 / 共 M 篇」与上一篇/下一篇。
- **没填这两项的文章完全不受影响**，这是可选的。
- 未填 `seriesOrder` 的文章排在已编号文章之后（视为"新连载"），组内按发布时间排序。
- URL slug 由系列名生成：含 ASCII 的名字直接转换（`Three Body Lab` → `three-body-lab`）；纯中文名回退为 `series-<短哈希>`，稳定且不会因插入新系列而变号。

### 4.2 仓库卡片（可选）

正文里单独成段写一行指令，就会渲染成一张带链接的仓库卡片：

```text
::repo{repo="dailiuyi/minialloc" desc="从零实现的分配器" lang="C" stars="128"}
```

- 只有 `repo="owner/name"` 是必需的，其余属性可省略。
- `stars` 只显示你手填的数字，**不请求 GitHub API**（构建离线可复现）。省略时只显示「GitHub ↗」。
- 写法不合法时该段落会**原样显示**，不会生成坏链接，同时构建日志里会有一条 `[remark-repo-card]` 警告。
- 该指令必须**单独成段**；写在行内不会生效。

## 5. 音乐资源维护

音乐库的来源文件是：

```text
src/data/music.ts
```

每首歌曲需要对应的音频和歌词：

```text
public/media/music/<album-slug>/01.mp3
public/media/music/<album-slug>/01.lrc
```

专辑封面放在：

```text
public/media/music/covers/<album-slug>.webp
```

维护规则：

- 音频继续使用 160kbps MP3。
- 歌词使用 UTF-8 LRC。
- 文件编号使用两位数字，并与 `music.ts` 中的曲目顺序一致。
- 新专辑追加到现有专辑之后，避免破坏浏览器保存的歌曲索引。
- 每次改动后检查专辑数、歌曲数、MP3 数和 LRC 数是否一致。

## 6. 类型检查与正式构建

只检查 Astro、内容结构和 TypeScript：

```bash
cd <仓库根目录>
export ASTRO_TELEMETRY_DISABLED=1
npm run check
```

生成正式网站：

```bash
cd <仓库根目录>
export ASTRO_TELEMETRY_DISABLED=1
npm run build
```

`npm run build` 会先执行 `astro check`，检查通过后再生成静态网站。成功时应看到：

```text
0 errors
0 warnings
dist/
```

最终需要发布的是整个 `dist` 目录的内容，而不是 `src`、`public` 或项目根目录。

## 7. 检查正式构建结果

构建完成后启动生产预览：

```bash
cd <仓库根目录>
npm run preview -- --host 127.0.0.1
```

再次打开：

```text
http://127.0.0.1:4321/
```

正式预览不会像开发服务器一样自动重新构建。修改源码后需要重新执行：

```bash
npm run build
```

基础产物检查：

```bash
find dist -type f -name '*.html' | wc -l
find dist/media/music -type f -name '*.mp3' | wc -l
find dist/media/music -type f -name '*.lrc' | wc -l
```

当前基线应为：

- 21 个 HTML 页面
- 94 个 MP3
- 94 个 LRC
- 7 张专辑封面
- 3 张首页动画宣传图

以上是上一次构建的产物基线；新增或删除路由后，HTML 页面数需要重新统计。MP3 不入 Git（见第 8 节），只在服务器和本地 `public/media/music` 中保存。

还需要人工检查：

1. 首页 3D 唱片廊的拖拽、方向键浏览和点击封面播放。
2. 左下角播放器的展开、快速收回和“固定 / 已固定”按钮。
3. 日文原文与中文译文是否正确配对。
4. 七个专辑折叠组是否能播放正确歌曲。
5. 播放中切换首页、音乐页和文章页是否保持进度。
6. 375px 手机宽度下是否存在遮挡或横向溢出。
7. 检查“星河”光影的 Summer / Night 两种状态，以及首页、音乐页和文章页的实际效果。
8. 首次访问是否跟随系统亮暗主题；手动切换后刷新和跨页是否保持 `nabunana:environment-v1`。
9. 光影持续观察至少 30 秒，确认只有极慢呼吸和轻微视差，正文对比度不受影响；减少动画设置下应完全静止。

环境光不使用 Three.js 或 WebGL，也不应出现大于 500KB 的光影专用 JavaScript chunk。正式方案固定为“星河”，右下角只保留 Summer / Night 控件，不显示诗句。

可直接检查指定组合：

```text
http://127.0.0.1:4321/?lightingPreset=silver-river&environment=night&lightingPreview=1
http://127.0.0.1:4321/music/?lightingPreset=silver-river&environment=summer&lightingPreview=1
```

## 8. GitHub Actions 自动发布

`main` 分支是唯一生产分支。Pull Request 只执行检查与构建；推送或手动运行
`Build and deploy blog` 工作流时，Actions 会构建静态站、通过 SSH 上传，并在服务器上
原子切换 `/var/www/blog`。GitHub Pages 不参与发布。

先在 GitHub 仓库的 `Settings → Environments` 创建 `production` 环境，再设置以下 Secrets：

| 名称 | 内容 |
| --- | --- |
| `SERVER_HOST` | 服务器地址，例如 `39.108.101.149` |
| `SERVER_USER` | 有权写入站点父目录的 SSH 用户 |
| `SERVER_PORT` | SSH 端口；不设置时使用 `22` |
| `SERVER_SSH_KEY` | 专用于 Actions 的 SSH 私钥全文 |
| `SERVER_KNOWN_HOSTS` | `ssh-keyscan -H <服务器地址>` 的输出；应在可信终端核对指纹 |

服务器准备完成后，在仓库 `Settings → Secrets and variables → Actions → Variables` 中新增
Repository Variable `DEPLOY_ENABLED=true`。没有这个开关时，工作流只构建和测试，不会连接服务器；
这样首次推送 `main` 时不会因为凭据尚未配置而误发布。

可选的 Environment Variables：

| 名称 | 默认值 | 用途 |
| --- | --- | --- |
| `DEPLOY_PATH` | `/var/www/blog` | Nginx 站点根目录 |
| `SITE_URL` | `https://elma-gohan.xyz` | 发布后的 HTTPS 健康检查地址 |

MP3 不进入 Git 仓库。自动发布会从服务器当前版本继承 `media/music` 下的 MP3；如果
服务器完全没有 MP3，发布会停止且不会切换旧站。首次上线前应手动把音乐文件上传到
`/var/www/blog/media/music`。首次自动发布会把原有实体目录移到相邻的
`.blog-backups/legacy-<UTC 时间>`，以后每个版本位于 `.blog-releases/`，站点目录本身
是指向当前版本的符号链接。

如需回滚，在服务器上把 `/var/www/blog` 符号链接切换到上一版本；每个新版本中的
`PREVIOUS_RELEASE` 文件记录了上一个目标。工作流不会自动删除旧版本或首次发布备份。

## 9. 手动发布边界

本地构建成功不等于可以直接上线。当前流程要求：

1. 完成 `npm run build`。
2. 使用 `npm run preview` 人工验收。
3. 明确确认“验收通过”。
4. 在服务器创建 `/var/www/blog` 的完整回滚备份。
5. 上传新的 `dist` 产物并原子替换网站目录。
6. 检查 Nginx、页面、歌词、图片和音频 Range 请求。
7. 记录备份路径，异常时立即回滚。

不要在未备份时直接清空或覆盖 `/var/www/blog`。服务器密码也不要写入脚本、本文档或命令参数。

## 10. 常见问题

### `npm` 或 `node` 找不到

安装 Node.js 22.19 或更高版本（CI 与 `.node-version` 使用 24）后重新打开终端，再运行：

```bash
node --version
npm --version
```

### 依赖损坏或版本不一致

优先重新执行：

```bash
npm ci
```

不要手动修改 `node_modules`。

### 构建提示文章字段错误

检查文章顶部 `---` 之间的字段是否符合 `src/content.config.ts`。常见问题包括日期格式错误、`tags` 不是数组、项目状态不在允许值内。

### 修改后预览没有变化

开发模式确认使用的是 `npm run dev`。生产预览模式需要先停止服务、重新构建，再启动预览：

```bash
npm run build
npm run preview -- --host 127.0.0.1
```

### 音乐或歌词返回 404

检查 `src/data/music.ts` 中的 `slug`、曲目顺序和 `public/media/music` 下的文件编号是否完全一致。路径和文件名区分字符与全角/半角形式。

### 端口已被占用

改用其他端口：

```bash
npm run preview -- --host 127.0.0.1 --port 4322
```

## 11. 访问统计服务

正式页面通过同源 `/api/stats/view` 记录 PV；服务只监听 `127.0.0.1:8787`，SQLite 数据保存在
`/var/lib/nabunana-stats/stats.db`。数据库不在 `/var/www/blog` Release 中，静态站发布和回滚都不会覆盖它。

### 11.1 首次编译与安装

在 CI 下载 `stats-<commit>` Artifact，或在装有 Go 1.25 的可信机器编译：

```bash
cd stats
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -o nabunana-stats .
```

把仓库、二进制上传到服务器。若现有 Nginx `server {}` 已包含一个专用片段目录，把该目录作为第三个参数；
否则安装器会生成 `/etc/nginx/snippets/nabunana-stats.conf`，需要先把下列 include 放入博客的 `server {}`：

```nginx
include /etc/nginx/snippets/nabunana-stats.conf;
```

然后安装：

```bash
sudo bash scripts/install-stats-service.sh ./nabunana-stats http://39.108.101.149
sudo nginx -t
```

使用域名或 HTTPS 后，第二个参数必须与浏览器地址的 Origin 完全一致，并同步更新
`/etc/nabunana-stats.env` 中的 `STATS_ALLOWED_ORIGIN`。

### 11.2 历史 Nginx 日志回填

服务接收实时流量前先预览现存日志。命令支持普通文件和 `.gz` 轮转文件，只导入当前 Astro 正式 HTML 路由，
并报告爬虫、无效状态、未知路径和解析失败数量：

```bash
sudo -u nabunana-stats /opt/nabunana-stats/nabunana-stats import-nginx \
  --database /var/lib/nabunana-stats/stats.db \
  --site-root /var/www/blog \
  '/var/log/nginx/access.log*'
```

核对 JSON 预览后，用相同参数追加 `--apply`。数据库一旦导入过历史数据或收到过实时访问，程序会拒绝再次导入。
若 `nabunana-stats` 用户无权读取日志，应由管理员把日志复制到一个只读临时目录后再导入；不要放宽整个
`/var/log/nginx` 的权限。

导入成功后才激活服务和 Nginx 路由：

```bash
sudo systemctl enable --now nabunana-stats.service
sudo nginx -t
sudo systemctl reload nginx
curl --fail http://127.0.0.1:8787/api/stats/health
```

### 11.3 自动更新权限

首次安装会把受约束的更新辅助程序放在 `/usr/local/sbin/deploy-nabunana-stats`。为 Actions 的部署用户配置
只允许执行该程序的 `sudoers` 规则，不要授予通用 `systemctl`、`install` 或无密码 shell 权限。辅助程序只接受
`/tmp/blog-deploy-<run>-<attempt>/nabunana-stats-linux-{amd64,arm64}`，更新后检查健康端点，失败时恢复上一二进制。

### 11.4 状态、备份与故障定位

```bash
systemctl status nabunana-stats.service
journalctl -u nabunana-stats.service --since today
curl --fail http://127.0.0.1:8787/api/stats/health
sudo -u nabunana-stats sqlite3 /var/lib/nabunana-stats/stats.db 'PRAGMA integrity_check;'
```

备份前使用 SQLite 在线备份命令，避免只复制 WAL 模式下的主文件：

```bash
sudo -u nabunana-stats sqlite3 /var/lib/nabunana-stats/stats.db \
  ".backup '/var/lib/nabunana-stats/stats-backup-$(date -u +%Y%m%dT%H%M%SZ).db'"
```

页面显示 `—` 时依次检查公网 `/api/stats/health`、Nginx location、systemd 日志、
`STATS_ALLOWED_ORIGIN` 和数据库目录权限。429 表示同一来源超过每秒一个请求、最多突发 20 次的内存限流。
