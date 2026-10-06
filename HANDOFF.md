# 博客交接文档

更新日期：2026-10-06

## 1. 当前状态

- 生产分支：`main`
- 技术栈：Astro 静态站、Node.js 24、Nginx
- CI/CD：GitHub Actions `Build and deploy blog`
- 服务器站点入口：`/var/www/blog`
- Release 目录：`/var/www/.blog-releases/`
- 首次迁移备份：`/var/www/.blog-backups/`
- GitHub Pages：不参与生产发布，生产站由自有服务器提供
- 当前媒体基线：94 个 MP3、94 个 LRC、7 张专辑封面

`/var/www/blog` 是指向当前 Release 的符号链接。发布时先在旁边准备完整版本，验证后再原子替换链接，不直接清空线上目录。

## 2. 仓库结构

| 路径 | 用途 |
| --- | --- |
| `src/content/blog` | 正式博客文章 Markdown |
| `src/content/projects` | 项目条目 |
| `src/content/notes` | 首页短笔记 |
| `src/components` | Astro 页面和组件 |
| `src/data/music.ts` | 专辑与歌曲索引 |
| `public/media` | 歌词、封面、图片等静态资源 |
| `.github/workflows/blog.yml` | 构建与生产部署工作流 |
| `scripts/deploy-release.sh` | 服务器原子发布脚本 |
| `scripts/test-deploy-release.sh` | Release 切换测试 |
| `OPERATIONS.md` | 完整开发、构建和日常运维手册 |

新增文章时复制现有 Markdown 的 frontmatter 格式。`draft: true` 的文章不会进入正式列表、详情页和 RSS；正式发布时删除该字段或改为 `false`。

## 3. 本地开发与验证

项目要求 Node.js 22.19 或更高版本，CI 使用 `.node-version` 指定的 Node.js 24。

```bash
npm ci
npm run dev
```

提交前至少执行：

```bash
npm run build
```

构建脚本会先运行 `astro check`，再生成 `dist`。不要直接编辑 `dist`、`.astro` 或 `node_modules`。

## 4. GitHub Actions 配置

工作流行为：

- Pull Request 到 `main`：只检查和构建。
- 推送到 `main`：检查、构建、打包；部署开关开启时发布到生产服务器。
- `workflow_dispatch`：允许从 Actions 页面手动运行。
- 并发发布不会互相取消或同时切换站点。

`production` Environment 需要以下 Secrets：

```text
SERVER_HOST
SERVER_PORT
SERVER_USER
SERVER_SSH_KEY
SERVER_KNOWN_HOSTS
```

`production` Environment Variables：

```text
DEPLOY_PATH=/var/www/blog
SITE_URL=https://elma-gohan.xyz
```

Repository Variable：

```text
DEPLOY_ENABLED=true
```

不要把 Secret 值写进仓库、Issue、Actions 日志或交接文档。`SERVER_SSH_KEY` 是 Actions 专用私钥；对应公钥位于服务器部署用户的 `~/.ssh/authorized_keys`。`SERVER_KNOWN_HOSTS` 用于固定服务器身份，不是登录凭据。

## 5. MP3 的特殊边界

MP3 通过 `.gitignore` 排除，不在 Git 和 Actions 构建产物中。服务器当前站点是 MP3 的发布来源。

部署脚本会：

1. 解压新构建到 Staging。
2. 从当前 `/var/www/blog/media/music` 继承所有 MP3。
3. 确认 `index.html` 存在。
4. 确认至少继承到一个 MP3。
5. 激活新 Release。

如果当前站点没有 MP3，部署会失败并保留旧版本。音乐库必须有独立备份；Git 仓库不能作为 MP3 灾难恢复来源。

发布后检查数量：

```bash
find /var/www/blog/media/music -type f -name '*.mp3' | wc -l
find /var/www/blog/media/music -type f -name '*.lrc' | wc -l
```

当前两项预期均为 `94`。

## 6. 发布验收

GitHub Actions 变绿以后仍需确认：

```bash
readlink -f /var/www/blog
cat /var/www/blog/RELEASE
find /var/www/blog/media/music -type f -name '*.mp3' | wc -l
nginx -t
```

HTTP 验收：

- 首页和文章页返回 200。
- 音乐文件普通请求可访问。
- 带 `Range: bytes=0-1023` 的 MP3 请求返回 206。
- 首页、音乐页、文章页和移动端布局可正常使用。

若 `curl http://127.0.0.1/` 返回 404，但公网正常，先检查 Nginx 是否按 Host 匹配虚拟主机。测试时应带生产 Host，而不是直接把 localhost 结果当成发布失败。

## 7. 回滚

每个新 Release 的 `PREVIOUS_RELEASE` 记录上一目标。回滚前先读取并核对它：

```bash
current=$(readlink -f /var/www/blog)
previous=$(cat "$current/PREVIOUS_RELEASE")
printf 'current=%s\nprevious=%s\n' "$current" "$previous"
test -d "$previous"
```

确认 `previous` 是 `/var/www/.blog-releases/` 中的预期版本后，再切换：

```bash
ln -s "$previous" /var/www/.blog.rollback-next
mv -Tf /var/www/.blog.rollback-next /var/www/blog
nginx -t
```

随后重新检查首页、文章、MP3 数量和 Range 请求。首次自动迁移前的实体目录备份位于 `/var/www/.blog-backups/`，它不一定带有 `RELEASE` 文件。

## 8. 已知风险与后续工作

1. 当前部署使用的服务器账户权限较高。应迁移到只拥有博客发布目录权限的专用用户。
2. 服务器密码曾进入不安全的对话渠道，应确认已经轮换；Actions 密钥登录不受密码轮换影响。
3. MP3 不在 Git 中，应建立至少一份异机或对象存储备份。
4. Release 和首次迁移备份不会自动清理。确认回滚窗口后再制定保留策略，不要直接递归删除未核对的目录。
5. 如果只删除旧 `master` 而不重写历史，旧博客仍存在于 Git 历史提交中。只有涉及敏感信息时才考虑历史重写。
6. 正式站使用 `https://elma-gohan.xyz`；`www.elma-gohan.xyz` 永久重定向到根域名。证书由 Certbot 自动续期，发布后应继续执行 HTTPS 健康检查。

## 9. 故障定位顺序

1. 查看 Actions 的 Build Job，确认依赖安装、Astro 检查和构建是否成功。
2. 查看 Deploy Job，区分 SSH、known_hosts、目录权限和健康检查错误。
3. 在服务器核对 `/var/www/blog` 指向的 Release 与 `RELEASE` 文件。
4. 核对 MP3/LRC 数量和 Nginx 配置。
5. 从公网请求首页和 MP3 Range；不要只检查服务器本机 localhost。
6. 无法快速恢复时，按上一节回滚，不在当前 Release 中边改边救。

更详细的文章维护、播放器检查和手动发布边界见 `OPERATIONS.md`。
