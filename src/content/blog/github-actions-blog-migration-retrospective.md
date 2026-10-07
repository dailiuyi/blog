---
title: "一次 GitHub Actions 自动发布复盘"
description: "从旧 Hexo GitHub Pages、Astro 新站到服务器:怎样处理分支切换、SSH 信任、未入库的 94 首音乐,以及可回滚的原子发布."
published: 2026-08-25T00:05:00+08:00
category: Engineering
tags: [Astro, GitHub Actions, Nginx, SSH, CI/CD, 部署]
featured: false
readingTime: "11 min"
series: "静态博客发布链路"
seriesOrder: 2
---

这次迁移的目标看起来很简单:让新 Astro 博客成为 `main`,不再发布旧博客,GitHub Pages 可以停掉,以后每次推送都由 GitHub Actions 发布到自己的服务器.

真正做完以后,我发现这里至少有四件不同的事:Git 分支要切换,GitHub 默认分支要切换,Pages 要停止,服务器发布链路也要重新建立.只完成其中一件,都会留下一个"看起来已经迁完"的半成品.

最终结果是:Astro 检查为 0 错误、0 警告,构建生成 21 个页面;Actions 完成 SSH 发布后,公网首页返回 200,播放器的 MP3 Range 请求返回 206,服务器上的 94 首音乐也全部保留.

## 建立 main 不等于迁移完成

仓库最初只有 `master`,里面是多年前生成的 Hexo 静态文件.新 Astro 站在另一个分支上,当前文件树已经删掉旧 HTML、主题资源和 Live2D 文件,只留下新的源码.

我从新站提交建立并推送了 `main`.但这时 GitHub 的默认分支仍然可能是 `master`,Pages 也可能继续从旧分支发布.于是还要在仓库设置中把默认分支改成 `main`,再把 Pages 的发布来源设为 `None`.自己的服务器成为唯一生产环境以后,不再需要同时维护两条发布路径.

这里还有一个容易混淆的边界:删除 `master` 只会删除分支引用,不会自动抹掉 Git 历史.因为新 `main` 继承了旧提交,过去的文件仍可从历史提交找到.我的要求只是"不再展示和维护旧文章",所以没有重写历史.如果目标是彻底清除敏感文件,则必须另做历史重写和强制推送,风险完全不同.

## 先让构建独立成功,再允许部署

工作流被拆成 `build` 和 `deploy` 两个 Job.

`build` 在 Pull Request、`main` 推送和手动运行时执行:

```text
checkout → Node.js → npm ci → astro check → astro build → 产物检查
```

`deploy` 只有在以下条件同时满足时才运行:

```text
分支是 main
不是 Pull Request
Repository Variable DEPLOY_ENABLED 等于 true
build 已成功
```

这个开关很有用.第一次推送 `main` 时,服务器密钥和环境变量尚未配置,工作流只完成构建,不会产生一条预料之中的部署失败.等服务器准备完成,再设置 `DEPLOY_ENABLED=true` 并手动运行一次.

生产凭据放在 GitHub 的 `production` Environment 中:

```text
SERVER_HOST
SERVER_PORT
SERVER_USER
SERVER_SSH_KEY
SERVER_KNOWN_HOSTS
```

普通配置放在 Variables 中:

```text
DEPLOY_PATH=/var/www/blog
SITE_URL=<站点地址>
```

Secrets、Environment Variables 和 Repository Variables 名字很像,但作用域不同.尤其是部署开关,它要在 Job 启动前参与条件判断,所以我把它放在仓库级 Variables,而不是生产环境内部.

## Node 的小版本也会让 CI 与本地分叉

依赖锁定后,项目实际使用的 Astro 版本要求 Node.js 至少为 22.12,部分间接依赖要求更高.本机最初是 Node 22.11,`npm ci` 已经开始提示 `EBADENGINE`.

这类提示如果被忽略,很容易出现"本地勉强能装、CI 突然不能跑".最后我增加了 `.node-version`,让 Actions 使用 Node 24,并在 `package.json` 中声明:

```json
{
  "engines": {
    "node": ">=22.19.0"
  }
}
```

版本约束不是装饰.它把失败提前到安装阶段,而不是等某个构建器或原生依赖以更难理解的方式崩掉.

## 最大的发布陷阱是 Git 里没有 MP3

博客音乐库有 94 首 MP3,但这些大文件被 `.gitignore` 排除,不会出现在 GitHub Runner 的 checkout 中.Actions 构建出的 `dist` 只有页面、歌词、封面和其他静态资源.

如果把这个 `dist` 当作"完整真相"覆盖服务器,新页面会成功上线,94 首音乐却会全部消失.构建本身仍然是绿色的,问题只会在播放器请求时出现.

因此发布脚本在切换前做了额外处理:

```text
解压新构建到 staging
从当前线上版本继承所有 MP3
检查 index.html
确认继承到的 MP3 数量不为 0
再激活新版本
```

如果服务器上一个 MP3 都没有,脚本直接失败,不碰当前站点.这个策略承认了一个事实:代码仓库和媒体库是两个数据源.既然暂时没有把音频迁到对象存储或制品仓库,部署系统就不能假装 Git 包含完整站点.

## SSH 有三种东西,不要混在一起

第一次配置 Actions SSH 时,最容易混淆的是私钥、公钥和服务器 Host Key.

```text
Actions 私钥       放进 SERVER_SSH_KEY,用来证明"我是谁"
对应公钥           放进服务器 authorized_keys,允许这把私钥登录
服务器 Host Key    放进 SERVER_KNOWN_HOSTS,证明"服务器是谁"
```

`SERVER_KNOWN_HOSTS` 不是服务器密码,也不是 Actions 公钥.它用于严格校验服务器身份,避免自动化在网络被劫持时把发布包交给另一台机器.生成记录后,还应通过云厂商控制台或其他可信通道核对 ED25519 指纹.

私钥从不打印到日志,也不写进仓库.服务器密码同样不应该出现在聊天、命令参数或文档里.我在配置过程中曾把密码直接发进对话窗口,这已经等同于凭据泄露;密钥登录打通后,正确收尾是立即轮换密码,而不是因为"只有自己看见"就继续使用.

命令复制也踩过一次小坑.Markdown 中的转义符被一起复制,`root@host` 变成了 `root\@host`,`~/.ssh/authorized_keys` 也多出反斜杠.PowerShell 出现连续的 `>>` 时,不代表 SSH 很慢,而是引号或管道没有闭合,Shell 仍在等待后续输入.此时先按 `Ctrl+C` 回到正常提示符,再复制不带提示符和转义字符的命令.SSH 密码输入时不显示字符和星号则是正常行为.

## 发布目录只在最后一刻切换

服务器不直接清空 `/var/www/blog`.每次发布使用提交 SHA 和运行次数组成版本号,把站点展开到相邻的 Releases 目录:

```text
/var/www/.blog-releases/<commit>-<attempt>/
```

第一轮自动发布时,原来的实体目录先移动到:

```text
/var/www/.blog-backups/legacy-<UTC 时间>/
```

新版本完整准备好以后,再用符号链接替换 `/var/www/blog`.后续发布只需要原子替换这个链接.每个版本还会记录 `RELEASE` 和 `PREVIOUS_RELEASE`,出问题时可以知道当前版本和上一目标,而不是临时猜哪个目录才是好的.

工作流使用并发锁,新的生产发布不会取消正在切换的旧发布.静态站发布很快,没有必要为了省几十秒,让两个进程同时操作同一个站点链接.

## 绿色 Actions 以后还要检查服务器

最终验收没有停在"Workflow success".我检查了四层状态:

1. Actions 的 Build 和 Deploy Job 都成功.
2. `/var/www/blog` 已经指向这次提交对应的 Release.
3. Release 中仍有 94 个 MP3,并且首次发布备份存在.
4. 公网首页返回 200,带 `Range: bytes=0-1023` 的 MP3 请求返回 206.

Nginx 在本机访问 `127.0.0.1` 时曾返回 404,而公网地址正常返回 200.原因是虚拟主机按 `Host` 匹配;给本地请求带上正确的 Host 后同样返回 200.单看一次 localhost 请求,很容易把正常的站点路由误判成发布失败.

`206 Partial Content` 也不能省.音乐文件普通 GET 返回 200,只能证明整文件可以下载;播放器拖动进度、续播和浏览器按区间加载依赖 Range 请求.

## 这次迁移留下的规则

最后,我把经验压缩成下面几条:

- 分支创建、默认分支、Pages 来源和生产服务器是四个独立状态,分别确认.
- 构建与部署分开,首次上线前给部署加显式开关.
- CI 的 Node 版本写进仓库,不依赖 Runner 或本机"碰巧可用".
- 被 Git 忽略的大文件也是生产数据,发布脚本必须明确它们从哪里来.
- SSH 私钥验证客户端,Host Key 验证服务器,两者缺一不可.
- 发布包在旁边准备完整,验证后再原子切换,首次迁移保留旧目录备份.
- Actions 变绿只是开始,最终还要核对 Release、资源数量、HTTP 200 和 Range 206.
- 密码一旦进入聊天或日志就按泄露处理,立即轮换,不讨论"应该没人看到".

这套流程没有引入容器、Kubernetes 或复杂的部署平台.对一个 Astro 个人站来说,GitHub Actions、SSH、Release 目录、符号链接和一份可恢复备份已经足够.复杂度应该用在真实风险上:半包发布、资源丢失、身份冒充和无法回滚,而不是为了让部署看起来更像一个大系统.
